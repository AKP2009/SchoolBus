# Demo script

**Story:** a night shift for Ravi, excavator operator on M04 at the NH-48 site, and Priya,
the site manager. About 6–7 minutes. One person drives the operator tablet (Chrome device mode
or a real tablet), one drives the manager screen on the projector, one walks into the webcam, one narrates.

> **Mocks (web without a backend):** the web mocks follow a different real shift: OP02 Ganesh Nair on the
> dozer M05, night shift `SH-2026-08-19-M05-N`, replayed from 15:15 UTC (21:00 IST, 2 h 45 min in).
> It holds a real blindspot intrusion at 2.4 m (16:05), fatigue reaching high (16:04) and the overheating
> scenario at 16:54. `/demo` jumps to each. The Ravi / M04 story below applies to the live backend demo.

## Setup (before judges arrive)
- [ ] Backend running, `/health` shows all models loaded
- [ ] Replay started for M01–M06 at 1×, from a timestamp 3 h into a night shift
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
