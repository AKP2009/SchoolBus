# Team roles

Four roles. Replace **A–D** with names. If the team is 3, merge B into A and C (A takes vision,
C takes voice). If the team is 5, split D into an operator-app dev and a manager-dashboard dev.

| | Role | One-line mission | Critical deliverable |
|---|---|---|---|
| **A** | Data + ML | Make the data, make the models, prove they work | Generator + 4 trained models with result sheets |
| **B** | Vision + Voice | Make the machine see and talk | Webcam proximity + fatigue detection posting live events |
| **C** | Backend + AI | Connect everything, make it feel live | Replay engine + alert engine + API + RAG |
| **D** | Frontend + Design | Make it usable in a cab and impressive on a projector | Operator PWA + manager dashboard |

**Ownership rule:** each person owns their folder and merges their own PRs. Anyone touching
someone else's folder asks first. The schema (`supabase/migrations/`) is owned by C; changes
need a message in the team chat.

---

## Person A — Data + ML

**Owns:** `data/generator/`, `ml/`, `docs/synthetic_data.md`, `docs/models.md` (§1–4, 11, 12)

**You are on the critical path.** Everyone else is blocked on realistic data, so your first
goal is a small sample fast, then the full dataset.

### Phase 0 — Design
- Read `synthetic_data.md` and `schema.md`; confirm every column you'll generate exists in the schema.
- Agree the hidden rules with the team (they become pitch material: "we built these rules, and the models found them").

### Phase 1 — Generator
**How:**
1. Set up `data/generator/` with `generate.py`, `config.yaml`, one module per table
   (`master.py`, `weather.py`, `tasks.py`, `telemetry.py`, `events.py`). Use numpy's
   `default_rng(seed)` everywhere so runs are reproducible.
2. **Within the first few hours:** generate 1 day, 2 machines, no anomalies → share
   `sample/` CSVs so C and D can start.
3. Build tasks with the duration formula, then telemetry by walking each shift's task timeline
   minute by minute (state machine: off → idle → working ↔ idle → travelling → off).
4. Add failures and drift, then injected anomalies with labels.
5. Derived tables: fatigue, safety events, incidents, handover notes.
6. Write `validation.ipynb`: plots and the checks listed in `synthetic_data.md`.
7. Write `load_to_supabase.py` (psycopg `COPY` for telemetry, respect FK order).

**Done when:** `python generate.py` produces all tables in < 5 min, validation passes, data is in Supabase.

### Phase 2 — Models (in this order)
For each model, one notebook: `01_anomaly.ipynb`, `02_task_time.ipynb`,
`03_clustering.ipynb`, `04_predictive_maintenance.ipynb`.

**How, for every notebook:**
1. Load Parquet/CSV, build features exactly as specified in `models.md`.
2. Time-based split: days 1–70 train, 71–80 validation, 81–90 test.
3. Baseline first (median, simple rule) — you need something to beat.
4. Train with the listed parameters, tune lightly (no more than 1 hour per model).
5. Evaluate with the listed metrics; for anomaly and clustering, compare to ground truth.
6. Export to `ml/artifacts/<model>/` with `joblib`: model, scaler/encoders, `feature_list.json`, `metrics.json`.
7. Write `ml/artifacts/<model>/README.md` — the one-page result sheet (metric, one chart,
   3 sentences). These go straight into the slides.
8. Provide a plain Python function C can import:
   `predict_task_time(df) -> DataFrame`, `score_anomaly(window_df) -> dict`,
   `predict_failure(machine_features) -> dict`, `cluster_week(metrics_df) -> DataFrame`.
   Put them in `ml/inference/` with no notebook dependencies.

**Done when:** C can `from ml.inference import predict_task_time` and get the output shape in `models.md`.

### Phase 3–5
- Health score formula (`models.md` §11) and plan re-evaluation logic (§12) as inference functions.
- Help C wire models into the replay loop; check alerts fire at the right moments.
- Build the scenario signal sequences (`overheating`, `hydraulic_leak`, `tip_risk`) as small
  DataFrames the replay engine can inject.
- Stretch: LSTM autoencoder.

### Phase 6
- Own the "Data and models" slides: hidden rules → what each model learned → metrics.
- Be ready for judge questions: why Isolation Forest, why time split, why quantile regression,
  how you'd retrain on real Cat telemetry.

**Tools:** Python 3.11, pandas, numpy, scikit-learn, LightGBM, XGBoost, SHAP, matplotlib, joblib, pyarrow.

**Pitfalls:** training on `anomaly_label` (don't); random train/test split (don't);
spending too long making data "perfect" — good enough and on time beats perfect and late.

---

## Person B — Vision + Voice

