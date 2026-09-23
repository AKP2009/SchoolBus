# Demo script

**Story:** a night shift for Ravi, excavator operator on M04 at the NH-48 site, and Priya,
the site manager. About 6–7 minutes. One person drives the operator tablet (Chrome device mode
or a real tablet), one drives the manager screen on the projector, one walks into the webcam, one narrates.

## Setup (before judges arrive)
- [ ] Backend running, `/health` shows all models loaded
- [ ] Replay started as in **Demo data** below (M04 + M05, 10×, from `2026-08-19T15:15:00Z`)
- [ ] Vision service running, calibrated, sector = rear
- [ ] Operator app logged in as Ravi (OP03), manager app as Priya
- [ ] Demo panel open in a hidden tab
- [ ] Laptop volume up (alert tones), Wi-Fi toggle reachable
- [ ] Backup video open in another tab

## Script

| Time | Screen | Action | Say |
|---|---|---|---|
| 0:00 | Slide | Problem | "Machines are digital; the operator's tools aren't. We built the companion." |
| 0:30 | Operator | Login → handover brief | "Ravi starts with what the last shift left him: hydraulic oil ran hot, task 5 unfinished." |
| 1:00 | Operator | Tasks | "Each estimate is a range from our model, with reasons — rain adds 8 minutes." |
| 1:30 | Operator | Walk into webcam from behind | Rear sector turns orange, then red with distance. "Person behind you — 2.4 m." Tone plays. |
| 2:15 | Demo panel | `overheating` on M04 | Warning banner → derate → recommend shutdown takeover with steps. "We never cut power — the machine might be holding a load. We guide a safe shutdown and escalate." |
| 3:00 | Manager | Alerts feed + map | Escalated alert appears live; M04 turns red on the map; digital twin shows cooling red. |
| 3:30 | Operator | Close eyes at camera ~3 s | Fatigue rises to high; voice suggests a break. "Night shift, 3 hours in — this is when our data shows most near-misses." |
| 4:00 | Operator | Voice: "Report incident — person walked behind the machine" | Structured incident draft appears; confirm. |
| 4:30 | Operator | Wi-Fi off → report another incident → Wi-Fi on | "Sites have bad connectivity; nothing is lost." Manager feed receives it. |
| 5:00 | Operator | Training → chatbot "What does E-365 mean?" | Answer with source; recommendation card: "You idled 31% last week…" |
| 5:30 | Manager | Maintenance board + clusters | "M02 has a 72% hydraulic failure risk in 48 hours. And these clusters found the idle-heavy operators — we built that pattern into the data and the model recovered it." |
| 6:15 | Slide | Impact + future | Real Cat telemetry, edge deployment in the cab, fleet-wide learning. |

## If something breaks
- Vision fails → switch to `--source demo.mp4`.
- LLM slow → cached answers for the demo questions.
- Anything else → backup video, keep narrating.

## Demo data

Chosen with `python scripts/find_demo_window.py`. It ranks 60-min windows in the 14 loaded days
(16–29 Aug 2026) on six things: night shift about 3 h in, rain starting, a task overrunning, a real
fault nearby, fatigue rising, and a proximity event. `anomaly_type` is used only to find faults,
never as a model input. The script needs about 70 s and `data/.env`.

**OP03 (Ravi) has no night shift in the loaded data.** He worked 12 day shifts, and his best window
scores 6.7/14. The script then ranks every S1 operator. We use its **#2**, not #1:
- #1 (`SH-2026-08-27-M05-N`) scores 10.8, but its battery fault started 52 min before the window
  and fatigue only reaches medium.
- #2 has the fatigue → blindspot sequence the script needs, and M04 is on shift at the same time
  for the `overheating` scenario.

| | |
|---|---|
| Shift | `SH-2026-08-19-M05-N`, OP02 Ganesh Nair on **M05 (Cat D6 dozer)**, 18:00–02:00 IST |
| Replay start | **`2026-08-19T15:15:00Z`** (19 Aug 20:45 IST, 2 h 45 min into the night shift) |
| Machines | **`["M04", "M05"]`**. They are the only S1 machines on shift then. M04 is OP09 on `SH-2026-08-19-M04-N`. |
| Speed | **10×**, so the 60 data-minutes play in about 6 min. At 1× nothing natural happens during the demo. |
| Handover at login | M04: OP03's day shift left "Left bucket teeth worn, check. Refuelled to 94%." M05: "Task 5 not finished, 13.8 m3 left." |

```
curl -X POST localhost:8000/replay/start -H 'content-type: application/json' \
  -d '{"machine_ids":["M04","M05"],"from":"2026-08-19T15:15:00Z","speed":10}'
```

**What happens by itself.** Minute = data minute from the replay start; wall = time at 10×.
Only telemetry is replayed (`backend/app/replay/source.py`). Safety events and fatigue rows
from the database are history for the charts, so the live proximity and fatigue moments still
come from the webcam.

| Minute | Wall | What | Source |
|---|---|---|---|
| −45 → +15 | before start | Rain 6.6 mm/h, visibility 3.3 km. Eases to 0.5 mm/h at +15 (21:00 IST). Rain is already falling at the start, so the task range carries a rain factor. | `weather` |
| −31 → +64 | — | `T-SH-2026-08-19-M05-N-3` backfill 155 m³ clay. Planner estimate about 72 min, actual 95 min. | `tasks` |
| +27 → +30 | 2:42–3:00 | **`SEATBELT` critical on M05**: unbuckled while idling at 35 % load. Resolves at +30. This is the only rule alert in the hour. | replay → rule engine (dry-run checked) |
| +41 | 4:06 | Task 3 passes the planner estimate, so it is now overrunning | `tasks` |
| +49 | 4:54 | OP02 fatigue turns **high** (0.45 → 0.65, `fatigue_high` warning) | `fatigue_log`, `safety_events` (history) |
| +51 | 5:06 | **`blindspot_intrusion` critical, 2.4 m, rear**, M05. It matches the script's "2.4 m" line. | `safety_events` (history) |

There is no real engine or hydraulic fault on M04 or M05 in this hour. The rule engine dry-run
fired only the seatbelt alert. The anomaly model (model 1) was not dry-run.

**Moments that still need a trigger**
- 1:30 proximity: walk into the webcam (`vision/run.py --sector rear --machine-id M05`, or M04 if that is the login machine).
- 2:15 overheating: `POST /scenario/overheating {"machine_id":"M04"}`. The 26 data-min scenario takes 2.6 min at 10×.
- 3:30 fatigue: close your eyes at the cab camera. The database history agrees: this operator reaches high at +49.
- 4:00 / 4:30 incident reports and the offline sync: manual, as scripted.

**Open points**
- Persona. The data has OP02 on M05 (a dozer) and OP09 on M04, while the script says Ravi on an
  excavator M04. In our data M04 is a Cat 950 wheel loader. Either log in as OP02 on M05 and rename
  the story, or keep the Ravi login and accept that he has no shift on 19 Aug night. A generator
  change that gives OP03 a night shift is the third option (data owner).
- 5:30 "M02 72 % hydraulic failure risk". The loaded days have no hydraulic pre-failure drift. The
  only ones are M04 electrical (23–26 Aug) and M08 undercarriage (20–24 Aug). The maintenance line
  should use what the predictive maintenance model actually outputs.
