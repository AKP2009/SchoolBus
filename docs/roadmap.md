# Roadmap

Phases run in order; work inside a phase runs in parallel. Tick boxes as you finish.
A phase ends at its **checkpoint** — don't start the next phase's integration until the
checkpoint passes (individual work can run ahead).

## Time budget

| Phase | Share | 48-hour hackathon | 7-day build |
|---|---|---|---|
| 0. Design lock | 5% | 0–2.5 h | Day 1 morning |
| 1. Data + scaffolds | 15% | 2.5–10 h | Day 1–2 |
| 2. Models, vision, RAG, screens on mocks | 25% | 10–22 h | Day 2–4 |
| 3. Backend live system | 15% | 22–29 h | Day 4–5 |
| 4. Connect frontend | 20% | 29–38 h | Day 5–6 |
| 5. Integration + scenarios | 12% | 38–44 h | Day 6 |
| 6. Polish + pitch | 8% | 44–48 h | Day 7 |

Sleep in shifts during a 48-hour event; never all four at once, never the same person twice in a row near the end.

---

## Phase 0 — Design lock (everyone)
- [ ] Read `features.md`; confirm P0 / P1 / P2 split — freeze it
- [ ] Confirm schema (`001_init.sql`) and generator column names match
- [ ] Confirm `api_contract.md`; D creates mock JSON files from it
- [ ] Paper sketches for all P0 screens
- [ ] Repo created with folder layout from `CLAUDE.md`; everyone can run `git pull`
- [x] Supabase project created, migration applied, demo users created (C) _(project `hjdxrhgqojlwpayoyfvc` linked, 001+002 pushed; 5 demo users via `backend/scripts/create_demo_users.py`)_
- [ ] Webcam + YOLO fps test (B)

**Checkpoint 0:** everyone can explain the demo story, the schema, and their first task.

## Phase 1 — Data and scaffolds
- [x] **A:** 1-day sample dataset shared (within the first few hours): `generate.py --sample` → `data/output/sample/`
- [x] **A:** master tables, weather, tasks with duration formula
- [x] **A:** telemetry normal behaviour + drift + anomalies + labels
- [x] **A:** fatigue, safety events, incidents, handover notes
- [x] **A:** validation notebook passes; 14 days loaded into Supabase _(28/28 checks; `load_to_supabase.py --days 14 --reset`, 2026-08-16 → 08-29, ~210k rows, 61 MB)_
- [x] **B:** proximity detection with tracking and distance on webcam _(YOLO11n + ByteTrack, ~21–30 fps at 640 px on the laptop webcam, CPU; `vision/run.py`)_
- [x] **C:** FastAPI skeleton, routers, config, Supabase client, `/health`
- [x] **C:** knowledge base documents written (fault codes, FAQ, safety, tips) _(`backend/kb/`: 5 markdown docs, `scenarios.json` (12 scenarios), `rag_eval.json` (25 questions))_
- [x] **D:** tokens, Tailwind config, layouts, shared components _(`web/`, Vite 7 + React 18 + Tailwind 3; every component on `/styleguide` in both modes; checked at 1280×800 and 1440)_

**Checkpoint 1:** sample data renders in at least one real screen; webcam shows distance to a person.

## Phase 2 — Models, vision, RAG, screens
- [x] **A:** anomaly model + result sheet (model alone misses recall target, see `ml/artifacts/anomaly/README.md`)
- [x] **A:** task time model (p10/p50/p90 + SHAP) + result sheet (late-shift and night p50 optimistic, see `ml/artifacts/task_time/README.md`)
- [x] **A:** clustering + result sheet (personality ARI 0.42 on holdout, below 0.5: efficient and average share a cluster, see `ml/artifacts/clustering/README.md`)
- [x] **A:** predictive maintenance + result sheet (hour-level recall 0.44 misses 0.75, 12/15 failures caught at 0.5 and 15/15 at medium with the 6σ safety floor, median lead 37 h; electrical/undercarriage weak, see `ml/artifacts/maintenance/README.md`)
- [x] **A:** digital twin health score + plan re-evaluation (`ml/inference/health.py`, `plan.py`, demo `ml/05_health_and_plan.ipynb`; anomaly term only on `machine_fault`, see `ml/artifacts/health/README.md` and `ml/artifacts/plan/README.md`)
- [x] **A:** `ml/inference/` functions importable by backend _(the backend imports anomaly, health, task_time, plan, maintenance and clustering)_
- [x] **B:** fatigue detection (EAR, PERCLOS, yawn, head-down, score) _(MediaPipe FaceLandmarker 1.0.1, ~23–24 fps on the laptop webcam with phone detection on; `vision/run.py --mode fatigue|both`)_
- [x] **B:** phone detection _(YOLO11n class 67 every 5th cab frame, shares the proximity weights, 3 s persistence)_
- [x] **B:** vision posts to `/events` (backend stub is fine) _(httpx + backoff; 501 from the stub is logged and dropped; verified in `--dry-run` only)_
- [x] **B:** vision live on the backend: `VISION_API_TOKEN` from `vision/.env` on every call; machine moving from `GET /machine/{id}/state` and high fatigue from `GET /operator/{id}/fatigue`, polled every 2 s with last-known / safe-default fallback; defaults OP02/M05 _(`vision/tests/test_backend_client.py` against a mock backend; live: 20 s proximity run posted 20 `blindspot_intrusion` → 20 `safety_events` rows on one `BLINDSPOT_RED` alert)_
- [ ] **C:** RAG ingest + `/chat` passing ≥ 80% of test questions _(built; 21/25 = 84% but 2 unsafe (E-365, Q01/Q24) vs target 0: see models.md §7)_
- [ ] **D:** all P0 operator screens on mocks
- [ ] **D:** all P0 manager screens on mocks

