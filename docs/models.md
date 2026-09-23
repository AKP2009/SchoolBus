# Models

Every model and rule system in the project: **purpose, inputs, parameters, output, evaluation,
and where the output is stored**. Column names match `docs/schema.md`.

| # | Model | Type | Trained by us? | Priority | Owner |
|---|---|---|---|---|---|
| R | Alert rule engine | Rules + graded response | No | P0 | C |
| 1 | Unusual behaviour / anomaly | Isolation Forest (+ LSTM AE) | Yes | P0 | A |
| 2 | Task time estimation | LightGBM quantile regression | Yes | P0 | A |
| 3 | Predictive maintenance | XGBoost classifier | Yes | P1 | A |
| 4 | Fleet clustering + outliers | K-Means + z-score / DBSCAN | Yes | P1 | A |
| 5 | Fatigue and attention | MediaPipe + thresholds, YOLO phone | Pretrained | P0 | B |
| 6 | Proximity / blindspot | YOLO + ByteTrack | Pretrained | P0 | B |
| 7 | RAG training chatbot | Embeddings + pgvector + LLM | No training | P1 | C |
| 8 | Voice assistant | faster-whisper + LLM intent + Piper | Pretrained | P1 | B |
| 9 | Shift handover summary | LLM prompt | No training | P1 | C |
| 10 | Training recommender | Rules on metrics | No | P1 | C |
| 11 | Digital twin health score | Weighted formula | No | P1 | A |
| 12 | Plan re-evaluation | Model 2 + greedy scheduler | No | P1 | A |

**General rules**
- Split by **time**, not randomly: train on days 1–70, validate 71–80, test 81–90. Random splits
  leak the future and give inflated scores.
- Inference functions select feature columns explicitly (never `select *`) and never read
  `anomaly_label` or `anomaly_type`; those stay in `telemetry` only for the demo's detected-vs-actual view.
- Fit scalers and encoders on train only. Save them with the model (`ml/artifacts/<model>/`).
- Log each trained model into `model_runs` with params and metrics.
- Every prediction shown to a user comes with a reason (SHAP factors, triggering signals, or rule).
- `random_state=42` everywhere.

---

## R. Alert rule engine (P0)

**Purpose:** Instant, explainable warnings for known limits. Runs on every telemetry row in the
replay stream before the ML models.

**Rules** (assumed thresholds, see `assumptions.md`)

| Code | Condition | Severity |
|---|---|---|
| `COOLANT_HIGH` | coolant_temp_c > 100 for 2 min | warning |
| `COOLANT_CRITICAL` | coolant_temp_c > 105 | critical |
| `HYD_OIL_HIGH` | hydraulic_oil_temp_c > 90 | warning; > 95 critical |
| `OIL_PRESSURE_LOW` | oil_pressure_kpa < 100 while engine_rpm > 1200 | critical |
| `BATTERY_LOW` | battery_voltage < 24.0 for 5 min | warning |
| `HYD_PRESSURE_DROP` | 2-min mean hydraulic_pressure_bar < 65% of the 3-min mean ending 3 min earlier (baseline > 100 bar), engine_load_pct > 40 for the last 6 min, stationary (3-min mean ground_speed_kmh < 2) | critical (possible leak) |
| `SEATBELT` | not fastened and (speed > 0.5 or load > 20%) for 5 s | warning; 30 s critical |
| `TIP_RISK` | abs(pitch) or abs(roll) > 15° | warning; > 25° critical |
| `EXCESS_IDLE` | is_idle for > 10 continuous min at mean engine_rpm > 1200 (high idle) | info; > 20 min warning |
| `OVERSPEED` | ground_speed_kmh above type limit or geofence limit | warning |
| `FAULT_CODE` | fault_code not null | from fault code table |

**Hysteresis:** an alert clears only when the signal is 5% inside the limit for 2 minutes, so it
doesn't flicker on and off.

**Graded response** (for critical internal alerts):

| Stage | When | What happens |
|---|---|---|
| `warn` | condition true | Banner + sound in cab, alert row created |
| `derate` | still true after 2 min | Recommend reduced power ("Switch to economy mode") |
| `recommend_shutdown` | still true after 5 min or value rising | Step-by-step safe shutdown: lower attachment, move to level ground, idle to cool, shut down |
| `escalated` | still true after 7 min or operator ignores | Site manager notified, alert shows on manager feed as critical |
| `resolved` | condition clears with hysteresis | Stage closed, time-to-resolve logged |

Never cut power automatically.

