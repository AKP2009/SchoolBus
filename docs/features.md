# Features

Everything we are implementing, grouped the way the problem statement groups it.
Each feature lists **what it does**, **how it works**, **data it uses**, and **priority**.

- **P0** — must work live in the demo
- **P1** — build once all P0 works
- **P2** — stretch; mention in the pitch as future scope if not built

Two users: the **Operator** (tablet in the cab, `/operator`) and the **Site Manager** (web, `/manager`).

---

## 1. Daily task dashboard

### 1.1 Today's tasks — P0
**What:** The operator sees their tasks for the shift in order: task type, material, quantity,
location, and a predicted time range ("42 min, usually 35–55").
**How:** Tasks come from the `tasks` table. Predictions come from the task time model (§5).
The card shows the two biggest factors behind the estimate ("rain +8 min, rock +6 min").
**Data:** `tasks`, `weather`, `operators`, `machines`. **Operator actions:** start task, complete task, flag delay with a reason.

### 1.2 Shift handover intelligence — P1
**What:** At login the operator gets a short brief of the previous shift on this machine:
open issues, alerts, fuel level, unfinished tasks, anything to check before starting.
**How:** Backend collects the last shift's `shift_logs.handover_notes`, alerts, safety events and
unfinished tasks, and an LLM writes a 5-line summary. Stored in `shifts.handover_summary`.
**Data:** `shifts`, `alerts`, `safety_events`, `tasks`.

### 1.3 Previous operator logs — P1
**What:** A timeline of the last 7 days on this machine: who operated it, issues, incidents.
**How:** Simple query view, no ML.

### 1.4 Adaptive mission control (plan re-evaluation) — P1
**What:** When the plan stops being realistic — a task overruns, rain starts, the machine's
health drops, or the operator's fatigue is high — the system re-estimates the remaining tasks
and suggests a new order or tells the manager which tasks won't fit in the shift.
**How:** Trigger → re-run task time model on remaining tasks with current conditions → greedy
re-order (highest priority first, respect dependencies, fit inside shift end) → show a diff
("Task 5 moves to next shift"). Operator or manager accepts. OR-Tools CP-SAT is the P2 version.

---

## 2. Safety features

All safety alerts follow one hierarchy (see `design.md` §Alerts): **info → warning → critical → emergency**.

### 2.1 Seatbelt compliance — P0
**What:** Warns if the machine moves or the engine is under load while the seatbelt is unfastened.
Logs every violation.
**How:** Rule on telemetry: `seatbelt_fastened = false AND (ground_speed_kmh > 0.5 OR engine_load_pct > 20)`
for more than 5 s → warning; more than 30 s → critical + manager notified.
Seatbelt switch is an assumed sensor; the cab camera is a backup check (P2).

### 2.2 Proximity and blindspot detection — P0
**What:** Detects people and vehicles around the machine, estimates distance, and highlights
which sector (front, rear, left, right) they are in. Warns before they reach the danger zone.
**How:** YOLO person/vehicle detection + ByteTrack tracking on camera feeds. Distance from
bounding-box height fused with an assumed ultrasonic/radar reading. Zones: red < 3 m,
orange 3–7 m, clear > 7 m. Tracking tells us if the person is **approaching**, which raises severity.
In the demo, a laptop webcam plays one camera sector.

### 2.3 Fatigue-aware voice assistant ("shift personality") — P0
**What:** A cab camera watches for eye closure, yawning, head nodding and phone use.
The voice assistant changes its behaviour with the operator's fatigue level: quiet and brief
when alert, more frequent check-ins when tired, a break suggestion and supervisor notification when fatigue is high.
**How:** MediaPipe Face Mesh → eye aspect ratio, PERCLOS over 60 s, yawns, head pose.
YOLO's pretrained phone class for phone use. Fatigue level combines camera signals with
hours into shift and night shift. See `models.md` §5.
**Voice commands (P1):** "What's my next task?", "Report an incident", "How much fuel?",
"Why is the engine warning on?", "Call supervisor", "SOS". Hindi, Tamil and English (P1).

