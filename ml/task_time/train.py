"""Model 2 — task time estimation (docs/models.md §2): training CLI + reusable helpers.

Time split by task_date (1-based days): train 1-70, validation 71-80, test 81-90. Only
completed tasks are used; future-day (`status='scheduled'`) rows are excluded from training.

Pipeline:
1. standard p50 LightGBM quantile model on STANDARD_FEATURES (log1p target, early stop 50).
2. out-of-fold standard_min for train+val rows: GroupKFold(5) grouped by ISO week, same
   params with n_estimators frozen to step 1's best iteration. Test and future tasks use
   the step-1 model for their standard_min.
3. operator_avg_time_ratio_30d recomputed against those standard_min values.
4. personal p10/p50/p90 quantile models on PERSONAL_FEATURES; enforce p10 <= p50 <= p90.
5. if validation p10-p90 coverage falls outside 75-85%: split-conformal (CQR) widening
   calibrated on validation; the offset is saved and applied to every interval.
6. test metrics: MAE/MAPE on p50 (personal, standard) + coverage + median-per-unit baseline.
7. SHAP effect summaries for the standard and personal p50 models (tasks_truth.csv is used
   only for the learned-vs-hidden comparison, never for training).
8. artifacts under ml/artifacts/task_time/v1 + model_run.json. No Supabase writes here.

random_state=42 everywhere (docs/models.md, General rules).
"""

import argparse
import json
import math
import warnings
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import shap
from joblib import dump
from sklearn.model_selection import GroupKFold