**Checkpoint 2:** `from ml.inference import …` works in the backend; every P0 screen exists.

## Phase 3 — Live backend
- [x] **C:** replay engine + WebSocket `/stream/{machine_id}`
- [x] **C:** rule engine + graded response state machine + hysteresis
- [x] **C + A:** anomaly scoring and health snapshots every minute in replay
- [x] **C:** `/events` writes safety_events / fatigue_log / alerts and forwards on WebSocket _(all 6 types validated per type; one alert per episode, resolved after 30 s quiet; SOS = emergency; `GET /operator/{id}/fatigue` added)_
- [x] **C:** `/predict/task-time` writes predictions to tasks _(live health from the replay / `v_machine_health_latest`)_
- [x] **C:** `/plan/re-evaluate` + `/plan/accept` _(fatigue trigger from the latest `fatigue_log`)_
- [x] **C:** maintenance scoring job every 10 replay minutes → `maintenance_predictions` (health shows failure probability); `POST /analytics/cluster` + daily clustering job (APScheduler)
- [x] **C:** Supabase JWT on every endpoint except `/health`; vision uses `VISION_API_TOKEN`; managers-only replay / scenario / clustering; contract error shape everywhere _(`backend/tests`, 99 passing)_
- [x] **C + A:** `/scenario/{name}` with overheating, hydraulic_leak, tip_risk, seatbelt _(also `GET /machine/{id}/state` for vision; `backend/tests`, 39 passing)_
- [x] **C:** handover summary, incident transcript → draft _(plus handover job at shift end, `backend/tests/test_ai.py`)_
- [ ] **B:** voice command pipeline (STT → intent → TTS)

**Checkpoint 3:** start replay → an overheating scenario produces warn → derate → recommend
shutdown → escalated alerts in the DB within the expected times. _(met 2026-09-24 for the backend part:
M01–M04 at speed 10 from Supabase, overheating on M04 → COOLANT_CRITICAL warn / derate +2 min /
recommend_shutdown +3 (value rising) / escalated +5 (not acknowledged) / resolved +15, data time)_

## Phase 4 — Connect frontend
- [ ] **D:** live gauges from WebSocket
- [ ] **D:** alerts via Realtime + takeover queue + sounds
- [ ] **D:** safety panel driven by vision events
- [ ] **D:** tasks with predictions and factor chips; start/complete
- [ ] **D:** fleet map, alerts feed, machine detail on real data
- [ ] **D:** digital twin from health snapshots
- [ ] **D:** chatbot UI, incident report (form + voice), SOS
- [ ] **D:** clusters view, maintenance board
- [ ] **D:** offline queue for incidents and task updates
- [ ] **B:** voice button wired in cab UI; fatigue-aware replies

**Checkpoint 4:** the full demo path runs end to end on one laptop, even if rough.

## Phase 5 — Integration and scenarios (everyone)
- [ ] Demo panel with all scenario buttons
- [ ] Run `demo_script.md` end to end 3 times; log every failure; fix
- [ ] Plan re-evaluation (P1) if time _(ML and backend done: `/plan/re-evaluate` and `/plan/accept`; UI not wired yet)_
- [ ] Geofencing, training recommendations, scenario quiz (P1) if time _(backend: recommender rules + daily job + `POST /training/recommendations` done)_
- [ ] Offline demo: Wi-Fi off → report incident → Wi-Fi on → appears on manager screen
- [ ] Fallbacks ready: `--source demo.mp4` for vision, cached LLM answers for chat

**Checkpoint 5:** three clean demo runs in a row.

## Phase 6 — Polish and pitch
- [ ] Design pass: contrast, text sizes, empty and offline states
- [ ] Slides (8–10): problem · personas · architecture · synthetic data · models + metrics ·
      safety design (graded response) · live demo · impact · future scope
- [ ] Record backup demo video
- [ ] Judge Q&A prep per person
- [ ] README and docs up to date; repo public if required

## Stretch (only after Checkpoint 5)
- [ ] LSTM autoencoder comparison
- [ ] OR-Tools scheduler
- [ ] External damage detection
- [ ] Instructor booking
- [ ] Yjs-based sync
- [ ] Daylight high-brightness theme

## Dependency map
```
Schema ─┬─> Generator ─> Models ─> Backend inference ─┐
        ├─> Backend skeleton ─> Replay + rules ───────┼─> Frontend live data ─> Demo
        └─> Mock JSON ─> Frontend screens ────────────┘
Webcam test ─> Vision ─> /events ─────────────────────┘
KB docs ─> RAG ─> Chat UI ────────────────────────────┘
```