**Output:** row in `alerts` (`source='rule'`, `alert_code`, `stage`, `evidence` = signal values).

---

## 1. Unusual behaviour / anomaly detection (P0)

**Purpose:** Catch abnormal machine behaviour that no single threshold catches, and separate
real faults from sensor glitches.

**Input:** `telemetry`, per machine, rolling windows.

**Features** (computed per minute on a trailing window):
- For each of engine_rpm, engine_load_pct, coolant_temp_c, oil_pressure_kpa,
  hydraulic_pressure_bar, hydraulic_oil_temp_c, fuel_rate_lph, battery_voltage, vibration_rms_g:
  5-min mean, 5-min std, 15-min slope
- idle_pct over last 30 min
- rpm_per_load = engine_rpm / (engine_load_pct + 1)
- fuel_per_load = fuel_rate_lph / (engine_load_pct + 1)
- delta between coolant and hydraulic oil temp
- machine_type (one-hot) — or train one model per machine type (preferred)

**Model:** `sklearn.ensemble.IsolationForest`
```python
IsolationForest(n_estimators=200, contamination=0.03, max_samples='auto',
                max_features=1.0, random_state=42)
```
Preprocessing: `StandardScaler` per machine type. Train on data **before** any known failure window.

**Sensor glitch vs real fault:** if only one signal is extreme for a single minute and its
neighbours are normal, classify as `sensor_glitch` (info). Real faults persist ≥ 3 minutes or
move multiple correlated signals.

**Explaining the alert:** report the 3 features with the largest absolute z-score in that window.

**Output**
```json
{ "machine_id": "M04", "ts": "...", "anomaly_score": 0.71, "is_anomaly": true,
  "kind": "machine_fault", "top_signals": [
    {"feature": "hydraulic_oil_temp_c_mean5", "z": 3.8},
    {"feature": "hydraulic_pressure_bar_std5", "z": 3.1} ] }
```
Stored as an `alerts` row (`source='anomaly_model'`, `category='behaviour'`) and as
`anomaly_score` in `machine_health_snapshots`. `anomaly_score` = `-score_samples()` min-max scaled to 0–1.

**Evaluation** (against ground-truth `anomaly_label`): precision, recall, F1 per `anomaly_type`,
and detection delay (minutes from anomaly start to first alert). Target: recall ≥ 0.8, median delay ≤ 5 min.

**Implementation decisions (v2, `ml/01_anomaly.ipynb`, `ml/inference/anomaly.py`)**
- Windows are trailing and time-based per machine (`5min`, `15min`, `30min`); the 15-min slope is
  a least-squares slope in units per minute. `rpm_per_load` / `fuel_per_load` clip load at 0
  (the sensor reads slightly negative at idle, which gave division by ~0).
- v2 adds `pitch_deg`, `roll_deg`, `ground_speed_kmh` (mean5 / std5 / slope15), giving 40 features, and
  trains **one model per machine type × state** (`idle` / `working` from `is_idle` of the scored
  minute): 8 IsolationForests with the parameters above, no one-hot.
- "Before any known failure window" = training excludes every row within `drift_window_h` engine
  hours before a failure. Engine hours advance by full shift length, so they are rebuilt from
  `shifts` (matches the `pre_failure_*` labels on 37,142 of 37,143 rows).
- Sensor glitch = exactly one signal outside its sensor validity range (`PLAUSIBLE_RANGE` in the
  inference module; oil pressure only while rpm > 500) and plausible the minute before. Causal
  and instant. The reading is forward-filled before features so one spike doesn't pollute 5
  minutes of features.
- Real fault (v2): model flags are ignored for the first 5 min after an idle↔working switch or a
  data gap (`SETTLE_MIN`), and the alert fires after 3 consecutive counting flags
  (`PERSIST_MIN`). v1's "≥ 2 signals with |z| ≥ 3" shortcut is removed.
- Output `kind` ∈ {`normal`, `machine_fault`, `sensor_glitch`}; `is_anomaly = kind != 'normal'`.
  For a glitch, the first `top_signals` entry is the raw signal, with its z computed using that signal's `_mean5` scaler.
- Evaluation: an event is caught if a correctly classified alert fires between its start and 5 minutes after its end.
  Only injected types are evaluated.
  A false alert = a run of alerting minutes touching no injected event, per 100 machine-hours.
