# API contract

Agree on this before building so frontend and backend work in parallel. The frontend builds
against mock JSON with exactly these shapes (`web/src/mocks/`) until the backend is ready.

**Rule of thumb:** plain reads and simple writes go straight to Supabase from the web app.
Anything that needs a model, an LLM, the service role, or the live stream goes through FastAPI.

## Supabase direct (web app, under RLS)

| Screen | Operation |
|---|---|
| Today's tasks | `from('tasks').select('*').eq('operator_id', me).eq('task_date', today).order('sequence_no')` |
| Start / complete task | `update({ status, actual_start / actual_end })` |
| Handover | `from('shifts').select('handover_summary, …').eq('machine_id', m).order('start_time', desc).limit(1)` |
| Machine logs (7 days) | `shifts` + `alerts` + `incidents` for the machine |
| Alerts feed | `from('v_open_alerts')` + Realtime |
| Acknowledge alert | `alerts.update({ acknowledged_by, acknowledged_at })` |
| Report incident | `incidents.upsert(row, { onConflict: 'client_id' })` |
| Fleet map | `machines` + `v_machine_health_latest` + `geofences` |
| Maintenance board | latest `maintenance_predictions` per machine |
| Clusters | `fleet_metrics_weekly` for the latest week |
| Training | `training_modules`, `training_records`, `training_recommendations` |

## FastAPI (base `VITE_API_URL`, JSON, auth header `Authorization: Bearer <supabase JWT>`)

### `GET /health`
`{ "status": "ok", "models": { "anomaly": "v1", "task_time": "v1", ... } }`

### `POST /predict/task-time`
Request
```json
{ "task_ids": ["T-SH-2026-09-01-M01-D-1"] }
```
Response (also written to `tasks`)
```json
{ "predictions": [ { "task_id": "T-…", "p10_min": 35.2, "p50_min": 42.0, "p90_min": 55.1,
  "factors": [ { "feature": "rain_mm", "label": "Rain", "impact_min": 8.1 } ],
  "operator_avg_min": 47.5, "expected_efficiency": 0.88 } ] }
```

### `POST /plan/re-evaluate`
Request `{ "shift_id": "SH-…", "reason": "task_overrun" }`
Response
```json
{ "shift_id": "SH-…", "fits": ["T-…-3", "T-…-4"], "moved_to_next_shift": ["T-…-5"],
  "new_order": ["T-…-4", "T-…-3"], "explanation": "Rain started; task 5 no longer fits before 18:00." }
```
`POST /plan/accept` with the same `shift_id` applies it.
Extra fields from `ml.inference.plan.re_evaluate_plan` (optional for clients): `triggers`, `available_min`,
`schedule` = `[{ "task_id", "priority", "p50_min", "fits", "start", "end" }]` in planned order (moved tasks last,
`start`/`end` null), `break` = `{ "start", "end", "minutes": 15 }` when the fatigue trigger inserted a break, else null.

### `GET /machine/{machine_id}/health`
```json
{ "machine_id": "M04", "ts": "…", "overall": 0.58,
  "subsystems": { "engine": 0.9, "cooling": 0.85, "hydraulics": 0.41, "electrical": 0.95, "undercarriage": 0.88 },
  "anomaly_score": 0.71, "failure_probability": 0.72, "likely_component": "hydraulics" }
```
Extra fields from `ml.inference.health.compute_health` (optional for clients): `band` (`green` / `orange` /
`red` for overall), `subsystem_bands`, `reasons` = `[{ "subsystem", "source": "rule" | "anomaly" |
"failure_probability", "penalty", "text" }]`, largest penalty first. Any of `anomaly_score`,
`failure_probability`, `likely_component` is null when that input is missing.
Served from the running replay (updated every data minute); without a replay, from the latest
`v_machine_health_latest` row; 404 `NOT_FOUND` when neither exists.

### `GET /machine/{machine_id}/state` (for the vision service)
```json
{ "machine_id": "M04", "moving": true, "ground_speed_kmh": 3.2, "ts": "2026-08-20T02:02:00Z" }
```
From the latest row of the replay stream (scripted scenario rows included). `moving` =
`ground_speed_kmh > 0.5` (`moving_kmh` in `thresholds.yaml`, the same limit as the SEATBELT rule).
`ts` is replay (data) time. 404 `NOT_FOUND` when the machine isn't in the running replay.

