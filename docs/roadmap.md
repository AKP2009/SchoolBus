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
- [ ] Supabase project created, migration applied, demo users created (C)
- [ ] Webcam + YOLO fps test (B)

**Checkpoint 0:** everyone can explain the demo story, the schema, and their first task.

## Phase 1 — Data and scaffolds
- [ ] **A:** 1-day sample dataset shared (within the first few hours)
- [ ] **A:** master tables, weather, tasks with duration formula
- [ ] **A:** telemetry normal behaviour + drift + anomalies + labels
- [ ] **A:** fatigue, safety events, incidents, handover notes
- [ ] **A:** validation notebook passes; 14 days loaded into Supabase
- [ ] **B:** proximity detection with tracking and distance on webcam
- [ ] **C:** FastAPI skeleton, routers, config, Supabase client, `/health`
- [ ] **C:** knowledge base documents written (fault codes, FAQ, safety, tips)
- [ ] **D:** tokens, Tailwind config, layouts, shared components

**Checkpoint 1:** sample data renders in at least one real screen; webcam shows distance to a person.

## Phase 2 — Models, vision, RAG, screens
- [ ] **A:** anomaly model + result sheet
- [ ] **A:** task time model (p10/p50/p90 + SHAP) + result sheet
- [ ] **A:** clustering + result sheet
- [ ] **A:** predictive maintenance + result sheet
- [ ] **A:** `ml/inference/` functions importable by backend
- [ ] **B:** fatigue detection (EAR, PERCLOS, yawn, head-down, score)
- [ ] **B:** phone detection
- [ ] **B:** vision posts to `/events` (backend stub is fine)
- [ ] **C:** RAG ingest + `/chat` passing ≥ 80% of test questions
- [ ] **D:** all P0 operator screens on mocks
- [ ] **D:** all P0 manager screens on mocks

**Checkpoint 2:** `from ml.inference import …` works in the backend; every P0 screen exists.

## Phase 3 — Live backend
- [ ] **C:** replay engine + WebSocket `/stream/{machine_id}`
- [ ] **C:** rule engine + graded response state machine + hysteresis
- [ ] **C + A:** anomaly scoring and health snapshots every minute in replay
- [ ] **C:** `/events` writes safety_events / fatigue_log / alerts and forwards on WebSocket
- [ ] **C:** `/predict/task-time` writes predictions to tasks
- [ ] **C + A:** `/scenario/{name}` with overheating, hydraulic_leak, tip_risk, seatbelt
- [ ] **C:** handover summary, incident transcript → draft
- [ ] **B:** voice command pipeline (STT → intent → TTS)

**Checkpoint 3:** start replay → an overheating scenario produces warn → derate → recommend
shutdown → escalated alerts in the DB within the expected times.

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
- [ ] Plan re-evaluation (P1) if time
- [ ] Geofencing, training recommendations, scenario quiz (P1) if time
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