- **Result (test, days 81–90):** rules + model recall 0.97, median delay 2 min, 17.5 false alerts
  per 100 machine-h (v1: 1.00, 2 min, 138). Model alone recall 0.28 (§1 target 0.8 missed; v1
  0.62). 97% of its flags on normal minutes fall within 5 min of a state switch, so the settle gate
  discards them, and it rarely flags battery or leak minutes. Next step if needed: train each model on
  settled minutes only. Its reliable contribution is glitch-vs-fault separation (19/19). Details:
  `ml/artifacts/anomaly/README.md`.
- Artifacts (≈ 5 MB, committed): `iforest_<type>_<state>.joblib`, `scaler_<type>_<state>.joblib`
  (joblib compress=3), `feature_list.json`, `config.json` (model keys, thresholds, score
  min/max, machine map), `metrics.json` (v2 and v1 results). They load only with the versions
  pinned in `ml/requirements.txt`. Not yet logged to `model_runs`; `metrics.json` has the fields
  that table needs.

**Stretch (P2) — LSTM autoencoder:** window 60 steps × 11 signals, encoder LSTM(64) → LSTM(16),
RepeatVector, decoder LSTM(16) → LSTM(64) → TimeDistributed(Dense(11)). Adam lr 1e-3, batch 128,
epochs 30–50, early stopping patience 5. Threshold = 99th percentile of reconstruction error on
normal validation data.

---

## 2. Task time estimation (P0)

**Purpose:** Predict how long a task will take with a realistic range, explain why, and compare
with the operator's usual performance.

**Input:** `tasks` joined with `weather` (hour of scheduled start), `operators`, `machines`,
`fatigue_log` (average for the operator in that hour of shift, if available).

**Features**
| Feature | Type |
|---|---|
| task_type, material_type, machine_type, unit | categorical |
| quantity, terrain_slope_deg, haul_distance_m | numeric |
| operator skill_score, experience_years, certification_level | numeric |
| operator_avg_time_ratio for this task_type (last 30 days, computed only from past) | numeric |
| machine health_score at task start | numeric |
| temp_c, rain_mm, wind_kmh, visibility_m, dust_index | numeric |
| shift_type, hours_into_shift, day_of_week | categorical / numeric |

**Target:** `actual_duration_min`. Train on `log1p(actual_duration_min)` and convert back.

**Model:** three LightGBM regressors, one per quantile
```python
LGBMRegressor(objective='quantile', alpha=a,          # a in {0.1, 0.5, 0.9}
              n_estimators=600, learning_rate=0.05, num_leaves=31,
              min_child_samples=20, subsample=0.8, subsample_freq=1,
              colsample_bytree=0.8, reg_lambda=1.0, random_state=42)
# early stopping: 50 rounds on the validation set
```
Categoricals passed as `category` dtype. Enforce p10 ≤ p50 ≤ p90 after prediction.

**Explanation:** SHAP `TreeExplainer` on the p50 model. Convert the top 2–3 SHAP values into
minutes and show them ("rain +8 min").

**Output**
```json
{ "task_id": "T-...", "p10_min": 35.2, "p50_min": 42.0, "p90_min": 55.1,
  "factors": [{"feature": "rain_mm", "impact_min": 8.1}, {"feature": "material_type=rock", "impact_min": 6.3}],
  "operator_avg_min": 47.5, "expected_efficiency": 0.88 }
```
Stored in `tasks.predicted_p10_min / p50 / p90` and `prediction_factors`.

**Evaluation:** MAE and MAPE on p50; interval coverage (share of actuals between p10 and p90,
target ≈ 80%). Baseline to beat: median duration per task_type × quantity.

**Implementation decisions (v1, `ml/02_task_time.ipynb`, `ml/inference/task_time.py`)**
- Targets: `completed` tasks with `actual_duration_min` (6,379). `delayed` tasks have no duration in
  the generated data (still running at shift end), so they drop out. Completed tasks with a random
  `delay_reason` are kept. Split by `task_date`: 4,946 / 717 / 716.
- Weather = the latest `weather` row at or before `scheduled_start` for the site (the hour
  containing it). `hours_into_shift` = scheduled_start − shift start. `day_of_week` from task_date.
- `health_score` at task start = service-based health rebuilt from `maintenance_log`:
  clip(1 − 0.2·hours_since_service/interval, 0.3, 1), with engine hours rebuilt per shift and anchored
  to the end-of-run `machines.total_engine_hours`. The generator's drift term needs the next failure
  (future), so it isn't used. The live backend passes `health_score` from `v_machine_health_latest`.
- `operator_avg_time_ratio` = mean of actual ÷ reference minutes over the operator's same-type tasks
  that **ended before** this task's scheduled start, last 30 days (NaN without history). Reference
  minutes = median minutes per unit per task_type on train, with haul measured in ton-km. The
  evaluation baseline stays literal (per unit, no distance).