### 2.4 Tip-over / slope warning — P0
**What:** Warns when the machine is operating on a dangerous slope or tilting.
**How:** Rule on assumed IMU `pitch_deg` / `roll_deg`: > 15° warning, > 25° critical.
Thresholds differ by machine type in production; ours are stated assumptions.

### 2.5 Geofencing — P1
**What:** Manager draws zones on the site map: no-go (power lines, trenches), pedestrian zones,
speed-limited zones. Operator is warned on entry.
**How:** GeoJSON polygons in `geofences`; point-in-polygon check on each telemetry GPS point.

### 2.6 Incident logging — P0
**What:** Operator reports an incident by form or voice in under 30 seconds. Alerts can
auto-create a draft incident. Works offline.
**How:** `incidents` table, optional photo upload to Storage, LLM turns voice transcript into a
structured report. Each record has a `client_id` so offline sync never duplicates it.

### 2.7 SOS / emergency — P1
**What:** One large button (and voice "SOS") sends machine location, state and operator ID to
the site manager as an emergency alert.

### 2.8 Working conditions awareness — P1
**What:** Weather, visibility and dust are shown on the dashboard and feed into alert thresholds
(e.g. proximity warning distance increases in low visibility) and time estimates.

---

## 3. Machine health and alerts

One telemetry pipeline, one anomaly engine, four features on top of it.

### 3.1 Internal warnings (rule engine) — P0
**What:** Instant warnings for known limits: coolant temperature, hydraulic oil temperature,
oil pressure, battery voltage, fault codes.
**How:** Threshold rules with hysteresis (see `models.md` §Rule engine).

### 3.2 Graded response instead of force stop — P0
**What:** When a condition is dangerous, the system escalates in stages rather than cutting power:
1. **Warn** the operator (sound + banner)
2. **Derate** — recommend or simulate reduced power
3. **Recommend safe shutdown** — "Lower the bucket, move to level ground, then shut down"
4. **Escalate** — notify the site manager if the condition persists

**Why:** An automatic stop can be more dangerous than the fault if the machine is lifting,
travelling or on a slope. This is a deliberate design decision to explain to the judges.

### 3.3 Unusual behaviour detection — P0
**What:** Flags machine behaviour that doesn't match normal patterns, even when no single
threshold is crossed: excessive idling, high RPM at low load, abnormal fuel burn, harsh
operation, unusual pressure-temperature combinations. Also tells a sensor glitch apart from a real fault.
**How:** Isolation Forest on rolling-window features + behaviour rules. LSTM autoencoder is P2.

### 3.4 Predictive maintenance — P1
**What:** "Hydraulics on M04: 72% chance of failure in the next 48 engine hours."
Ranked risk list for the manager, a simple warning for the operator.
**How:** XGBoost classifier on aggregated telemetry trends. See `models.md` §3.

### 3.5 Digital twin health view — P1
**What:** A simple drawing of the machine with subsystems (engine, hydraulics, cooling,
electrical, brakes/undercarriage) coloured by health. Tap a part to see its signals and trend.
**How:** No new model. Health score per subsystem = combination of rule states, anomaly score
and failure probability. Stored in `machine_health_snapshots`. SVG in the frontend.

### 3.6 External damage check by camera — P2
**What:** Walk-around photo check for visible damage or obstacles.
**How:** YOLO fine-tuned on a vehicle-damage dataset. Public data for construction machines is
scarce, so this stays a stretch goal.

---

## 4. Operator training hub

### 4.1 RAG chatbot — P1
**What:** Operator asks questions about operation, safety and troubleshooting and gets answers
with sources. Knows our fault codes ("What does E-360 mean?").
**How:** Documents chunked and embedded into pgvector; top chunks + question go to an LLM.
See `models.md` §7.

