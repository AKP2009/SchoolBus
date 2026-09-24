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

**Auth** (`backend/app/core/auth.py`). Every endpoint except `GET /health` needs a bearer token:
- **People:** the Supabase access token (`session.access_token`). Checked against the project's JWKS
  (ES256/RS256), with `SUPABASE_JWT_SECRET` for legacy HS256, else by Supabase (`GET /auth/v1/user`).
  Role, `operator_id` and `site_id` come from `profiles` (cached 60 s), never from `user_metadata`.
- **Vision service:** `VISION_API_TOKEN` from `backend/.env` (`vision/run.py --token`, default
  `$VISION_API_TOKEN`, read from `vision/.env`; sent as `Authorization: Bearer <token>` on every call). It may call `POST /events`, `GET /machine/{id}/state` and
  `GET /operator/{id}/fatigue` only; anything else is 403.
- Like RLS: operators act on their own rows (events, fatigue, tasks, shifts); managers and admins
  on everything. **Managers only:** `/replay/start`, `/replay/stop`, `/scenario/{name}`,
  `/analytics/cluster`.
- Missing / invalid / expired token → 401 `UNAUTHORIZED`; valid token but not allowed → 403 `FORBIDDEN`
  (also for an account without a `profiles` row).

### `GET /health`
`{ "status": "ok", "models": { "anomaly": "v1", "task_time": "v1", ... } }`

### `POST /predict/task-time`
Request
```json
{ "task_ids": ["T-SH-2026-09-01-M01-D-1"] }
```
Response (also written to `tasks.predicted_p10_min / p50 / p90`, `prediction_factors`)
```json
{ "predictions": [ { "task_id": "T-…", "p10_min": 35.2, "p50_min": 42.0, "p90_min": 55.1,
  "factors": [ { "feature": "rain_mm", "label": "Rain", "impact_min": 8.1 } ],
  "operator_avg_min": 47.5, "expected_efficiency": 0.88 } ] }
```
Predictions come back in request order. `health_score` is the machine's live overall health (running
replay, else `v_machine_health_latest`; without either, the service-based health the model was trained
on). 404 `NOT_FOUND` for an unknown task id; operators may predict only their own tasks (403);
503 `MODEL_NOT_LOADED` without the artifacts.

