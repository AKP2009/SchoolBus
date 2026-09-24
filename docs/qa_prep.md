# Judge Q&A prep

The 25 hardest questions we expect, with short, honest answers. Numbers match `docs/results.md`.
Each answer's source doc is in brackets. When a question is about a weakness, admit it first, then give the reason
and the fix.

**Owner by area** (`person_role.md`): data and models → A · vision and voice → B · backend, safety logic and RAG → C ·
UI and offline → D.

---

## Data

**1. Why synthetic data? Isn't that a weakness?**
We had no access to real Cat telemetry, and the problem statement allows "available or assumed data". Synthetic data
also gives us something real data doesn't: **ground truth.** We know which minutes were faults, which operators were
idlers, and what really drives task time. So we can prove what each model learned, not only report a score.
The weakness is real, though: real sensors are messier than ours. [assumptions.md, synthetic_data.md]

**2. How do you know the data is realistic?**
We don't claim the values match Cat specifications. Our thresholds and fault codes are our own, and we label them as
assumptions. What we checked is that the data behaves plausibly. Signals are smoothed so they drift like physical
quantities (hydraulic oil slowly, rpm quickly). Rain comes in multi-hour spells. Failures build up over 50–200 engine
hours. Handover notes include typos. The validation notebook runs 28 checks, all passing, including anomaly rate
2.5–3.5 % (measured 3.00 %), duration rising with rain and falling with skill, and a simple linear model reaching
R² > 0.6 on tasks. [synthetic_data.md, roadmap.md]

**3. Isn't it circular: you wrote the formula, then the model found it?**
Partly, and that is the point of the test: if a model can't recover a cause we planted, it won't find one in real
data. It isn't a given, though. The models missed things. Task time recovered **4 of 6** planted factors and missed
night shift and hours into shift. Clustering reached ARI **0.415**, below our 0.5 target. We report the misses and
why they happened. [results.md §2, §3]

**4. How would this run on real Cat telemetry?**
Cat Product Link / VisionLink already provides hours, fuel, idle time, location and fault codes. We'd add a live feed
in place of the replay engine: the replay emits the same one-minute rows a live feed would. Thresholds would come from each
machine model's service manual; they already sit in one config file (`thresholds.yaml`). Then we retrain on the
fleet's own history with the same time-based split. The maintenance model compares each signal with the machine's
own normal, which is designed to carry over between machines better than absolute levels. [assumptions.md, models.md §R, §3]

**5. Which sensors do you assume that machines may not have today?**
An IMU (pitch and roll), a seatbelt switch, four exterior cameras, ultrasonic or radar per sector, a cab camera, and a
cab tablet. Everything else comes from standard engine telematics. Each assumed sensor is listed in
`assumptions.md` with the feature it enables. Without the cameras there's no proximity or fatigue, but the machine
health features still work. [assumptions.md]

## Safety design

**6. Why not stop the machine automatically when something is dangerous?**
Because a sudden stop can be more dangerous than the fault. The machine may be lifting a load, travelling, or on a
slope. We use a **graded response**: warn → derate (recommend reduced power) → recommend a safe shutdown with steps
("lower the attachment, move to level ground, idle to cool, shut down") → escalate to the site manager. The system
advises and escalates. It never sends a control command. Any automatic intervention in production belongs to Cat's
own machine-control safety systems. [features.md §3.2, models.md §R, assumptions.md]

**7. What if the operator ignores the warnings?**
The alert escalates by itself. It derates after 2 min critical, recommends shutdown after 5 min (earlier if the value
keeps rising), and escalates to the manager after 7 min, or once the operator hasn't acknowledged it 2 min after the
shutdown step. In the backend test, overheating on M04 escalated after 5 min because nobody acknowledged it. Safety
rules (seatbelt, tip risk) skip derate and escalate after 2 min critical. [models.md §R, roadmap.md Checkpoint 3]

**8. How do you stop false alarms from making operators ignore the system?**
Three ways. First, we measured it: **17.5 false alerts per 100 machine-hours**, down from 138 before tuning. That's
about one per 5.7 machine-hours. Second, alerts have hysteresis and one alert per episode, so they don't flicker or
repeat. Third, the UI is silent by default: only critical alerts take over the screen, and warnings are one banner
and one tone. A 150 °C coolant spike from a broken sensor doesn't raise a critical alarm: the model labelled all
**19 of 19** sensor glitches correctly. [results.md §1, design.md]