- Extra feature `operator_fatigue_hour_avg`: the operator's mean `fatigue_score` in that whole hour
  of shift over earlier shifts (the "if available" fatigue input above).
- Categoricals use the training levels (`encoders.json`); an unseen level becomes NaN.
- **Interval calibration (one tuning round):** raw intervals covered 83% on train but 66% on test
  (too narrow on both sides: out-of-sample p50 error is larger than the in-sample residuals the
  quantile models learn). Conformalized quantile regression on validation: offset
  c = quantile of max(p10 − y, y − p90) in log space; shipped interval [p10 − c, p90 + c], then sorted.
  c = 0.052 (×1.05). LightGBM parameters unchanged.
- Factors: top 3 by |impact| in minutes, impact = p50 − expm1(log p50 − shap). `quantity`/`unit` are
  never shown (size of the job). Each factor = `{feature, label, impact_min}`, e.g.
  `{"feature": "material_type=rock", "label": "Material: rock", "impact_min": 6.8}`.
- `operator_avg_min` = operator_avg_time_ratio × reference minutes (null without history);
  `expected_efficiency` = p50 ÷ operator_avg_min (< 1 = faster than usual).
- **Result (test, days 81–90):** p50 MAE 8.9 min / MAPE 10.6% vs baseline 23.8 min / 28.4%;
  p10–p90 coverage 80.0% (raw 65.5%). The random +15–60 min delays are the main misses (MAPE 31%).
  Planted factors recovered: rain (with visibility), material, slope, skill, at 69–88% of the planted size.
  **Not recovered: hours into shift (×0.94 vs ×1.03) and night (×1.05 vs ×1.10)**, because of
  survivorship: long tasks late in a shift or at night get cut off as `delayed` (82% after hour 7) and
  never become targets. **Late-shift and night p50s are optimistic.** The fix would be censored-duration
  training (not done). Details: `ml/artifacts/task_time/README.md`.
- Artifacts (≈ 1.8 MB, committed): `lgbm_p10/p50/p90.joblib` (compress=3), `encoders.json`,
  `config.json` (interval offset, params), `feature_list.json`, `metrics.json`, `shap_importance.png`.
  Pinned in `ml/requirements.txt` (lightgbm 4.7.0, shap 0.52.0). Not yet logged to `model_runs`.

---

## 3. Predictive maintenance (P1)

**Purpose:** Probability that a machine has a failure in the next 48 engine hours, and the likely component.

**Input:** `telemetry` aggregated per machine per engine hour + `maintenance_log`.

**Features** (per machine per hour)
- hours_since_service, hours_since_service / service_interval_hours
- 24h and 72h mean and slope of: coolant_temp_c, oil_pressure_kpa, hydraulic_oil_temp_c,
  hydraulic_pressure_bar std, vibration_rms_g, battery_voltage
- anomaly count and mean anomaly_score over last 24h (from model 1)
- fault code count 24h / 7d
- cumulative high-load hours (engine_load_pct > 80) since service
- machine_type, age (years)

**Label:** 1 if a `maintenance_log` row with `event_type='failure'` occurs within the next 48
engine hours, else 0. Drop the rows after a failure until the repair is logged.

**Model:** `xgboost.XGBClassifier`
```python
XGBClassifier(n_estimators=400, max_depth=5, learning_rate=0.05, subsample=0.8,
              colsample_bytree=0.8, min_child_weight=3, gamma=0.1,
              scale_pos_weight=neg/pos, eval_metric='aucpr', random_state=42)
```
**Likely component:** a second small XGBoost multiclass model on failure rows only, or the
component of the subsystem with the worst trend (simpler, fine for the demo).

**Output**
```json
{ "machine_id": "M04", "horizon_hours": 48, "failure_probability": 0.72,
  "likely_component": "hydraulics",
  "top_factors": [{"feature": "hydraulic_oil_temp_c_slope72", "shap": 0.31}] }
```
Stored in `maintenance_predictions`. Risk bands: < 0.3 low, 0.3–0.6 medium, > 0.6 high.

**Evaluation:** PR-AUC, recall at 0.5 threshold (target ≥ 0.75), and **lead time** — how many
hours before failure the probability first crossed 0.6. Missing a failure costs more than a false alarm.

**Implementation decisions (v2 + safety floor, `ml/04_predictive_maintenance.ipynb`, `ml/inference/maintenance.py`)**
- One row per machine × engine-hour bin that has telemetry. The prediction is made at the bin's last minute,
  and all windows trail it. Engine hours are rebuilt from `shifts`, cut at `maintenance_log` failures (not
  truth files), and anchored to `machines.total_engine_hours`. They match the logged failure engine hours
  within 0.05 h.