### `POST /plan/re-evaluate`
Request `{ "shift_id": "SH-…", "reason": "task_overrun", "now": "…" }` (`now` optional: the plan's
clock; default = replay time when the shift's machine is in the running replay, else the wall clock)
Response
```json
{ "shift_id": "SH-…", "fits": ["T-…-3", "T-…-4"], "moved_to_next_shift": ["T-…-5"],
  "new_order": ["T-…-4", "T-…-3"], "explanation": "Rain started; task 5 no longer fits before 18:00." }
```
`POST /plan/accept` with the same `shift_id` applies it.
Extra fields from `ml.inference.plan.re_evaluate_plan` (optional for clients): `triggers`, `available_min`,
`schedule` = `[{ "task_id", "priority", "p50_min", "fits", "start", "end" }]` in planned order (moved tasks last,
`start`/`end` null), `break` = `{ "start", "end", "minutes": 15 }` when the fatigue trigger inserted a break, else null,
`now` (the plan's clock), `fatigue` = `{ "fatigue_level", "ts" }` (the row the fatigue trigger read, or null).

`triggers` = the request's `reason` followed by the triggers that hold now (task past its p90, rain > 2 mm in
the current weather hour, live health < 0.6, fatigue high). The fatigue trigger reads the latest `fatigue_log`
row of the shift's operator: a live row (written in the last 30 min of wall-clock time) wins, else the latest
at or before the plan's clock. Tasks with `delay_reason = 'moved_to_next_shift'` are left out.
Accept applies the last re-evaluation of that shift (kept in memory for 30 min; 409 `NO_PENDING_PLAN` /
`PLAN_EXPIRED` otherwise): tasks that fit get a new `sequence_no` (after the shift's finished tasks) and
`scheduled_start` from `schedule` (the running task keeps its start); moved tasks get `status = 'delayed'`,
`delay_reason = 'moved_to_next_shift'`, `scheduled_start = null`. The shift's operator or a manager may
re-evaluate and accept; 404 `NOT_FOUND` for an unknown shift.

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
Vision polls it every 2 s; on 404 it assumes `moving: true`, and it keeps the last value while the
backend is unreachable (models.md §6 "Backend polling").

### `POST /events` (from vision service and voice)
```json
{ "type": "proximity_breach", "machine_id": "M05", "operator_id": "OP02",
  "ts": "2026-09-23T10:15:03Z", "severity": "critical",
  "distance_m": 2.4, "sector": "rear", "approaching": true,
  "details": { "track_id": 7, "class": "person", "conf": 0.83 } }
```
Types: `proximity_breach`, `blindspot_intrusion`, `fatigue_high`, `phone_use`, `sos`, plus
`fatigue_sample` (per-minute fatigue_log row, not an alert):
```json
{ "type": "fatigue_sample", "operator_id": "OP02", "shift_id": "SH-…", "ts": "…",
  "ear_avg": 0.27, "perclos_60s": 0.08, "yawn_count": 0, "head_down_events": 0,
  "phone_detected": false, "fatigue_score": 0.31, "fatigue_level": "low" }
```
Fatigue alerts from the cab camera have no distance or approaching flag:
```json
{ "type": "fatigue_high", "machine_id": "M05", "operator_id": "OP02", "ts": "…",
  "severity": "critical", "sector": "cab",
  "details": { "shift_id": "SH-…", "reason": "eyes_closed", "eyes_closed_s": 2.07, "machine_moving": true,
               "fatigue_score": 0.41, "fatigue_level": "medium", "perclos_60s": 0.12,
               "yawns_10min": 0, "head_down_10min": 0 } }
{ "type": "phone_use", "machine_id": "M05", "operator_id": "OP02", "ts": "…", "severity": "warning",
  "sector": "cab", "details": { "shift_id": "SH-…", "class": "cell phone", "conf": 0.71, "seen_s": 3.0 } }
```
`fatigue_high` is `warning` when the level becomes high (no `reason`) and `critical` for
`reason: "eyes_closed"` (models.md §5).

Response `{ "stored": true, "alert_id": 123, "event_id": 456 }` (`event_id` = the `safety_events` or
`fatigue_log` id, optional for clients; `alert_id` is null for `fatigue_sample`).

Validation per type (400 `VALIDATION_ERROR`; `ts` must carry a timezone):

| type | needs | severity |
|---|---|---|
| `proximity_breach`, `blindspot_intrusion` | `machine_id`, `distance_m` (0–100), `sector` front/rear/left/right | warning, critical |
| `fatigue_high`, `phone_use` | `machine_id`, `operator_id`; `sector` defaults to `cab` | info, warning, critical |
| `sos` | `operator_id` or `machine_id` | always stored as `emergency` |
| `fatigue_sample` | `operator_id`, `shift_id`, `fatigue_score` 0–1, `fatigue_level`; optional `machine_id` | – |

Unknown machine / operator → 404 `UNKNOWN_MACHINE` / `UNKNOWN_OPERATOR`. An operator's token may post
only for its own `operator_id` (403). A `fatigue_sample` whose `shift_id` isn't in `shifts` (vision's
"today" id) is stored with `shift_id` null. What is written (`backend/app/services/events.py`):
- Alert types: a `safety` WebSocket message first, then the `alerts` row, the `safety_events` row
  (`alert_id` set), then an `alert` message with the row id.

  | type | alert_code | category | source | stage |
  |---|---|---|---|---|
  | proximity_breach | `PROXIMITY_ORANGE` / `PROXIMITY_RED` (warning / critical) | safety | vision | warn |
  | blindspot_intrusion | `BLINDSPOT_ORANGE` / `BLINDSPOT_RED` | safety | vision | warn |
  | fatigue_high | `FATIGUE_HIGH`; `EYES_CLOSED` for `reason: "eyes_closed"` | safety | vision | warn |
  | phone_use | `PHONE_USE` | behaviour | vision | warn |
  | sos | `SOS` (severity emergency) | emergency | operator | escalated |
- One alert per episode: a repeat of the same machine and kind within 30 s updates the open alert
  (`evidence.count`, `min_distance_m`, `last_ts`; severity and code only rise, e.g. ORANGE → RED) and
  pushes `alert` only on a rise. With no event for 30 s the alert is resolved (`stage: "resolved"`,
  `resolved_at`, `alert` message). SOS is never auto-resolved: the manager resolves it.
- `fatigue_high` with `details.fatigue_level` / `fatigue_score` also pushes a `fatigue` message.
- `fatigue_sample`: `fatigue_log` row and a `fatigue` message to the machine of the sample
  (`machine_id`, else the shift's machine, else parsed from the `SH-<date>-<machine>-<D|N>` id).

### `GET /operator/{operator_id}/fatigue` (for the vision service and voice assistant)
```json
{ "operator_id": "OP02", "ts": "2026-09-24T04:10:00Z", "shift_id": "SH-…", "fatigue_level": "medium",
  "fatigue_score": 0.44, "perclos_60s": 0.12, "stale": false, "age_min": 0.8 }
```
The latest `fatigue_log` row of the operator. `stale` = older than 10 min of wall-clock time: treat the
level as unknown (the loaded history is weeks old). 404 `NOT_FOUND` with no row. Vision token, the
operator themselves, or a manager.

### `POST /chat`
Request `{ "session_id": "uuid", "operator_id": "OP02", "message": "What does E-360 mean?", "language": "en" }`
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
Managers only. `week_start` must be a Monday (400). Uses the week's shifts, tasks and safety events from
Supabase and telemetry from `data/output/telemetry.parquet` when present, else Supabase (`telemetry_source`
in the response). 404 `NOT_FOUND` when the week has no shifts. Upserts on (entity_type, entity_id,
week_start); `verified_by` / `verified_at` are kept. Takes 10–30 s (model 1 runs on every telemetry minute).

### `POST /replay/start`
`{ "machine_ids": ["M04","M05"], "from": "2026-08-19T15:15:00Z", "speed": 10 }` (the demo window, `demo_script.md` "Demo data")
`POST /replay/stop`, `GET /replay/status`
Supabase holds 2026-08-16 → 08-29 (shifts 00:30–07:30 and 12:30–20:30 UTC). Starting again
replaces the running replay. 404 `UNKNOWN_MACHINE`; 503 `NO_TELEMETRY_SOURCE` when neither
Supabase nor `data/output/telemetry.parquet` has the data.

### `POST /scenario/{name}` (demo panel only)
Names: `overheating`, `hydraulic_leak`, `fatigue`, `proximity`, `tip_risk`, `seatbelt`, `sos`.
Body `{ "machine_id": "M05" }`. Injects a scripted signal sequence into the replay stream.
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

Auth: `?token=<Supabase access token>` (browsers can't set WebSocket headers) or a bearer header; the
vision token works too. A bad or missing token is accepted and closed at once with code 4401 (4403 when
not allowed). `safety` also carries `severity`.

Server behaviour (`backend/app/ws.py`): pings get no reply; any client message counts as a sign
of life, and a client silent for 60 s (3 missed pings) is dropped. `telemetry` is sent for every
replayed row (scenario rows included, 5 s apart during `seatbelt`), without `anomaly_label` /
`anomaly_type`. `health` is sent once per data minute. `alert` is sent after the `alerts` row is
written, on open, stage change, severity rise and resolve (`stage: "resolved"`), with the row's
`id`; `id` is negative only if the database write failed.

## Shapes fixed during scaffolding
These were not specified above. They are defined in `backend/app/schemas/` and can be changed there:
- `POST /plan/accept` → `{ "shift_id": "SH-…", "applied": true }`
- `POST /incidents/transcribe` request `{ "transcript": "…", "operator_id": "OP02", "machine_id": "M05" | null }`
- `POST /analytics/cluster` → `{ "week_start": "2026-09-14", "rows_written": 42, "summary": "…", "telemetry_source": "parquet" }`
- `POST /replay/start`, `POST /replay/stop`, `GET /replay/status` → `{ "running": true, "machine_ids": [...], "speed": 1, "replay_ts": "…" | null,
  "source": "supabase" | "parquet" | null, "scenarios": { "M05": "overheating" } }`
- `POST /scenario/{name}` → `{ "scenario": "overheating", "machine_id": "M05", "started": true, "start_ts": "…", "duration_min": 26 }`
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
| `alerts_open.json` | `v_open_alerts` rows at `now` (rule-engine + vision alerts through the backend code; escalated overheating on M05) |
| `safety_events.json`, `shifts.json`, `fatigue.json`, `incidents.json` | table rows up to `now` |
| `fleet.json` | machines + latest position/health/open alerts + `geofences` (four zones placed from S1's GPS extent: none are loaded yet) |
| `maintenance_predictions.json` | latest per machine (XGBoost) + `risk_band`, factor `label` |
| `clusters.json` | `fleet_metrics_weekly` for the last full week + `pca_points.json` for S1 + display names |
| `training.json`, `chat.json`, `plan.json` | modules (TM-SIM-01 from `backend/kb/scenarios.json`), records, §10 recommendations; cached answers from the KB sections `rag_eval.json` names; `/plan/re-evaluate` |
| `telemetry_stream.json` | `{machine_id, from, to, messages: [{at_s, kind, data}]}`: 120 min of `/stream` messages, replayed by the mock WebSocket |

The stream is the backend `ReplayEngine` run offline (in-memory store) with `overheating` triggered on
M05 at 16:54:30. Vision events in the shift become alerts through the backend's own
`app.services.events.alert_spec` (e.g. `BLINDSPOT_RED` "Person 2.4 m behind the machine — blind spot") and
resolve `EXPIRE_S` (30 s) after the event, as the live service does; so at `now` the open alerts are the
escalated `COOLANT_CRITICAL` and the `COOLANT_HIGH` warning on M05.

**Known limit:** the maintenance model's baselines need ~21 days of telemetry; the 14-day load gives
none, so every failure probability from Supabase is ~0 (`--source files` reads 30 days; the fleet
is still all "low" at this moment: the next failure, M08, is 2.5 days away).

Web switch: `VITE_USE_MOCKS=false` moves every hook in `web/src/data/hooks.ts` to Supabase + FastAPI.
Mock-mode QA URL parameters: `?state=loading|empty|error|offline`, `?t=<data s>`, `?speed=0`,
`?as=OP02` (sign the cab in), `?ack=<alert ids>`, `?tab=practice|ask` (training).

## Errors
`{ "error": { "code": "MODEL_NOT_LOADED", "message": "Task time model is not loaded. Run ml/02_task_time.ipynb and restart." } }`
HTTP 400 validation (`VALIDATION_ERROR`), 401 auth (`UNAUTHORIZED`), 403 not allowed (`FORBIDDEN`),
404 not found (`NOT_FOUND`, `UNKNOWN_MACHINE`, `UNKNOWN_OPERATOR`), 405 `METHOD_NOT_ALLOWED`,
409 state conflicts (`REPLAY_NOT_RUNNING`, `NO_PENDING_PLAN`, `PLAN_EXPIRED`, …), 501 not built yet
(`NOT_IMPLEMENTED`), 503 model/LLM unavailable (`MODEL_NOT_LOADED`, `AUTH_UNAVAILABLE`), 500 `INTERNAL_ERROR`.
Every error, including unknown routes and framework validation, uses this shape (`backend/app/core/errors.py`).