from ml.task_time.features import (
    CATEGORICAL,
    PERSONAL_FEATURES,
    STANDARD_FEATURES,
    build_features,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data/output"
DEFAULT_OUT_DIR = REPO_ROOT / "ml/artifacts/task_time/v1"

SEED = 42
N_ESTIMATORS = 600
LEARNING_RATE = 0.05
EARLY_STOPPING_ROUNDS = 50
OOF_FOLDS = 5
ALPHAS = (0.1, 0.5, 0.9)
COVERAGE_BOUNDS = (0.75, 0.85)
CONFORMAL_COVERAGE = 0.8
SHAP_SAMPLE = 400

MODEL_NAME = "task_time"
VERSION = "v1"


def lgb_params(alpha: float, n_estimators: int = N_ESTIMATORS) -> dict:
    """Exactly the parameters from docs/models.md §2."""
    return {
        "objective": "quantile",
        "alpha": alpha,
        "n_estimators": n_estimators,
        "learning_rate": LEARNING_RATE,
        "num_leaves": 31,
        "min_child_samples": 20,
        "subsample": 0.8,
        "subsample_freq": 1,
        "colsample_bytree": 0.8,
        "reg_lambda": 1.0,
        "random_state": SEED,
    }


def _fit_quantile(X_tr, y_tr, X_va, y_va, alpha: float) -> lgb.LGBMRegressor:
    model = lgb.LGBMRegressor(**lgb_params(alpha))
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*'eval_set' is deprecated.*")
        model.fit(
            X_tr,
            y_tr,
            eval_set=[(X_va, y_va)],
            callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
        )
    return model


def load_data(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, pd.DataFrame]:
    """Load generator output; add the 1-based `day` column (day 1 = first task date)."""
    rd = lambda name: pd.read_csv(Path(data_dir) / f"{name}.csv")
    data = {
        name: rd(name)
        for name in (
            "tasks",
            "shifts",
            "operators",
            "machines",
            "weather",
            "machine_health_daily",
            "tasks_truth",
        )
    }
    dates = pd.to_datetime(data["tasks"]["task_date"])
    data["tasks"]["day"] = (dates - dates.min()).dt.days + 1
    return data


def _shap_frame(
    model, X: pd.DataFrame, n_sample: int = SHAP_SAMPLE
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(sampled feature rows, SHAP values) for a fitted LightGBM model."""
    rng = np.random.default_rng(SEED)
    idx = np.sort(rng.choice(len(X), min(n_sample, len(X)), replace=False))
    Xs = X.iloc[idx].reset_index(drop=True)
    values = shap.TreeExplainer(model).shap_values(Xs)
    if isinstance(values, list):
        values = values[0]
    return Xs, pd.DataFrame(np.asarray(values), columns=list(X.columns))


def shap_table(shap_df: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    mean_abs = shap_df.abs().mean().sort_values(ascending=False).head(n)
    return pd.DataFrame({"feature": mean_abs.index, "mean_abs_shap": mean_abs.to_numpy()})


def _conformal_offset(p_lo: np.ndarray, p_hi: np.ndarray, y: np.ndarray) -> float:
    """Split-conformal (CQR) offset so the widened [p_lo - q, p_hi + q] hits ~80% coverage."""
    scores = np.maximum(p_lo - y, y - p_hi)
    n = len(scores)
    level = min(1.0, math.ceil((n + 1) * CONFORMAL_COVERAGE) / n)
    return float(np.quantile(scores, level))


def learned_effects(results: dict) -> pd.DataFrame:
    """Learned (SHAP) vs hidden (tasks_truth.csv) effect table.

    Learned effects come from the personal p50 SHAP values on the test sample; hidden
    effects are group-mean log effects of the truth factors. tasks_truth.csv is used ONLY
    here, for comparison — never for training.
    """
    tasks = results["data"]["tasks"]
    truth = (
        results["data"]["tasks_truth"]
        .merge(
            tasks[["task_id", "material_type", "terrain_slope_deg", "operator_id"]],
            on="task_id",
            how="inner",
        )
        .merge(
            results["data"]["operators"][["operator_id", "skill_score"]],
            on="operator_id",
            how="left",
        )
    )
    Xs, sp = results["shap_pers"]

    rain = Xs["rain_mm"].to_numpy()
    wet = rain > 2.0
    s_rain = sp["rain_mm"].to_numpy()
    night = (Xs["shift_type"].astype(str) == "night").to_numpy()
    s_night = sp["shift_type"].to_numpy()
    rock = (Xs["material_type"].astype(str) == "rock").to_numpy()
    clay = (Xs["material_type"].astype(str) == "clay").to_numpy()
    s_mat = sp["material_type"].to_numpy()

    lg = np.log
    hidden = {
        "rain: % when raining": math.exp(
            lg(truth.loc[truth["f_rain"] > 1.0, "f_rain"]).mean()
            - lg(truth.loc[truth["f_rain"] == 1.0, "f_rain"]).mean()
        )
        - 1.0,
        "slope: log per deg": float(
            np.polyfit(truth["terrain_slope_deg"].to_numpy(), lg(truth["f_slope"].to_numpy()), 1)[0]
        ),
        "rock vs clay: %": math.exp(
            lg(truth.loc[truth["material_type"] == "rock", "f_material"]).mean()
            - lg(truth.loc[truth["material_type"] == "clay", "f_material"]).mean()
        )
        - 1.0,
        "night: %": math.exp(
            lg(truth.loc[truth["f_night"] > 1.0, "f_night"]).mean()
            - lg(truth.loc[truth["f_night"] == 1.0, "f_night"]).mean()
        )
        - 1.0,
        "skill: log per skill point (personal)": float(
            np.polyfit(truth["skill_score"].to_numpy(), lg(truth["f_skill"].to_numpy()), 1)[0]
        ),
    }
    learned = {
        "rain: % when raining": math.exp(s_rain[wet].mean() - s_rain[~wet].mean()) - 1.0,
        "slope: log per deg": float(
            np.polyfit(Xs["terrain_slope_deg"].to_numpy(), sp["terrain_slope_deg"].to_numpy(), 1)[0]
        ),
        "rock vs clay: %": math.exp(s_mat[rock].mean() - s_mat[clay].mean()) - 1.0,
        "night: %": math.exp(s_night[night].mean() - s_night[~night].mean()) - 1.0,
        "skill: log per skill point (personal)": float(
            np.polyfit(Xs["skill_score"].to_numpy(), sp["skill_score"].to_numpy(), 1)[0]
        ),
    }
    sign = {
        "rain: % when raining": +1,
        "slope: log per deg": +1,
        "rock vs clay: %": +1,
        "night: %": +1,
        "skill: log per skill point (personal)": -1,
    }
    rows = [
        {
            "effect": name,
            "learned": learned[name],
            "hidden": hidden[name],
            "expected_direction": "+" if sign[name] > 0 else "-",
            "direction_ok": learned[name] * sign[name] > 0,
        }
        for name in learned
    ]
    return pd.DataFrame(rows)


def metrics_table(results: dict) -> pd.DataFrame:
    m = results["metrics"]
    return pd.DataFrame(
        {
            "model": ["personal p50", "standard p50", "baseline (median min/unit x qty)"],
            "MAE_min": [m["personal_mae_min"], m["standard_mae_min"], m["baseline_mae_min"]],
            "MAPE": [m["personal_mape"], m["standard_mape"], m["baseline_mape"]],
        }
    )


def train_all(
    data_dir: Path = DEFAULT_DATA_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    with_shap: bool = True,
) -> dict:
    data = load_data(data_dir)
    tasks = data["tasks"]
    shifts, operators, machines, weather, health = (
        data[k] for k in ("shifts", "operators", "machines", "weather", "machine_health_daily")
    )
    day = tasks["day"]
    completed = tasks["status"] == "completed"  # future-day rows are never trained on
    tr = completed & day.between(1, 70)
    va = completed & day.between(71, 80)
    te = completed & day.between(81, 90)
    y = np.log1p(tasks["actual_duration_min"].to_numpy())

    # ---- 1. standard p50 (early stopping on validation) ----------------------------------
    feats = build_features(tasks, shifts, operators, machines, weather, health)
    X_std = feats[STANDARD_FEATURES]
    std_model = _fit_quantile(X_std[tr], y[tr.to_numpy()], X_std[va], y[va.to_numpy()], alpha=0.5)
    best_iter = int(std_model.best_iteration_)

    # ---- 2. out-of-fold standard_min for train+val; step-1 model for test + future --------
    under = (tr | va).to_numpy()
    X_tv = X_std.loc[under].reset_index(drop=True)
    y_tv = y[under]
    weeks = (
        pd.to_datetime(tasks.loc[under, "task_date"]).dt.isocalendar().week.astype(int).to_numpy()
    )
    oof = np.full(int(under.sum()), np.nan)
    gkf = GroupKFold(n_splits=OOF_FOLDS)
    for itr, iva in gkf.split(X_tv, groups=weeks):
        fold = lgb.LGBMRegressor(**lgb_params(alpha=0.5, n_estimators=best_iter))
        fold.fit(X_tv.iloc[itr], y_tv[itr])
        oof[iva] = np.expm1(fold.predict(X_tv.iloc[iva]))
    standard_min = np.expm1(std_model.predict(X_std))  # test + future rows
    standard_min[under] = oof

    # ---- 3. recompute personal features with real standard_min ---------------------------
    tasks2 = tasks.copy()
    tasks2["standard_min"] = standard_min
    feats2 = build_features(tasks2, shifts, operators, machines, weather, health)
    X_pers = feats2[PERSONAL_FEATURES]

    # ---- 4. personal quantile models ------------------------------------------------------
    models = {}
    raw = {}
    for a, name in ((0.1, "p10"), (0.5, "p50"), (0.9, "p90")):
        models[name] = _fit_quantile(
            X_pers[tr], y[tr.to_numpy()], X_pers[va], y[va.to_numpy()], alpha=a
        )
        raw[name] = np.expm1(models[name].predict(X_pers))
    ordered = np.sort(np.column_stack([raw["p10"], raw["p50"], raw["p90"]]), axis=1)
    p10, p50, p90 = ordered[:, 0], ordered[:, 1], ordered[:, 2]

    # ---- 5. validation coverage + split-conformal widening --------------------------------
    yv = tasks["actual_duration_min"].to_numpy()[va.to_numpy()]
    cov_raw = float(np.mean((yv >= p10[va.to_numpy()]) & (yv <= p90[va.to_numpy()])))
    offset = 0.0
    applied = not (COVERAGE_BOUNDS[0] <= cov_raw <= COVERAGE_BOUNDS[1])
    if applied:
        offset = _conformal_offset(p10[va.to_numpy()], p90[va.to_numpy()], yv)
    lo = p10 - offset
    hi = p90 + offset

    # ---- 6. test metrics ------------------------------------------------------------------
    yt = tasks["actual_duration_min"].to_numpy()[te.to_numpy()]
    std_p50 = np.expm1(std_model.predict(X_std[te]))
    base_rate = (
        tasks.loc[tr | va]
        .assign(rate=lambda d: d["actual_duration_min"] / d["quantity"])
        .groupby("task_type")["rate"]
        .median()
    )
    baseline_p50 = (
        tasks.loc[te, "task_type"].map(base_rate).to_numpy() * tasks.loc[te, "quantity"].to_numpy()
    )

    def mae(a, b):
        return float(np.mean(np.abs(a - b)))

    def mape(a, b):
        return float(np.mean(np.abs(a - b) / b))

    metrics = {
        "personal_mae_min": mae(p50[te.to_numpy()], yt),
        "personal_mape": mape(p50[te.to_numpy()], yt),
        "standard_mae_min": mae(std_p50, yt),
        "standard_mape": mape(std_p50, yt),
        "baseline_mae_min": mae(baseline_p50, yt),
        "baseline_mape": mape(baseline_p50, yt),
        "coverage_test": float(np.mean((yt >= lo[te.to_numpy()]) & (yt <= hi[te.to_numpy()]))),
        "coverage_val_raw": cov_raw,
        "conformal_offset_min": offset,
        "conformal_applied": bool(applied),
        "personal_beats_baseline": mae(p50[te.to_numpy()], yt) < mae(baseline_p50, yt),
    }

    # ---- 7. SHAP effects ------------------------------------------------------------------
    shap_std = shap_pers = None
    Xs_std = Xs_pers = None
    if with_shap:
        Xs_std, shap_std = _shap_frame(std_model, X_std.loc[te.to_numpy()].reset_index(drop=True))
        Xs_pers, shap_pers = _shap_frame(
            models["p50"], X_pers.loc[te.to_numpy()].reset_index(drop=True)
        )

    results = {
        "data": data,
        "features": feats2,
        "models": {"standard": std_model, **models},
        "preds": {
            "p10": p10,
            "p50": p50,
            "p90": p90,
            "standard_p50": np.expm1(std_model.predict(X_std)),
        },
        "metrics": metrics,
        "best_iterations": {
            "standard": best_iter,
            **{k: int(m.best_iteration_ or 0) for k, m in models.items()},
        },
        "shap_std": (Xs_std, shap_std) if shap_std is not None else None,
        "shap_pers": (Xs_pers, shap_pers) if shap_pers is not None else None,
        "out_dir": Path(out_dir),
        "params": lgb_params(0.5),
    }
    _save_artifacts(results, out_dir, with_shap)
    return results


def _save_artifacts(results: dict, out_dir: Path, with_shap: bool) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dump(results["models"]["standard"], out / "standard.joblib")
    for name in ("p10", "p50", "p90"):
        dump(results["models"][name], out / f"{name}.joblib")

    (out / "features.json").write_text(
        json.dumps(
            {
                "standard": STANDARD_FEATURES,
                "personal": PERSONAL_FEATURES,
                "categorical": CATEGORICAL,
            },
            indent=2,
        )
    )
    (out / "conformal_offset.json").write_text(
        json.dumps(
            {
                "offset_min": results["metrics"]["conformal_offset_min"],
                "applied": results["metrics"]["conformal_applied"],
                "validation_coverage_raw": results["metrics"]["coverage_val_raw"],
                "level": CONFORMAL_COVERAGE,
            },
            indent=2,
        )
    )
    (out / "params.json").write_text(
        json.dumps(
            {
                "lgb_params": results["params"],
                "best_iterations": results["best_iterations"],
                "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
                "oof_folds": OOF_FOLDS,
            },
            indent=2,
        )
    )
    metrics = results["metrics"]
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))

    tasks = results["data"]["tasks"]
    te = tasks["status"].eq("completed") & tasks["day"].between(81, 90)
    preds = pd.DataFrame(
        {
            "task_id": tasks.loc[te, "task_id"].to_numpy(),
            "task_date": tasks.loc[te, "task_date"].to_numpy(),
            "actual_duration_min": tasks.loc[te, "actual_duration_min"].to_numpy(),
            "personal_p10_min": results["preds"]["p10"][te.to_numpy()],
            "personal_p50_min": results["preds"]["p50"][te.to_numpy()],
            "personal_p90_min": results["preds"]["p90"][te.to_numpy()],
            "standard_p50_min": results["preds"]["standard_p50"][te.to_numpy()],
        }
    )
    preds["final_p10_min"] = preds["personal_p10_min"] - metrics["conformal_offset_min"]
    preds["final_p90_min"] = preds["personal_p90_min"] + metrics["conformal_offset_min"]
    preds.to_csv(out / "test_predictions.csv", index=False)

    if with_shap:
        for key, fname in (
            ("shap_std", "shap_top_standard.csv"),
            ("shap_pers", "shap_top_personal.csv"),
        ):
            if results[key] is not None:
                shap_table(results[key][1]).to_csv(out / fname, index=False)
        learned_effects(results).to_json(out / "shap_effects.json", orient="records", indent=2)

    (out / "model_run.json").write_text(
        json.dumps(
            {
                "model_name": MODEL_NAME,
                "version": VERSION,
                "trained_at": datetime.now(UTC).isoformat(),
                "params": {"lgb": results["params"], "best_iterations": results["best_iterations"]},
                "metrics": metrics,
                "artifact_path": str(Path("ml/artifacts") / MODEL_NAME / VERSION),
                "is_active": False,
            },
            indent=2,
        )
    )


def load_results(out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    """Reload what the notebook needs from saved artifacts (no retraining)."""
    out = Path(out_dir)
    metrics = json.loads((out / "metrics.json").read_text())
    effects = pd.DataFrame(json.loads((out / "shap_effects.json").read_text()))
    return {
        "out_dir": out,
        "metrics": metrics,
        "test_predictions": pd.read_csv(out / "test_predictions.csv"),
        "shap_top": {
            "standard": pd.read_csv(out / "shap_top_standard.csv"),
            "personal": pd.read_csv(out / "shap_top_personal.csv"),
        },
        "effects": effects,
        "conformal": json.loads((out / "conformal_offset.json").read_text()),
        "model_run": json.loads((out / "model_run.json").read_text()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = ap.parse_args()
    results = train_all(args.data_dir, args.out_dir)
    m = results["metrics"]
    print()
    print(metrics_table(results).to_string(index=False, float_format=lambda v: f"{v:8.2f}"))
    print(
        f"\n  p10-p90 coverage (test, final): {m['coverage_test']:.3f}"
        f"   (validation raw {m['coverage_val_raw']:.3f},"
        f" conformal offset {f'applied: {m["conformal_offset_min"]:.1f} min' if m['conformal_applied'] else 'not needed'})"
    )
    print(
        f"  personal vs baseline: MAE {m['personal_mae_min']:.2f} vs {m['baseline_mae_min']:.2f} min"
        f" -> {'BEATS' if m['personal_beats_baseline'] else 'LOSES'}"
    )
    ok = (
        m["personal_beats_baseline"]
        and COVERAGE_BOUNDS[0] <= m["coverage_test"] <= COVERAGE_BOUNDS[1]
    )
    print(f"  artifacts: {results['out_dir']}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