### `POST /events` (from vision service and voice)
```json
{ "type": "proximity_breach", "machine_id": "M04", "operator_id": "OP03",
  "ts": "2026-09-23T10:15:03Z", "severity": "critical",
  "distance_m": 2.4, "sector": "rear", "approaching": true,
  "details": { "track_id": 7, "class": "person", "conf": 0.83 } }
```
Types: `proximity_breach`, `blindspot_intrusion`, `fatigue_high`, `phone_use`, `sos`, plus
`fatigue_sample` (per-minute fatigue_log row, not an alert):
```json
{ "type": "fatigue_sample", "operator_id": "OP03", "shift_id": "SH-…", "ts": "…",
  "ear_avg": 0.27, "perclos_60s": 0.08, "yawn_count": 0, "head_down_events": 0,
  "phone_detected": false, "fatigue_score": 0.31, "fatigue_level": "low" }
```
Fatigue alerts from the cab camera have no distance or approaching flag:
```json
{ "type": "fatigue_high", "machine_id": "M04", "operator_id": "OP03", "ts": "…",
  "severity": "critical", "sector": "cab",
  "details": { "shift_id": "SH-…", "reason": "eyes_closed", "eyes_closed_s": 2.07, "machine_moving": true,
               "fatigue_score": 0.41, "fatigue_level": "medium", "perclos_60s": 0.12,
               "yawns_10min": 0, "head_down_10min": 0 } }
{ "type": "phone_use", "machine_id": "M04", "operator_id": "OP03", "ts": "…", "severity": "warning",
  "sector": "cab", "details": { "shift_id": "SH-…", "class": "cell phone", "conf": 0.71, "seen_s": 3.0 } }
```
`fatigue_high` is `warning` when the level becomes high (no `reason`) and `critical` for
`reason: "eyes_closed"` (models.md §5).

Response `{ "stored": true, "alert_id": 123 }`

### `POST /chat`
Request `{ "session_id": "uuid", "operator_id": "OP03", "message": "What does E-360 mean?", "language": "en" }`
Response
```json
{ "answer": "E-360 means low hydraulic oil level. Stop work, lower the attachment…",
  "sources": [ { "document_id": 4, "title": "Fault code reference", "chunk_index": 12 } ] }
```

### `POST /voice/command`
Multipart audio (`audio/webm`) + `operator_id`, `machine_id`.
Response `{ "transcript": "…", "intent": "next_task", "reply_text": "…", "reply_audio_url": "/audio/…wav", "action": { … } }`

### `POST /handover/{shift_id}` → `{ "summary": "…" }` (also saved to `shifts`)

### `POST /incidents/transcribe`
Voice transcript → structured incident draft `{ incident_type, severity, description, injury }`
for the operator to confirm.

### `POST /analytics/cluster?week_start=2026-09-14` → writes `fleet_metrics_weekly`, returns summary.

### `POST /replay/start`
`{ "machine_ids": ["M01","M04"], "from": "2026-08-20T01:30:00Z", "speed": 1 }`
`POST /replay/stop`, `GET /replay/status`
Supabase holds 2026-08-16 → 08-29 (shifts 00:30–07:30 and 12:30–20:30 UTC). Starting again
replaces the running replay. 404 `UNKNOWN_MACHINE`; 503 `NO_TELEMETRY_SOURCE` when neither
Supabase nor `data/output/telemetry.parquet` has the data.

### `POST /scenario/{name}` (demo panel only)
Names: `overheating`, `hydraulic_leak`, `fatigue`, `proximity`, `tip_risk`, `seatbelt`, `sos`.
Body `{ "machine_id": "M04" }`. Injects a scripted signal sequence into the replay stream.
Built into the replay: `overheating`, `hydraulic_leak`, `tip_risk`, `seatbelt` (timings in
`backend/app/replay/scenarios.py`). `fatigue`, `proximity`, `sos` come from the vision service /
voice via `POST /events` and return 501 here. 409 `REPLAY_NOT_RUNNING`, `MACHINE_NOT_IN_REPLAY`,
`SCENARIO_RUNNING` (one scenario per machine at a time).

## WebSocket `GET /stream/{machine_id}`
Server → client messages, one JSON per line:
```json
{ "kind": "telemetry", "data": { "ts": "…", "engine_rpm": 1780, "coolant_temp_c": 91.2, "…": "…" } }
{ "kind": "alert", "data": { "id": 123, "alert_code": "HYD_OIL_HIGH", "severity": "warning",
  "stage": "warn", "title": "Hydraulic oil hot — 94 °C", "recommended_action": "Switch to economy mode" } }
{ "kind": "safety", "data": { "type": "proximity_breach", "distance_m": 2.4, "sector": "rear", "approaching": true } }
{ "kind": "health", "data": { "overall": 0.58, "subsystems": { … } } }
{ "kind": "fatigue", "data": { "fatigue_level": "medium", "fatigue_score": 0.44 } }
```
Client → server: `{ "kind": "ping" }` every 20 s.

Server behaviour (`backend/app/ws.py`): pings get no reply; any client message counts as a sign
of life, and a client silent for 60 s (3 missed pings) is dropped. `telemetry` is sent for every
replayed row (scenario rows included, 5 s apart during `seatbelt`), without `anomaly_label` /
`anomaly_type`. `health` is sent once per data minute. `alert` is sent after the `alerts` row is
written, on open, stage change, severity rise and resolve (`stage: "resolved"`), with the row's
`id`; `id` is negative only if the database write failed.

