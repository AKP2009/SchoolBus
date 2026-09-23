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
