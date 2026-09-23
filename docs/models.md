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
| `HYD_PRESSURE_DROP` | hydraulic_pressure_bar falls > 35% within 3 min under load | critical (possible leak) |
| `SEATBELT` | not fastened and (speed > 0.5 or load > 20%) for 5 s | warning; 30 s critical |
| `TIP_RISK` | abs(pitch) or abs(roll) > 15° | warning; > 25° critical |
| `EXCESS_IDLE` | is_idle for > 10 continuous min | info; > 20 min warning |
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

**Stretch (P2) — LSTM autoencoder:** window 60 steps × 11 signals, encoder LSTM(64) → LSTM(16),
RepeatVector, decoder LSTM(16) → LSTM(64) → TimeDistributed(Dense(11)). Adam lr 1e-3, batch 128,
epochs 30–50, early stopping patience 5. Threshold = 99th percentile of reconstruction error on
normal validation data.

---

## 2. Task time estimation (P0)

**Purpose:** Predict how long a task will take with a realistic range, explain why, and compare
with the operator's usual performance.

**Two models share one feature builder** (`ml/task_time/features.py`, imported by training and the
backend so they can never drift). Feature lists are module constants:

| Model | Features (STANDARD_FEATURES / PERSONAL_FEATURES) | Purpose |
|---|---|---|
| **Standard** | task_type, material_type, machine_type, unit, quantity, log_quantity, terrain_slope_deg, haul_distance_m, machine_health, temp_c, rain_mm, wind_kmh, visibility_m, dust_index, shift_type, hours_into_shift, day_of_week — **no operator information** | Fair "standard operator" p50 for the same work order |
| **Personal** | STANDARD_FEATURES + skill_score, experience_years, certification_level, operator_avg_time_ratio_30d | p10/p50/p90 for this operator on this task |

`operator_avg_time_ratio_30d`: per (operator_id, task_type), the mean of
`actual_duration_min / standard_min` over completed, non-delayed tasks with task_date in
[d−30, d−1] (strictly earlier dates only; NaN under 3 tasks). It uses `standard_min` as input and
can be recomputed once standard_min exists. Categoricals are pandas `category` with the DB enum
values. **Never features:** personality, operator_id, delay_reason, any `actual_*` column,
anything from `tasks_truth.csv`, `anomaly_label`/`anomaly_type`.

**standard_min** anchors every efficiency number: `efficiency = standard_min / actual_min`
(completed, non-delayed tasks only). For training rows it is computed **out-of-fold**: GroupKFold
with 5 folds grouped by ISO week, same params with `n_estimators` frozen to the standard model's
best iteration. Test and future tasks use the standard model directly. The backend recomputes the
30-day ratio against these values (`ml/inference/task_time.py`).

**as_of rule:** efficiency summaries anchor on `as_of` = max(task_date) that has a completed,
non-delayed task — not current_date (the synthetic data ends before today). avg window
[as_of−29, as_of], prev window [as_of−59, as_of−30]; `trend` = 'up' if avg ≥ 1.03·prev,
'down' if avg ≤ 0.97·prev, else 'flat'; `fleet_median` = site median efficiency for that
task_type over the avg window; any metric with n < 3 is null. The same rules live in the SQL
view `v_operator_efficiency` (migration 002).

**Target:** `log1p(actual_duration_min)`, converted back after prediction. Time split by task_date:
train days 1–70, validation 71–80, test 81–90 (docs "General rules"); future-day tasks excluded.

**Model:** standard p50, then three personal quantile models — the params from the doc below,
early stopping 50 rounds on the validation set:
```python
LGBMRegressor(objective='quantile', alpha=a,          # a in {0.1, 0.5, 0.9}
              n_estimators=600, learning_rate=0.05, num_leaves=31,
              min_child_samples=20, subsample=0.8, subsample_freq=1,
              colsample_bytree=0.8, reg_lambda=1.0, random_state=42)
# early stopping: 50 rounds on the validation set
```
Categoricals passed as `category` dtype. Enforce p10 ≤ p50 ≤ p90 after prediction.

**Intervals:** if validation p10–p90 coverage falls outside 75–85%, split-conformal (CQR)
widening calibrated on validation is applied; the offset is saved with the artifacts and added
to p10/p90 by the backend.

**Results (test days 81–90, `ml/artifacts/task_time/v1/metrics.json`):**

| model | MAE (min) | MAPE |
|---|---|---|
| personal p50 | 7.9 | 9.3% |
| standard p50 | 11.3 | 13.5% |
| baseline (median min/unit × qty) | 24.4 | 29.7% |

p10–p90 coverage 0.793 (validation raw 0.657 → conformal offset 2.81 min applied). Personal p50
beats the baseline by 3×.

**Explanation:** SHAP `TreeExplainer` on the standard p50 and personal p50 models. Convert the
top 2–3 SHAP values into minutes (exp-space deltas) and show them ("rain +8 min"). The learned
effects match the hidden formula (rain +, slope +, rock ≈ +40%, night +, skill −); tasks_truth.csv
is used **only** for that comparison (see `ml/notebooks/02_task_time.ipynb`).

**Output**
```json
{ "task_id": "T-...", "p10_min": 35.2, "p50_min": 42.0, "p90_min": 55.1,
  "standard_min": 48.0, "expected_efficiency": 0.88,
  "operator_avg_min": 47.5, "operator_avg_efficiency": 0.90, "operator_prev_efficiency": 0.95,
  "factors": [{"feature": "rain_mm", "label": "Rain", "impact_min": 8.1}] }
```
Stored in `tasks.predicted_p10_min / p50 / p90` and `prediction_factors`; the model's
`standard_min` and `expected_efficiency` are written back too (migration 002 columns).

**Evaluation:** MAE and MAPE on p50; interval coverage (target ≈ 80%, enforced by the conformal
step). Baseline to beat: median duration per task_type × quantity — beaten.

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

| Trigger | Module topic |
|---|---|
| idle_pct > 25% | Fuel-efficient operation |
| harsh_maneuver ≥ 3 | Smooth controls |
| efficiency low for a task_type: avg < 0.83 with n ≥ 5, or avg ≤ 0.90 × prev (both windows n ≥ 3) | Technique for that task type |
| proximity breaches ≥ 2 | Blindspot awareness |
| seatbelt violations ≥ 1 | Seatbelt and cab safety |
| tip_risk events ≥ 1 | Working on slopes |
| cluster_label = 'needs safety coaching' | Scenario simulator pack |

Efficiency = standard_min / actual (see §2). Module IDs per task_type: `TM-TECH-DIG-01`,
`TM-TECH-TRENCH-01`, `TM-TECH-LOAD-01`, `TM-TECH-HAUL-01`, `TM-TECH-GRADE-01`,
`TM-TECH-BACKFILL-01` (seeded in `supabase/seed.sql`); each rec carries
`trigger_metric='efficiency'`, `trigger_value=avg`, and `sim_module_id` (null for now).
Worst gap (site median − avg) first, max 2 open per operator, no duplicate module.

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
