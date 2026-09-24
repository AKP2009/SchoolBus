# Demo script

**Story:** a night shift for Ganesh (OP02), dozer operator on M05 at the NH-48 site, and Priya,
the site manager. About 6–7 minutes. One person drives the operator tablet (Chrome device mode
or a real tablet), one drives the manager screen on the projector, one walks into the webcam, one narrates.

## Setup (before judges arrive)
- [ ] **Before the demo:** clear leftovers from rehearsals. Stop any replay, then run
      `python scripts/reset_demo_state.py` (dry run, check the counts) and
      `python scripts/reset_demo_state.py --apply`. This empties alerts, health snapshots,
      maintenance predictions, fleet metrics, chat and recommendations. It also deletes the
      safety/fatigue/incident rows added after the load and clears handover summaries and task
      predictions. Loaded data stays. The script refuses to run while a replay is running.
- [ ] Backend running, `/health` shows all models loaded
- [ ] Replay command from **Demo data** below ready (M04 + M05, 10×, from `2026-08-19T15:15:00Z`).
      Fire it at 0:00, not earlier: at 10× every minute of waiting uses 10 minutes of the window.
- [ ] Vision service running, calibrated, sector = rear, `--machine-id M05 --operator-id OP02`
- [ ] Operator app logged in as Ganesh (`ganesh@demo.site`, OP02), manager app as Priya
- [ ] Demo panel open in a hidden tab
- [ ] Laptop volume up (alert tones), Wi-Fi toggle reachable
- [ ] Backup video open in another tab

## Script

| Time | Screen | Action | Say |
|---|---|---|---|
| 0:00 | Slide | Problem | "Machines are digital; the operator's tools aren't. We built the companion." |
| 0:30 | Operator | Login → handover brief | "Ganesh starts with what the day shift left him on M05: task 5 unfinished, 13.8 m³ left." |
| 1:00 | Operator | Tasks | "Each estimate is a range from our model, with reasons — rain adds 8 minutes." |
| 1:30 | Operator | Walk into webcam from behind | Rear sector turns orange, then red with distance. "Person behind you — 2.4 m." Tone plays. |
| 2:15 | Demo panel | `overheating` on M05 | Warning banner → derate → recommend shutdown takeover with steps. "We never cut power — the machine might be holding a load. We guide a safe shutdown and escalate." |
| 3:00 | Manager | Alerts feed + map | Escalated alert appears live (about 3:40); M05 turns red on the map; digital twin shows cooling red. |
| 3:30 | Operator | Close eyes at camera ~3 s | Fatigue rises to high; voice suggests a break. "Night shift, 3 hours in — this is when our data shows most near-misses." |
| 4:00 | Operator | Voice: "Report incident — person walked behind the machine" | Structured incident draft appears; confirm. |
| 4:30 | Operator | Wi-Fi off → report another incident → Wi-Fi on | "Sites have bad connectivity; nothing is lost." Manager feed receives it. |
| 5:00 | Operator | Training → chatbot "What does E-365 mean?" | Answer with source; recommendation card: "You idled 31% last week…" |
| 5:30 | Manager | Health backtest (`ml/artifacts/health/health_before_failure.png`) + clusters | "Tonight the whole fleet is low risk. Here is a failure from these same two weeks: M04's electrical system failed on 25 August, and our model flagged it 23 engine hours ahead — two and a half days on the calendar. And these clusters found the idle-heavy operators — we built that pattern into the data and the model recovered it." |
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

**Decisions**
- **The demo operator is OP02, Ganesh Nair, on M05 (Cat D6 dozer).** The earlier persona, OP03 Ravi,
  has no night shift in the loaded data. He worked 12 day shifts, and his best window scores 6.7/14.
  The demo login is `ganesh@demo.site` (`backend/scripts/create_demo_users.py`). The old
  `ravi@demo.site` account still exists but is no longer used.
- **We use the script's #2 window, not #1.**
  - #1 (`SH-2026-08-27-M05-N`) scores 10.8, but its battery fault started 52 min before the window
    and fatigue only reaches medium.
  - #2 has the fatigue → blindspot sequence the script needs.
- **Replay at 10×.** 60 data-minutes play in about 6 min. At 1× nothing natural happens during the demo.
- **The overheating scenario runs on M05**, Ganesh's own machine, so it shows on his tablet and on
  Priya's screen.