**9. Your anomaly model alone catches only 28 % of events. Why ship it?**
Because of what it does that the rules can't: telling a broken sensor from a broken machine (19 of 19). The rules catch
most real faults. Together they catch **84 of 87 (97 %)** with a 2 min median delay. After tuning, the model spends
its flags on idle↔working transitions, which we filter out. The next fix, not tried yet, is to train it only on
settled minutes. [results.md §1, ml/artifacts/anomaly/README.md]

## Privacy and people

**10. A camera watching the operator's face: what about privacy?**
Frames are processed on the device and never stored. Only derived numbers leave it: eye aspect ratio, PERCLOS, event
counts. Row-level security lets an operator see only their own fatigue rows. Managers can read all of them, as
supervisors need to know when someone is too tired to operate. In a real rollout, the retention and access policy
would be agreed with operators before switching the camera on. [assumptions.md, supabase.md §5]

**11. Could managers use this to punish operators?**
The design pushes against it. Clustering outliers are marked "for the manager to verify", not verdicts, and each
one names the metric behind it. Fatigue alerts are left out of the "needs safety coaching" metric, because fatigue
follows the roster (night shifts), not behaviour. Weak metrics lead to a training module with a plain reason ("You
idled 31 % last week…"), not a score on a wall. Policy matters as much as software here. [models.md §4, §10]

## Offline and field conditions

**12. Construction sites have bad connectivity. What happens offline?**
The operator app is a PWA. Today's tasks, the handover and downloaded training are cached. Incident reports, task
updates and training progress are queued in IndexedDB with a client-generated `client_id` and synced when the network
returns. Because sync is an upsert on `client_id`, a retry never creates a duplicate. Production would use a CRDT
library (Yjs) for edits by several people, but our writes only add records, so the queue is enough.
[supabase.md "Offline sync", features.md §7.1]

**13. Does safety depend on the network?**
In our demo, the vision service posts to a backend on the same laptop, and the UI gets vision events over a WebSocket
in under a second. In production, the camera and the warnings would run on a computer in the cab, so the operator gets the warning even when
the site network is down. That edge setup is future work, not built. [architecture.md, demo_script.md]

**14. Gloves, vibration, glare, noise: can operators actually use it?**
Cab mode touch targets are at least 64 px and body text at least 18 px. It uses a dark theme for night, and every
status has a colour, an icon and a word (colour-blind safe). Critical alerts hold for 1 s to acknowledge. Voice is
push-to-talk because engine noise makes always-listening unreliable. [design.md, models.md §8]

## When models are wrong

**15. What happens when a model is wrong?**
No model acts on the machine: the worst a wrong prediction can do is give bad advice, and each one shows its reason.
Task times come as a range with the top factors. Maintenance risk names the component and the signals behind it. A
re-plan is a suggestion the operator or manager accepts or rejects. The rules run separately from the models, so a
threshold breach still alerts whatever the model says. If an input is missing, the prediction degrades rather than
failing: the health score just adds no penalty, and the planner falls back to the stored estimate. [models.md]

**16. Predictive maintenance hour-level recall is 0.44 against a 0.75 target. Is it useful?**
Yes, at the failure level. It warned before **12 of 15** unseen failures, with a median **37 engine hours** of lead
time (target 12 h), at 0.16 false alarms per machine-week. Hour-level recall is low because the probability rises as
the fault builds, so it isn't above 0.5 for all 48 hours. It is weak on electrical (1 of 3) and undercarriage (1 of 2),
which had 1–2 training examples. A safety floor (any signal 6 σ worse than that machine's normal → at least medium)
brings **15 of 15** failures to medium risk. The cost is 4.7 % of normal hours at medium or above, up from 2.4 %.
[results.md §4]

**17. Why cross-validation instead of your test split? Did you choose what flatters you?**
The test days hold only 2 failures, too few for a verdict. So we used forward-chaining cross-validation grouped by
failure, which gives 15 held-out failures. Each fold trains only on the past, and the 48 h before each boundary is
removed, so the future can't leak. We also report the 2-failure test split: PR-AUC 0.77, 1 of 2 caught, 0 false alarms.
[ml/artifacts/maintenance/README.md]

**18. Your task estimates are biased late in the shift. Why ship them?**
We found the bias and we state it: late-shift and night estimates are **too optimistic**. Tasks still running at shift
end have no duration, so the long ones drop out of training (82 % of tasks that start after hour 7). Overall the model
still beats the baseline by a wide margin: 8.9 min average error against 23.8, with 80 % range coverage. The fix
is censored-duration training (treat an unfinished task as "at least this long"). [results.md §2]

## Model choices

**19. Why gradient boosting and Isolation Forest, not deep learning?**
Our data is tabular and small, with 4,946 training tasks and 25 failures. Tree models are the strongest choice for
that. They run on a CPU without a GPU, and SHAP gives exact per-prediction reasons in minutes or log-odds, which we show
to operators. The artifacts total under 8 MB, small enough for a cab computer. An LSTM autoencoder is on the stretch list to compare
against the Isolation Forest. It isn't built yet. [models.md, features.md P2]

**20. Why is your clustering score below target, and why not just set k = 5?**
ARI is **0.415** on unseen weeks, target 0.5. We know there are 5 personalities only because we generated them.
Choosing k = 5 for that reason would use the answer key, and on a real fleet nobody knows k. Silhouette picks
**k = 4**, so the two largest groups (efficient and average) share a cluster. Even a perfect 4-cluster answer that
merged only those two would score 0.58. Aggressive, idler and novice operators are recovered 100 % of the time
on both fit and unseen weeks. [results.md §3]

## Scale and deployment

**21. Does it scale to a fleet of hundreds of machines?**
The design keeps the database small. Minute telemetry streams over a WebSocket and isn't written through Realtime; we
store only alerts, health snapshots and predictions. Models are one per machine type (and idle/working state), not
per machine, so new machines need no retraining. Rules and graded response keep a small state per machine and alert.
To be honest, we have tested 12 machines' history and live replay of 2–4 machines at 10× speed, not a large fleet. A
real fleet needs a streaming ingest (for example a message queue) in place of our replay engine. [supabase.md §6,
models.md §1, roadmap.md]

**22. How accurate is the camera distance?**
One camera gives ±20–30 %. That's why the zones are wide (red < 3 m, orange 3–7 m) and add 2 m in low visibility or
high fatigue, and why production would take the smaller of the camera and ultrasonic distances. We measured frame
rate (~21–30 fps on a laptop CPU), not detection accuracy. A labelled test on site footage is next. [assumptions.md, results.md §5]

**23. What does the LLM do, and what if it makes things up?**
Three jobs: the training chatbot, handover summaries and turning a spoken incident into a form. The chatbot answers
only from our retrieved documents, cites the source, puts the safe action first, and says "I don't know, ask your
supervisor" otherwise. It will be tested on 25 questions with a target of 80 % correct and **zero unsafe answers**. The operator confirms every incident draft
before it's saved. The LLM never drives an alert or a machine action. [models.md §7, §9, features.md §2.6]

## Status and limits

**24. What actually works today, and what is mocked?**
Working: the data generator, all four trained models with result sheets, health score and re-planning, the vision
service (proximity, fatigue, phone), and the backend. The backend covers replay, rules with graded response, events,
live predictions, the maintenance and clustering jobs, and auth, with automated tests. The demo's scripted moments use scenario triggers
on replayed data, and we say so. As of 2026-09-24, the RAG chatbot, the voice pipeline, the handover LLM and connecting
the UI to live data were still in progress (`roadmap.md` Phases 2–4). Check the roadmap before the pitch and update
this answer. [roadmap.md]

**25. What are the biggest limits, and what would you do next?**
1. **Real data:** retrain and re-validate on real Cat telemetry. Thresholds should come from service manuals.
2. **Rare failures:** electrical and undercarriage need more failure history. Until then, the safety floor covers them.
3. **Task time bias:** censored-duration training to fix optimistic late-shift and night estimates.
4. **Anomaly model:** train on settled minutes so it catches more faults on its own.
5. **Vision:** measure detection accuracy on site footage, fuse with radar, run on a cab computer.
6. **Fleet scale:** streaming ingest in place of the replay, and field trials with operators for usability.