- §3 features as listed: 24/72 h are **engine** hours, minute-weighted means and least-squares
  slopes per engine hour; hydraulic pressure uses its hourly std. Sensor glitches are replaced first (model 1 rule).
  Anomaly count = model-1 `machine_fault` events. Fault-code count = episodes (runs of one code); "7d" is
  calendar days. Service features use scheduled services only.
- Label: failure within (t, t + 48] engine hours. Rows between failure and repair are dropped, and so is the
  censored last 48 h of each machine with no failure ahead.
- **Days 81–90 hold only 2 failures**, so the main evaluation is forward-chaining, grouped time CV
  by failure: boundaries on 2026-07-02 / 07-26 / 08-13 (no open pre-failure window), 48 h purge, and 15
  held-out failures. The shipped model is trained on days 1–70 as usual.
- **Tuning round (one):** v1 (§3 only) caught no cooling or undercarriage failure and 1/3 electrical, even
  though the battery sat about 25 σ below normal. With 1–2 training failures per component, trees split
  on levels of the components that failed in training and on machine type / age (machine identity). v2 keeps
  the §3 features and parameters and adds: each signal's 24 h deviation from the machine's own baseline
  (engine hours t−336…t−72) in per-type training-std units (`scales` in `config.json`), `worst_dev_z24`
  and `worst_trend_z72` (component-agnostic), and vibration while travelling (> 2 km/h, undercarriage).
  `predict_failure` takes the raw columns (`SPEC_FEATURES` + `RAW_EXTRA_COLUMNS`) and adds these itself.
- Likely component = rule: the subsystem with the worst signed z (deviation or 72 h trend). Engine = oil
  pressure + vibration, and undercarriage = travelling vibration beyond the overall vibration change. The
  multiclass model was not trained: 2–3 examples per class.
- Output adds `risk_band` (low / medium / high from the bands above). `top_factors` = XGBoost's exact
  TreeSHAP (`pred_contribs`, log-odds): the top 3 contributions pushing the probability up.
- **Result v2 (CV, 15 held-out failures):** PR-AUC 0.68 (prevalence 0.10; v1 0.57; baseline rule 0.25),
  **recall at 0.5 per hour 0.44, target 0.75 missed**. 12/15 failures caught at 0.5 (v1 9/15), median
  lead 37 h (target 12 h met, v1 21 h), 0.16 false alarms per machine-week. Missed: electrical M01, M02
  and undercarriage M08 (max p ≤ 0.21). Engine hour-level recall fell from 0.67 to 0.47 in v2 (all 3 still caught).
  Component rule right on 86 % of pre-failure hours (93 % in the last 12 h, undercarriage 27 %).
  Details: `ml/artifacts/maintenance/README.md`.
- **Safety floor (ships, added after acceptance, not a tuning round):** if any signal's 24 h deviation from
  the machine's own normal is ≥ 6 σ the wrong way (`FLOOR_Z`), the probability is at least 0.35 (`FLOOR_P`,
  medium) and `top_factors` lists that `<signal>_devz24` first. Same CV, **v2 + floor:** PR-AUC 0.69,
  recall at 0.5 0.44, 12/15 caught at 0.5, median lead 37 h, 0.16 false alarms per machine-week. These are
  unchanged, since the floor sits below 0.5 and 0.6. Medium band (p ≥ 0.3), v2 → v2 + floor: hour recall 0.51 → 0.72,
  failures reaching medium 12/15 → **15/15** (M01 and M02 electrical, M08 undercarriage now medium), false medium
  alerts 0.29 → 0.25 per machine-week (runs merge), normal hours at medium or above 2.4 % → 4.7 %.
- Artifacts (≈ 0.4 MB, committed): `xgb_failure.joblib` (compress=3), `config.json`, `feature_list.json`,
  `metrics.json`, `shap_importance.png`, `lead_time.png`. xgboost 2.1.4 pinned. Not yet logged to `model_runs`.

---

## 4. Fleet clustering and outliers (P1)

**Purpose:** Group operators and machines by behaviour, rank efficiency, and flag outliers for
the manager to verify.

**Input:** weekly aggregates → `fleet_metrics_weekly`.

**Features** (per entity per week): fuel_per_productive_hour, idle_pct, productivity_per_hour,
time_ratio (actual / p50), anomaly_count per 10 h, safety_event_count per 10 h.
Machines are compared only within the same `machine_type`.