**Owns:** `vision/`, voice module in `backend/app/voice/`, `models.md` §5, 6, 8

### Phase 0
- Test that the laptop webcam works with OpenCV; measure fps with YOLOv8n.
- Agree the `/events` payload (`api_contract.md`) with C.

### Phase 2 — Proximity (first)
**How:**
1. `vision/proximity.py`: Ultralytics YOLO `model.track(..., classes=[0,2,7], tracker='bytetrack.yaml', persist=True)`.
2. Calibrate distance: stand at 3 m, record the person box height, compute focal constant; store in `vision/calibration.json`.
3. Per track, keep last 10 distances → speed → `approaching` flag.
4. Map to zones (red < 3 m, orange 3–7 m). Debounce: only post an event when the zone changes or every 2 s while in red.
5. Draw a debug overlay (boxes, distance, zone) — useful in the demo as a second screen.
6. Sector: the laptop camera plays "rear". Make it configurable (`--sector rear`) so a second
   webcam or phone camera (via DroidCam/IP camera) can be "front".

### Phase 2 — Fatigue (second)
**How:**
1. `vision/fatigue.py`: MediaPipe Face Mesh → EAR, MAR, head pitch.
2. 30-second calibration on start (open-eye EAR mean).
3. Rolling 60 s PERCLOS buffer; yawn and head-down counters over 10 min.
4. Fatigue score formula from `models.md` §5; hours_into_shift comes from a CLI arg or the backend.
5. Post `fatigue_sample` every minute and `fatigue_high` / `phone_use` events immediately.
6. Critical rule: eyes closed > 2 s while machine moving → immediate critical event.

### Phase 2 — Phone detection (third)
YOLO COCO class 67 on every 5th frame, conf ≥ 0.5, must persist 3 s before posting.

### Phase 3–4 — Voice
**How:**
1. `POST /voice/command`: save audio → faster-whisper `small` int8 → transcript.
2. LLM with function list (next_task, report_incident, machine_status, explain_alert,
   call_supervisor, sos, ask_training) → call the matching backend function.
3. Reply text → Piper TTS (English + Hindi voices) → wav; fallback to browser speechSynthesis.
4. Fatigue-aware behaviour: read the operator's latest fatigue level; medium → shorter replies
   and a 30-min check-in; high → suggest a break and notify supervisor.
5. Test with engine noise played from a phone (YouTube excavator sound) — push-to-talk only.

### Phase 5
- Make `vision/run.py` start everything with one command and reconnect if the backend restarts.
- Rehearse the proximity and fatigue moments until they trigger reliably every time.
- Prepare a fallback: a pre-recorded video file input (`--source demo.mp4`) in case the venue lighting breaks detection.

**Tools:** OpenCV, Ultralytics, MediaPipe, numpy, httpx, faster-whisper, Piper, FastAPI.

**Pitfalls:** low fps from full-resolution frames (use 640 or 480 and skip frames); lighting
changes breaking EAR (recalibrate); posting an event every frame (debounce).

---

## Person C — Backend + AI

**Owns:** `backend/`, `supabase/`, `docs/api_contract.md`, `docs/supabase.md`, `models.md` §R, 7, 9, 10

### Phase 0
- Create the Supabase project, run `001_init.sql`, create demo users, share the anon URL/key.
- Finalise `api_contract.md` with D and B. Put mock JSON files in `web/src/mocks/`.
- FastAPI skeleton: `app/main.py`, routers (`predict`, `events`, `chat`, `voice`, `replay`,
  `scenario`, `plan`), `app/core/config.py` (pydantic-settings), Supabase client.

### Phase 2 — RAG
**How:**
1. Write the knowledge base (in `backend/kb/`): fault code table, troubleshooting FAQ
   (15–20 Q&As), safety rules, operating tips, training module text. Write these yourselves
   so there's no copyright issue.
2. `scripts/ingest_docs.py`: chunk (600 tokens, 100 overlap) → bge-small embeddings →
   `documents` + `document_chunks`.
3. `/chat`: embed question → `match_document_chunks` → prompt with context → LLM → save to `chat_messages`.
4. 25-question test set; fix chunking until ≥ 80% correct.

### Phase 3 — Replay + alert engine (the heart of the demo)
**How:**
1. `app/replay/engine.py`: async loop reads telemetry for selected machines from Supabase (or
   Parquet), emits one row per machine per tick; `speed` controls tick interval.
2. `app/ws.py`: `ConnectionManager` with per-machine subscribers; push `telemetry`, `alert`,
   `safety`, `health`, `fatigue` messages.
3. `app/alerts/rules.py`: implement the rule table and graded-response state machine
   (per machine, per alert_code: stage, since, last_value). Hysteresis on clear.