| | |
|---|---|
| Shift | `SH-2026-08-19-M05-N`, OP02 Ganesh Nair on M05, 18:00–02:00 IST |
| Replay start | **`2026-08-19T15:15:00Z`** (19 Aug 20:45 IST, 2 h 45 min into the night shift) |
| Machines | **`["M04", "M05"]`**. They are the only S1 machines on shift then. M04 is OP09 on `SH-2026-08-19-M04-N`. |
| Handover at login | From `SH-2026-08-19-M05-D` (OP10): "Task 5 not finished, 13.8 m3 left." |

```
curl -X POST localhost:8000/replay/start -H 'content-type: application/json' \
  -d '{"machine_ids":["M04","M05"],"from":"2026-08-19T15:15:00Z","speed":10}'
curl -X POST localhost:8000/scenario/overheating -H 'content-type: application/json' -d '{"machine_id":"M05"}'
python vision/run.py --mode both --camera 1 --cab-camera 0 --sector rear --machine-id M05 --operator-id OP02 --shift-type night
```

**What happens by itself.** Minute = data minute from the replay start; wall = time at 10× if the
replay starts at 0:00. Only telemetry is replayed (`backend/app/replay/source.py`). Safety events
and fatigue rows from the database are history for the charts, so the live proximity and fatigue
moments still come from the webcam.

| Minute | Wall | What | Source |
|---|---|---|---|
| −45 → +15 | before start | Rain 6.6 mm/h, visibility 3.3 km. Eases to 0.5 mm/h at +15 (21:00 IST). Rain is already falling at the start, so the task range carries a rain factor. | `weather` |
| −31 → +64 | — | `T-SH-2026-08-19-M05-N-3` backfill 155 m³ clay. Planner estimate about 72 min, actual 95 min. | `tasks` |
| +27 → +30 | 2:42–3:00 | `SEATBELT` critical on M05, from the recorded data. It doesn't happen in the scripted demo, because the overheating scenario takes over M05's stream from 2:15 to about 4:51. | replay → rule engine (dry-run checked) |
| +41 | 4:06 | Task 3 passes the planner estimate, so it is now overrunning | `tasks` |
| +49 | 4:54 | OP02 fatigue turns **high** (0.45 → 0.65, `fatigue_high` warning) | `fatigue_log`, `safety_events` (history) |
| +51 | 5:06 | **`blindspot_intrusion` critical, 2.4 m, rear**, M05. It matches the script's "2.4 m" line. | `safety_events` (history) |

There is no real engine or hydraulic fault on M04 or M05 in this hour. The rule engine dry-run
fired only the seatbelt alert. The anomaly model (model 1) was not dry-run.

**Moments that still need a trigger**
- 1:30 proximity: walk into the webcam (rear sector, M05).
- 2:15 overheating: `POST /scenario/overheating {"machine_id":"M05"}`, 26 data-min. Coolant goes
  over 105 °C about 6.7 data-min after the trigger. At 10× (wall time):
  - critical warning at about 2:55
  - derate at about 3:07
  - recommend shutdown at about 3:15–3:25
  - escalated at about 3:35–3:40
  - cool-down finished at about 4:51
- 3:30 fatigue: close your eyes at the cab camera. The database history agrees: Ganesh reaches high at +49.
- 4:00 / 4:30 incident reports and the offline sync: manual, as scripted.

**Maintenance line (5:30).** Checked with `ml.inference.maintenance` on all 12 machines (S1 and S2)
at `2026-08-19T15:15Z`. It used the regenerated 90-day telemetry, which matches Supabase row for
row, because the features need up to 7 days of history. Every machine is **low** risk. The highest
is M02 at 0.021, and medium starts at 0.3. So the script shows a backtest instead:
- **M04 electrical failure**, 2026-08-25 02:27Z (07:57 IST).
- The model first reaches medium risk (p 0.53, component electrical) at 2026-08-22 15:26Z.
- That is **23 engine hours**, about 59 calendar hours, before the failure.
- By the next hour it is 0.62, **high**. After that it moves between medium and high until the failure.
- Chart and details: `ml/artifacts/health/health_before_failure.png` and `README.md` there.

The 37 h in `ml/artifacts/maintenance/README.md` is the **median** lead (p ≥ 0.6) over the 12
failures caught in cross-validation, not M04's own lead. Don't quote 37 h for M04.