**Model**
```python
StandardScaler()
KMeans(n_clusters=k, n_init=10, random_state=42)   # k in 3..5, pick best silhouette
PCA(n_components=2)                                 # for the scatter plot only
```
**Cluster labels:** name clusters by their centroid (highest idle_pct → "idle-heavy",
lowest time_ratio + low fuel → "efficient", highest safety events → "needs safety coaching").

**Efficiency index:** `0.35*z(productivity) - 0.25*z(fuel_per_hour) - 0.2*z(idle_pct) - 0.2*z(time_ratio)`

**Outliers:** |z| > 2.5 on any feature within machine type, or DBSCAN noise points
(`eps` from k-distance plot, `min_samples=5`). `outlier_reason` names the feature.

**Output:** `cluster_id`, `cluster_label`, `efficiency_index`, `rank_in_site`, `is_outlier`,
`outlier_reason` in `fleet_metrics_weekly`.

**Evaluation:** silhouette score; check that the hidden `operators.personality` groups are
recovered (adjusted Rand index). This is our proof the clustering found real patterns.

**Implementation decisions (v2, `ml/03_clustering.ipynb`, `ml/inference/clustering.py`)**
- Weeks start on the Monday of `shift_date`. 2026-06-01 is a Monday, so weeks 1–10 = days 1–70 (fit:
  scalers, KMeans, PCA, DBSCAN); weeks 11–13 (days 71–90, week 13 has 6 days) are the holdout.
- Metrics per entity × week × machine_type ("segment"): productive_hours = non-idle engine-on hours
  (one telemetry row = one minute); fuel_per_productive_hour = all fuel burnt ÷ productive hours;
  idle_pct = idle ÷ engine-on minutes; productivity_per_hour = completed quantity ÷ productive hours;
  anomaly and safety events per 10 **engine-on** hours (engine h = productive_hours ÷ (1 − idle_pct/100),
  so the rate can be rebuilt from the stored columns).