4. Every minute per machine: call A's `score_anomaly`, compute health, write
   `machine_health_snapshots`, insert/update `alerts`.
5. `/events`: validate, write `safety_events` / `fatigue_log` / `alerts`, forward on WebSocket.
6. `/scenario/{name}`: splice A's scenario sequences into the live stream for that machine.
7. APScheduler jobs: maintenance scoring, clustering, recommendations, handover summaries.

### Phase 3 — LLM features
- Handover summary prompt (`models.md` §9) at shift end and on demand.
- Incident transcript → structured draft.
- Training recommender rules (`models.md` §10).

### Phase 4–5
- Integrate A's inference functions and B's events; end-to-end test each scenario.
- `/health` endpoint showing which models are loaded.
- Deploy backend (Render/Railway) or confirm local run on the demo laptop.

**Tools:** FastAPI, uvicorn, pydantic, supabase-py, psycopg, APScheduler, sentence-transformers, LLM SDK.

**Pitfalls:** blocking the event loop with model inference (use `run_in_threadpool`); writing
every telemetry row to Supabase (don't — stream it, store only alerts/health); schema drift
(any change = new migration + regenerate types + tell D).

---

## Person D — Frontend + Design

**Owns:** `web/`, `docs/design.md`

### Phase 0
- Read `design.md`; set up tokens (`tokens.css`, Tailwind config) before any component.
- Paper sketches of all screens; agree them with the team in 20 minutes, no more.
- Scaffold: Vite + React + TS + Tailwind + react-router + supabase-js + Recharts + react-leaflet + lucide-react + vite-plugin-pwa + idb.

### Phase 1–2 — Build against mocks
**How:**
1. Layout shells: `CabLayout` (status bar, primary zone, safety zone, bottom bar with Voice and
   SOS) and `OfficeLayout` (graphite header, side nav, 12-col grid).
2. Shared components first: `Button`, `StatusBadge` (colour + icon + word), `AlertBanner`,
   `AlertTakeover`, `Gauge`, `TaskCard`, `SafetyPanel` (machine outline + 4 sectors),
   `DigitalTwin` (SVG), `KpiTile`, `EmptyState`.
3. Operator screens: Login → Handover → Tasks → Machine (gauges + twin) → Safety → Training
   (library, chatbot, scenario quiz) → Report incident.
4. Manager screens: Fleet map + alerts feed → Machine detail → Maintenance risk → Clusters
   (scatter + ranking + outliers) → Safety analytics → Geofence editor.
5. Use `web/src/mocks/*.json` behind a `useMock` flag so every screen works before the backend exists.

### Phase 3–4 — Connect real data
1. `useLiveMachine(machineId)`: WebSocket hook with reconnect; updates gauges, pushes alerts to an alert store (zustand).
2. Realtime subscriptions for alerts, incidents, tasks, health.
3. Alert queue logic: one takeover at a time, sounds by severity (Web Audio API tones from `design.md`).
4. Offline: service worker caches app shell + today's tasks; IndexedDB queue for incidents/task
   updates/training progress; status bar shows offline + pending count.
5. i18n: `en.json`, `hi.json`, `ta.json` for operator strings.
6. Hidden demo panel (`/demo`, or press `D` five times) with scenario buttons and replay controls.

### Phase 5–6
- Test on a real tablet or Chrome device mode at 1280×800; test with the laptop projected.
- Check contrast, focus rings, reduced motion.
- Own the presentation's visual design (slides use the same palette; Playfair Display for titles).

**Tools:** React, TypeScript, Tailwind, Recharts, react-leaflet, lucide-react, zustand, idb, vite-plugin-pwa.

**Pitfalls:** building pixel-perfect screens nobody demos (prioritise the demo path); tiny
text in cab mode (18px minimum); hard-coded colours (tokens only).

---

## Shared responsibilities

| Area | Lead | Support |
|---|---|---|
| Architecture decisions | C | all |
| Demo script and rehearsal | D | all |
| Slides | D (design), A (models), C (architecture), B (safety) | |
| Judge Q&A prep | everyone prepares answers for their own area | |
| Backup demo video | B records, D edits | |

## Working rhythm
- **Stand-up every 4–6 hours (10 min):** what's done, what's next, what's blocking.
- **Git:** `main` always runs. Feature branches `a/generator`, `b/proximity`, … Small PRs, merge often.
- **Integration points** (don't skip): end of Phase 1 (sample data in app), end of Phase 2
  (models callable from backend), end of Phase 3 (live stream in UI), end of Phase 5 (full demo run).
- **Using Claude Code:** start each session with "read CLAUDE.md and docs/<your doc>.md".
  Ask it to follow the schema and API contract exactly. Review its diffs before merging.
