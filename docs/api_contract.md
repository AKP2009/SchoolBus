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
`{ "status": "ok", "models": { "anomaly": "v1", "task_time": "v1", ..., "llm": "gemini-3.5-flash-lite" } }`
(`llm` = `LLM_MODEL`, or `"not_configured"` when `LLM_API_KEY` is missing).

All JSON responses are `application/json; charset=utf-8`: without the charset, Windows PowerShell 5.1
(`Invoke-RestMethod`) decodes them as ISO-8859-1 and "—" in alert titles shows as "â€”".

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
{ "answer": "Lower the attachment slowly, stop and shut down… (Source: Fault codes)",
  "sources": [ { "document_id": 1, "title": "Fault codes", "chunk_index": 4 } ] }
```
Operators chat only as themselves (403). Non-English questions (`language` or Devanagari/Tamil script) are
translated to English for retrieval and answered in that language. `sources` lists only the chunks the
answer used (empty when the answer is "not in my manuals"). Both turns are saved in `chat_messages`
(same `session_id`, user turn first by `created_at`). 503 `LLM_UNAVAILABLE` (no `LLM_API_KEY`),
`LLM_RATE_LIMITED` (daily free-tier quota used up) or `LLM_ERROR`.

### `POST /voice/command`
Multipart audio (`audio/webm`) + `operator_id`, `machine_id`.
Response `{ "transcript": "…", "intent": "next_task", "reply_text": "…", "reply_audio_url": "/audio/…wav", "action": { … } }`

### `POST /handover/{shift_id}` → `{ "summary": "…" }` (also saved to `shifts`)
Summary of that shift for the next operator on the machine, 5 lines (`Machine:`, `Open issues:`, `Check before
starting:`, `Unfinished work:`, `Fuel:`), saved to `shifts.handover_summary` + `handover_generated_at`.
Managers, the shift's operator, or operators of the same site. 404 unknown shift, 503 as for `/chat`. The
scheduler also writes it when a shift ends (supabase.md §10).

### `POST /incidents/transcribe`
Voice transcript → structured incident draft `{ incident_type, severity, description, injury }`
for the operator to confirm. Nothing is saved: the web app upserts the confirmed row (`reported_via: 'voice'`,
`voice_transcript`). Someone hurt → `injury: true` and severity at least `critical`. 400 empty transcript,
403 other operator, 503 as for `/chat`.

### `POST /training/recommendations`
Runs the recommender rules now (models.md §10), as the daily job does. Request
`{ "operator_id": "OP02" | null, "as_of": "…" | null }` (null operator = everyone, managers only; operators only
for themselves; null `as_of` = end of the latest shift in the database). Response
`{ "results": [ { "operator_id": "OP02", "as_of": "…", "triggers": [ { "metric": "tip_risk", "value": 1,
"modules": ["TM-SAFE-03"] } ], "created": [ { "id": 7, "module_id": "TM-SAFE-03", "reason": "…",
"trigger_metric": "tip_risk", "trigger_value": 1 } ] } ] }`. The web app reads `training_recommendations`.

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

## Errors
`{ "error": { "code": "MODEL_NOT_LOADED", "message": "Task time model is not loaded. Run ml/02_task_time.ipynb and restart." } }`
HTTP 400 validation (`VALIDATION_ERROR`), 401 auth (`UNAUTHORIZED`), 403 not allowed (`FORBIDDEN`),
404 not found (`NOT_FOUND`, `UNKNOWN_MACHINE`, `UNKNOWN_OPERATOR`), 405 `METHOD_NOT_ALLOWED`,
409 state conflicts (`REPLAY_NOT_RUNNING`, `NO_PENDING_PLAN`, `PLAN_EXPIRED`, …), 501 not built yet
(`NOT_IMPLEMENTED`), 503 model/LLM unavailable (`MODEL_NOT_LOADED`, `AUTH_UNAVAILABLE`, `LLM_UNAVAILABLE`,
`LLM_RATE_LIMITED`, `LLM_ERROR`), 500 `INTERNAL_ERROR`.
Every error, including unknown routes and framework validation, uses this shape (`backend/app/core/errors.py`).