## Shapes fixed during scaffolding
These were not specified above. They are defined in `backend/app/schemas/` and can be changed there:
- `POST /plan/accept` → `{ "shift_id": "SH-…", "applied": true }`
- `POST /incidents/transcribe` request `{ "transcript": "…", "operator_id": "OP03", "machine_id": "M04" | null }`
- `POST /analytics/cluster` → `{ "week_start": "2026-09-14", "rows_written": 42, "summary": "…" }`
- `POST /replay/start`, `POST /replay/stop`, `GET /replay/status` → `{ "running": true, "machine_ids": [...], "speed": 1, "replay_ts": "…" | null,
  "source": "supabase" | "parquet" | null, "scenarios": { "M04": "overheating" } }`
- `POST /scenario/{name}` → `{ "scenario": "overheating", "machine_id": "M04", "started": true, "start_ts": "…", "duration_min": 26 }`
  (`start_ts` is replay time)
- `POST /events` accepts only the 5 alert types listed above plus `fatigue_sample`; any other `type` is a 400.

## Mocks (`web/src/mocks/`, built by `scripts/build_mocks.py`)
`python scripts/build_mocks.py` reads the loaded Supabase data (`DATABASE_URL` in `data/.env`);
`--source files` reads `data/output/` cut to the same 14-day window (same rows). Demo world: OP02
(Ganesh Nair, name from `operators`) on the dozer M05, night shift `SH-2026-08-19-M05-N`; the stream
covers 15:15–17:15 UTC and the static files are the state at 17:15 (`world.now`). `_meta.json` says
where each file came from; `anomaly_label`, `anomaly_type` and `personality` are never written.

| File | Shape |
|---|---|
| `tasks.json`, `task_predictions.json` | `tasks` rows with p10/p50/p90 + factors (LightGBM + SHAP); `/predict/task-time` response |
| `handover.json`, `machine_logs.json` | previous shift + 5-line brief (template until the LLM is wired); 7 days of shifts with rule-engine alerts |
| `machine_health.json`, `machine_signals.json`, `machine_live.json` | `/machine/{id}/health` for all 12 machines; 60-min signals; last telemetry row |
| `alerts_open.json` | `v_open_alerts` rows: rule-engine alerts from the backend replay engine (escalated overheating on M05) + vision alerts |
| `safety_events.json`, `shifts.json`, `fatigue.json`, `incidents.json` | table rows up to `now` |
| `fleet.json` | machines + latest position/health/open alerts + `geofences` (four zones placed from S1's GPS extent: none are loaded yet) |
| `maintenance_predictions.json` | latest per machine (XGBoost) + `risk_band`, factor `label` |
| `clusters.json` | `fleet_metrics_weekly` for the last full week + `pca_points.json` for S1 + display names |
| `training.json`, `chat.json`, `plan.json` | modules (TM-SIM-01 from `backend/kb/scenarios.json`), records, §10 recommendations; cached answers from the KB sections `rag_eval.json` names; `/plan/re-evaluate` |
| `telemetry_stream.json` | `{machine_id, from, to, messages: [{at_s, kind, data}]}`: 120 min of `/stream` messages, replayed by the mock WebSocket |

The stream is the backend `ReplayEngine` run offline (in-memory store) with `overheating` triggered on
M05 at 16:54:30. Vision events become alerts the way `/events` will: `PROXIMITY_RED` (critical, "Person
behind you — 2.4 m"), `PROXIMITY_ORANGE`, `FATIGUE_HIGH` (warning), `PHONE_USE`, `SOS`, source `vision`;
they stay open until the manager resolves them (nothing clears them like hysteresis).

**Known limit:** the maintenance model's baselines need ~21 days of telemetry; the 14-day load gives
none, so every failure probability from Supabase is ~0 (`--source files` reads 30 days; the fleet
is still all "low" at this moment: the next failure, M08, is 2.5 days away).

Web switch: `VITE_USE_MOCKS=false` moves every hook in `web/src/data/hooks.ts` to Supabase + FastAPI.
Mock-mode QA URL parameters: `?state=loading|empty|error|offline`, `?t=<data s>`, `?speed=0`,
`?as=OP02` (sign the cab in), `?ack=<alert ids>`, `?tab=practice|ask` (training).

## Errors
`{ "error": { "code": "MODEL_NOT_LOADED", "message": "Task time model is not loaded. Run ml/02 and restart." } }`
HTTP 400 validation (`VALIDATION_ERROR`), 401 auth, 404 not found, 501 not built yet (`NOT_IMPLEMENTED`),
503 model/LLM unavailable.
