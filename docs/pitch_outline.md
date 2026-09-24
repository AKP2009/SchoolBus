# Pitch outline

Ten slides for about 6–7 minutes. They follow the story in `demo_script.md`: Ganesh Nair (OP02) drives M05, a Cat D6
dozer, on a night shift at the NH-48 site in the rain. Priya Menon is the site manager. Slides 2–8 are mostly live
demo, and the slide is the backdrop or the fallback. Numbers come from `results.md`. Answers to follow-up questions
are in `qa_prep.md`.

Slide design: `design.md` palette. Playfair Display only on title slides, DM Sans everywhere else.

---

### 1. Machines are digital; the operator's tools aren't (0:00)
- CAT machines already report hours, fuel, idle, location and fault codes, but the operator in the cab doesn't see
  most of it.
- Operators face night shifts, rain, blind spots, fatigue and bad connectivity.
- We built one companion for the operator in the cab and one dashboard for the site manager.
- **Show:** title slide, then a photo-style shot of the cab tablet in dark cab mode.

### 2. Ganesh starts his night shift (0:30)
- 20:45, 2 h 45 min into an 18:00–02:00 night shift on M05. Rain at 6.6 mm/h, visibility 3.3 km.
- At login he gets the handover from the day shift: "Task 5 not finished, 13.8 m³ left."
- The app works with gloves on: touch targets ≥ 64 px, text ≥ 18 px, dark theme, voice button always on screen.
- **Show:** operator app, Login → Handover brief.

### 3. Every estimate is a range with reasons (1:00)
- Each task shows a p50 time, a p10–p90 range and the top factors behind it (rain, material, slope).
- Off by 8.86 min on average against 23.75 min for the rule of thumb. The range holds the real time for 80.0 % of tasks.
- The model recovered 4 of 6 causes we planted. Night and late-shift estimates are too optimistic, and we say so.
- **Show:** operator app, Tasks with factor chips. Say the factor the screen actually shows: the "rain +8 min"
  line in `demo_script.md` is an example, not a measured value.

### 4. Eyes behind the machine (1:30)
- A teammate walks into the webcam from behind. The rear sector turns orange at 7 m, then red under 3 m: "Person behind you — 2.4 m."
- YOLO11n + ByteTrack on a laptop CPU at ~21–30 fps. Moving closer raises severity. Zones widen by 2 m in low visibility or high fatigue.
- One-camera distance is ±20–30 %, so production fuses it with ultrasonic or radar.
- **Show:** operator safety panel (4 sectors) + the vision overlay on a second screen.

### 5. We never cut the power (2:15)
- The overheating scenario starts on M05. Warning → derate → recommend safe shutdown with steps → escalate to Priya.
- Why: the machine might be holding a load or standing on a slope. A sudden stop can cause the accident.
- Rules + model catch 84 of 87 test faults (96.5 %), with a median 2 min delay and 17.49 false alerts per 100 machine-hours.
- **Show:** operator warning banner → critical takeover with shutdown steps. Slide fallback: the four-stage ladder.

### 6. Priya sees it live (3:00)
- The escalated alert appears on the manager feed at about 3:40 and M05 turns red on the map.
- The digital twin shows cooling in red, with the reason.
- A broken sensor is not a broken machine: all 19 test sensor glitches were labelled correctly as glitches, so none raised a false critical alarm.
- **Show:** manager Alerts feed + fleet map + M05 digital twin.

### 7. Fatigue at the worst hour (3:30)
- Close eyes at the cab camera for ~3 s. Fatigue goes high, and the assistant suggests a break.
- Signals: eye closure, PERCLOS over 60 s, yawns, head down, phone use, plus hours into shift and night shift.
  Eyes closed > 2 s while moving is critical at once.
- Frames never leave the device and are never stored. Only the numbers are saved.
- "Night shift, 3 hours in": in our synthetic data, near-misses rise when fatigue is high (we built that in, ×2.5).
- **Show:** operator fatigue indicator going low → high, and the break suggestion.

### 8. Report it by voice, even offline (4:00–5:00)
- Voice: "Report incident — person walked behind the machine" → a structured draft that Ganesh confirms.
- Wi-Fi off → report another incident → Wi-Fi on → it appears on Priya's feed. Each report carries a `client_id`,
  so a retry never duplicates it.
- Training hub: "What does E-365 mean?" gets an answer with its source, plus a recommendation card ("You idled 31 % last week…").
- **Show:** operator incident draft, offline status bar, manager feed receiving it, chatbot answer.

### 9. Predicting failures, and proving the models work (5:30)
- Tonight the whole fleet is low risk (highest M02 at 0.021), so we show a backtest from the same two weeks.
  M04's electrical system failed on 25 Aug. The model flagged it **23 engine hours ahead**, about 59 calendar hours.
- Across 15 unseen failures: 12 caught, median 37.31 h lead, PR-AUC 0.693 against 0.104 for a random score. The safety floor brings all 15 to medium risk.
- Clustering found the idle-heavy, aggressive and novice operators we built into the data (100 % of their weeks). It
  merged efficient with average (ARI 0.415), and we explain why.
- **Show:** `ml/artifacts/health/health_before_failure.png`, then the manager Clusters view
  (`ml/artifacts/clustering/pca_scatter.png` as fallback).

### 10. What's next (6:15)
- Real Cat telemetry: swap the replay for a live feed, take thresholds from service manuals, retrain on the fleet's history.
- Edge deployment in the cab, so safety warnings don't depend on the site network.
- Fleet-wide learning: more failure history for the weak components (electrical, undercarriage), and a fix for the late-shift bias.
- **Show:** closing slide with the one-line summary and the three next steps.

---

**Before presenting:** check `roadmap.md`. Every **Show** screen needs to run end to end (Phase 4–5). For any
screen that isn't wired yet, use the slide or the backup video and say it's a mock-up.