- **Within machine type:** every segment is z-scored against its entity_type × machine_type mean/std on
  the fit weeks. Operators drive all four types, so an operator-week is the productive-hours-weighted
  mean of their per-type z. The efficiency index and the |z| > 2.5 rule use these z. For operators the
  stored `productivity_per_hour` mixes m³ and tons in weeks with a truck (the z don't).
- `anomaly_count` = `machine_fault` events (runs of fault minutes) from `ml.inference.anomaly`, sensor
  glitches excluded. Never `anomaly_label`. `safety_event_count` leaves out `fatigue_high`, because it
  follows the roster (night shifts) rather than operating behaviour.
- **time_ratio = Σ actual ÷ Σ fleet p50** over completed tasks. The fleet p50 is the task-time model
  with `operator_avg_time_ratio` left unknown (`clustering.fleet_p50`). The planning p50
  (`tasks.predicted_p50_min`) already contains the operator's usual pace, which hides the pace
  differences between operators (week-to-week reliability 0.14 vs 0.72).
- **Operator clustering weights each standardised feature by √ICC** (week-to-week reliability on the
  fit weeks: idle 0.98, fuel 0.87, productivity 0.79, time_ratio 0.72, safety 0.26, anomalies 0.07),
  so weekly Poisson noise in events doesn't set the cluster boundaries. Machines keep equal weights:
  their ICCs are ~0 because a machine's week reflects who drove it. KMeans, DBSCAN and PCA all work
  in this weighted space. Rows with < 4 productive hours are not fitted on.
- Names: "needs safety coaching" (highest safety z ≥ 0.5), "idle-heavy" (highest idle z ≥ 0.5),
  "efficient" (highest −z(time_ratio) − z(fuel)), then the strongest remaining trait ("low output", …)
  or "average". DBSCAN eps = knee of the 5-NN distance curve; a new week is noise when no training
  core point lies within eps. `outlier_reason` names the feature, its value, the type's typical value
  and z.
- `rank_in_site`: 1 = highest efficiency_index within entity_type × site × week.
- **Result:** operators k = 4 (efficient, idle-heavy, needs safety coaching, low output), machines
  k = 3. ARI vs personality: **0.45 fit weeks, 0.42 holdout** (spec as written: 0.37 / 0.36; one tuning
  round). **Target 0.5 missed.** Aggressive → safety coaching, idler → idle-heavy, novice → low output
  (100 % of fit weeks), efficient → efficient (98 %). Average splits 66 % efficient / 34 % low output:
  with k = 4 the two largest groups share a cluster, and a perfect answer that merged only those two
  would score 0.58. Efficiency index by personality: efficient +0.49 > average +0.09 > aggressive
  −0.09 > idler −0.58 > novice −0.79. Details: `ml/artifacts/clustering/README.md`.
- Artifacts (≈ 0.3 MB, committed): `model_<entity_type>.joblib` (scaler, weights, KMeans, PCA, DBSCAN
  core points, eps), `config.json` (type scalers, cluster names), `feature_list.json`, `metrics.json`,
  `pca_scatter.png`, `pca_points.json` (dashboard scatter), `k_distance.png`,
  `fleet_metrics_weekly.csv` (13 weeks of output rows). Not yet logged to `model_runs`.

---

## 5. Fatigue and attention (P0, pretrained)

**Purpose:** Detect drowsiness, distraction and phone use; drive the fatigue-aware voice assistant.

**Pipeline** (runs in `vision/`, ~10–15 fps on a laptop)
1. MediaPipe Face Mesh (`refine_landmarks=True`, `max_num_faces=1`,
   `min_detection_confidence=0.5`, `min_tracking_confidence=0.5`)
2. **EAR** (eye aspect ratio) from 6 landmarks per eye, averaged
3. **Eyes closed** if EAR < 0.22 (calibrate per person in first 30 s: threshold = 0.75 × open-eye mean)
4. **PERCLOS** = share of frames with eyes closed over the last 60 s
5. **Yawn** if MAR (mouth aspect ratio) > 0.6 for > 1.5 s
6. **Head down** if head pitch < −20° for > 2 s (from `solvePnP` on face landmarks)
7. **Phone:** YOLO pretrained COCO class 67 (`cell phone`), conf ≥ 0.5, every 5th frame

**Fatigue score** (0–1)
```
score = 0.45*min(perclos/0.3, 1) + 0.15*min(yawns_10min/3, 1) + 0.15*min(head_down_10min/3, 1)
      + 0.15*min(hours_into_shift/10, 1) + 0.10*(shift_type == 'night')
low < 0.35 ≤ medium < 0.6 ≤ high
```
Immediate critical alert if eyes closed continuously > 2 s while the machine is moving.

**Output:** one `fatigue_log` row per minute; `safety_events` for `fatigue_high` and `phone_use`.

**Assistant behaviour by level:** low — speak only for alerts; medium — check-in every 30 min,
shorter sentences; high — suggest a break now, notify supervisor, raise proximity warning distance by 2 m.

---

## 6. Proximity and blindspot (P0, pretrained)

**Model:** Ultralytics YOLOv8n or YOLO11n, COCO weights, classes person (0), car (2), truck (7).
```python
model.track(frame, classes=[0, 2, 7], conf=0.45, iou=0.45, imgsz=640,
            tracker='bytetrack.yaml', persist=True)
```
**Distance estimate:** `d ≈ (H_real × f) / h_pixels` with H_real = 1.7 m for a person, f from a
one-time calibration (stand at 3 m, measure box height). Fuse with the assumed ultrasonic
reading when available: `d = min(d_camera, d_ultrasonic)`.

**Zones:** red < 3 m (critical), orange 3–7 m (warning), clear > 7 m.
Low visibility or high fatigue adds 2 m to both limits.

**Approaching:** tracked distance decreasing > 0.5 m/s over 1 s → severity +1 level.

**Performance:** process every 2nd frame, 640 px (drop to 480 if laggy).

**Output:** `safety_events` (`proximity_breach` or `blindspot_intrusion`, `distance_m`, `sector`,
`approaching`) and an `alerts` row when orange or red.

**Implementation decisions** (`vision/proximity.py`, `vision/run.py`)
- Weights `yolo11n.pt`. Calibration: `python vision/run.py calibrate --distance 3` takes the median
  person box height over 30 frames and saves `focal_px` + `frame_height_px` to `vision/calibration.json`
  (per camera, git-ignored); f is rescaled if the frame height changes. Without the file the service
  assumes f = 1.2 × frame height and says "UNCALIBRATED" on the overlay.
- Approaching: closing speed = distance change between the newest sample and the oldest sample at
  or before 1 s ago; no verdict with less than 0.7 s of history.
- Severity: red → critical, orange → warning, approaching raises one level (orange → critical,
  red → emergency).
- Debounce, per track: post when the zone changes into red or orange, when severity rises inside the
  same zone (the object starts approaching), and every 2 s while red. Going clear posts nothing
  but resets the track, so re-entry posts again. A track unseen for 2 s is forgotten.
- Zone hysteresis: entry limits are exact (3 m / 7 m), but leaving a zone outward needs 0.3 m extra,
  otherwise a person standing at 3.0 m flips warning/critical every frame (seen on the webcam test).
- Event type: `blindspot_intrusion` for sectors rear / left / right, else `proximity_breach`.
- High fatigue widening: `FatigueStatus` is a stub (always false) until the backend has an endpoint
  to poll; `--low-visibility` widens now.
- Delivery: background thread, httpx, exponential backoff 0.5 → 10 s on connection errors and 5xx;
  4xx and 501 are logged and dropped. Bounded queue of 200, oldest dropped first. `--dry-run` prints.
- 640 → 480 px when the loop averages under 10 fps over 3 s (after a 3 s warm-up that starts at the
  first processed frame, since torch start-up makes the first inference take seconds); it does not switch back.

---

## 7. RAG training chatbot (P1)

**Embedding model:** `BAAI/bge-small-en-v1.5` (384-d, normalized). For Hindi/Tamil questions,
translate to English with the LLM first, answer in the operator's language.
**Chunking:** 600 tokens, 100 overlap, split on headings first.
**Retrieval:** `match_document_chunks(embedding, match_count=4, min_similarity=0.3)`.
**LLM:** any capable chat model via API; `temperature=0.2`, `max_tokens=500`.
**System prompt rules:** answer only from the retrieved context; if it isn't there, say so and
suggest asking the supervisor; for safety-critical topics always add the safe action first;
cite sources by title.
**Knowledge base:** our fault code table, troubleshooting FAQ, safety guidelines, operating tips,
training module text.
**Output:** answer + `sources` saved in `chat_messages`.
**Evaluation:** 25 test questions with expected answers; target ≥ 80% judged correct, 0 unsafe answers.

---

## 8. Voice assistant (P1)

- **STT:** faster-whisper `small` (`compute_type='int8'`, `language=None` for auto-detect, `vad_filter=True`)
- **Intent:** LLM with a function list: `next_task`, `report_incident`, `machine_status`,
  `explain_alert`, `call_supervisor`, `sos`, `ask_training`. `temperature=0`
- **TTS:** Piper voices (English, Hindi) — fallback to browser `speechSynthesis`
- Push-to-talk button (engine noise makes always-listening unreliable)

---

## 9. Shift handover summary (P1)

**Input:** previous shift on the same machine: `handover_notes`, unresolved alerts, safety
events, unfinished tasks, fuel_end_pct, latest maintenance prediction.
**Prompt output format:** 5 lines max — machine condition, open issues, check before starting,
unfinished work, fuel. `temperature=0.3`. Stored in `shifts.handover_summary`.

---

## 10. Training recommender (P1, rules)

| Trigger (last 7 days) | Module topic |
|---|---|
| idle_pct > 25% | Fuel-efficient operation |
| harsh_maneuver ≥ 3 | Smooth controls |
| time_ratio > 1.2 for a task_type | Technique for that task type |
| proximity breaches ≥ 2 | Blindspot awareness |
| seatbelt violations ≥ 1 | Seatbelt and cab safety |
| tip_risk events ≥ 1 | Working on slopes |
| cluster_label = 'needs safety coaching' | Scenario simulator pack |

Max 2 open recommendations per operator. Stored in `training_recommendations` with a plain-language `reason`.

---

## 11. Digital twin health score (P1, formula)

For each subsystem: `score = 1 − max(rule_penalty, 0.6*anomaly_contrib, failure_prob_if_component)`
- rule_penalty: 0 none, 0.3 warning, 0.7 critical
- anomaly_contrib: anomaly_score if the top signals belong to the subsystem
- overall = min of subsystem scores

Colour: ≥ 0.75 green, 0.5–0.75 orange, < 0.5 red. Stored in `machine_health_snapshots` every minute of replay.

Signal → subsystem: engine (rpm, oil pressure, oil temp, vibration), cooling (coolant temp),
hydraulics (hydraulic pressure, hydraulic oil temp), electrical (battery voltage),
undercarriage (vibration while travelling).

---

## 12. Plan re-evaluation (P1)

**Triggers:** a task runs past its p90; rain starts (> 2 mm/h); machine overall health < 0.6;
fatigue level high; manager edits the plan.
**Steps:** re-predict remaining tasks with current conditions → sort by priority, then by
p50 → fill until shift end with 10 min buffer → tasks that don't fit go to "next shift" →
show the diff for accept/reject. Stretch: OR-Tools CP-SAT with precedence constraints.