### 4.2 Performance-based recommendations — P1
**What:** "You idled 31% last week. This 8-minute module on fuel-efficient operation can help."
**How:** Rules map weak metrics (idle %, harsh events, slow task types, safety events) to training
modules. Uses clustering output. Stored in `training_recommendations`.

### 4.3 Learning library — P1
**What:** Videos, documents, quizzes by topic and language. Tracks completion and scores.
Download for offline use.

### 4.4 Instructor booking — P2
**What:** Book a session with an instructor from available slots.

### 4.5 Scenario simulator — P1
**What:** "What would you do?" scenarios: an image or short clip of a situation (person in
blindspot, overheating while lifting, slope warning) with choices, scored with explanations.
Cheaper than a 3D simulator and still interactive. A 3D simulator is P2 future scope.
Content: `backend/kb/scenarios.json`, 12 scenarios in the `training_modules.scenario` shape
(`{"steps": [{prompt, choices, answer}]}`, `answer` = 0-based index, as in the generator seed), with
extra per-step fields `id`, `title`, `image_prompt`, `explanation` and `module_id` (the module to take
next). Meant to replace the 3-step placeholder in `TM-SIM-01.scenario` (not loaded yet).

---

## 5. Task time estimation — P0
**What:** Predicts how long each task will take with a range, using task details, operator skill,
machine health, weather and time of shift. Shows the operator's previous average and expected
efficiency, and suggests a training module when they are consistently slower.
**How:** LightGBM quantile regression (P10 / P50 / P90). SHAP values explain each estimate.
See `models.md` §2.

---

## 6. Site manager dashboard

### 6.1 Live fleet map — P0
Machines on a Leaflet map coloured by status, geofences drawn, click a machine for its live view.

### 6.2 Alerts and emergencies feed — P0
Live list from Supabase Realtime, filter by severity, acknowledge and resolve.

### 6.3 Fleet clustering and outlier ranking — P1
**What:** Groups machines and operators by behaviour, ranks efficiency, flags outliers for the
manager to verify. **How:** K-Means + outlier detection on weekly metrics. See `models.md` §4.

### 6.4 Maintenance risk board — P1
Machines ranked by failure probability with likely component and top reasons.

### 6.5 Safety analytics — P1
Charts such as "safety events by hour of shift" — built from the fatigue/safety patterns in our data.

---

## 7. Platform features

### 7.1 Offline-first operator app — P1
**What:** Tasks, handover and training content cached; incidents, task updates and training
progress queued while offline and synced when connectivity returns.
**How:** PWA + IndexedDB queue + idempotent upserts by `client_id`. Full CRDT sync (Yjs) is
the production approach and P2 for us.

### 7.2 Live telemetry replay — P0
**What:** Replays synthetic telemetry as if it were live, at 1× or faster, so the demo behaves
like a real system. **How:** Backend reads telemetry and streams over WebSocket, running the
alert engine on each window.

### 7.3 Demo scenario triggers — P0
Buttons (hidden in a demo panel) that inject a scripted event: overheating, hydraulic leak,
fatigue, proximity, tip risk, offline. The demo never depends on luck.

### 7.4 Multilingual UI and voice — P1
English, Hindi, Tamil for UI strings and voice.

---

## Priority summary

| Priority | Features |
|---|---|
| **P0** | Today's tasks · seatbelt · proximity/blindspot · fatigue detection · tip-over · incident logging · internal warnings · graded response · unusual behaviour · task time estimation · fleet map · alerts feed · telemetry replay · demo triggers |
| **P1** | Shift handover · operator logs · adaptive mission control · voice commands · geofencing · SOS · working conditions · predictive maintenance · digital twin · RAG chatbot · recommendations · library · scenario simulator · clustering · maintenance board · safety analytics · offline-first · multilingual |
| **P2** | External damage check · instructor booking · LSTM autoencoder · CP-SAT scheduler · full CRDT sync · 3D simulator · seatbelt camera check |
