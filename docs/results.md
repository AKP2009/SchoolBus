# Results

Every number here comes from `ml/artifacts/*/metrics.json` or the result sheets (`ml/artifacts/*/README.md`),
plus the measured fps in `docs/roadmap.md` for vision. Numbers are rounded as in the result sheets. All data is synthetic. Test = days 81–90,
never used for tuning. Targets are the ones we set in `models.md` before training.

## 1. Unusual behaviour: rules + anomaly model (test, 87 injected events)
**In plain words:** The system caught 84 of 87 machine problems, a median 2 minutes after they started. It
raised about one false alert per 5.7 machine-hours, and it never mistook a broken sensor for a broken machine.

| Test, days 81–90 | Events caught | Median delay | False alerts / 100 machine-h |
|---|---|---|---|
| Rules only | 64 of 87 (74 %) | 6.5 min | 16.9 |
| **Rules + model (ships)** | **84 of 87 (97 %)**, target ≥ 90 % ✓ | **2 min** | **17.5**, target ≤ 40 ✓ |
| Model only | 24 of 87 (28 %), target ≥ 80 % ✗ | 0 min | 0.6 |
| v1 rules + model (before tuning) | 87 of 87 (100 %) | 2 min | 138 |

- **Glitch detection:** 19 of 19 sensor glitches were labelled as glitches in the same minute. Rules alone label 0 of 19
  as glitches: a coolant reading of 150 °C would raise a false critical alarm.
- Tuning cut false alerts by 8× (138 → 17.5) and cost 3 events: 1 hydraulic leak and 2 harsh-lever unsafe operations.
- The model alone misses its target. Its job in the product is the glitch-vs-fault call. The rules catch most real faults.

## 2. Task time estimation (test, 716 completed tasks)
**In plain words:** Our estimate is off by about 9 minutes on average. The simple rule of thumb is off by
about 24 minutes. The range we show contains the real time for 8 tasks in 10.

| Test | MAE | MAPE | p10–p90 coverage |
|---|---|---|---|
| Baseline: median min/unit per task type × quantity | 23.8 min | 28.4 % | — |
| p50 model, raw intervals | 8.9 min | 10.6 % | 65.5 % |
| **p50 model, calibrated intervals (ships)** | **8.9 min** | **10.6 %** | **80.0 %** (target ≈ 80 %) |

- Calibrated: 9.6 % of tasks finish below p10 and 10.3 % above p90. Median range width is 33.8 min.
- Main misses: the 40 test tasks with a random +15–60 min delay (waiting for a truck, blocked access). Nothing in
  the inputs can predict these. On them MAPE is 31 % and coverage 10 %. On the rest, MAPE is 9.4 % and coverage 84 %.
- **Planted factors recovered: 4 of 6.** Rain (only when moved together with visibility), material, slope and skill
  come out in the right direction at 69–88 % of the planted size. Example: clay → rock, planted ×1.40, recovered ×1.287.
- **Not recovered: night shift and hours into shift.** A task still running at shift end has no duration, so it
  can't be a training target. That removes the long late-shift tasks: 82 % of tasks that start after hour 7, and
  11.4 % of night tasks against 8.0 % of day tasks. The model therefore learns "late tasks are short" (×0.936
  instead of ×1.03) and sees night at about half strength (×1.048 instead of ×1.10). **So late-shift and night
  estimates are too optimistic.** The fix is censored-duration training, which we haven't done.

## 3. Fleet clustering (operators, weekly metrics)
**In plain words:** Without being told, the clustering found the aggressive, idle-heavy and slow novice operators
in our data. It could not separate "efficient" from "average" operators.

| Personality (hidden) | Cluster it landed in | Fit weeks | Holdout weeks |
|---|---|---|---|
| aggressive | needs safety coaching | 20 of 20 | 6 of 6 |
| idler | idle-heavy | 30 of 30 | 9 of 9 |
| novice | low output | 20 of 20 | 6 of 6 |
| efficient | efficient | 49 of 50 (98 %) | 14 of 15 |
| average | split: efficient / low output | 53 / 27 (66 % efficient) | 13 / 11 |

- **Adjusted Rand index (ARI):** 0.451 on the fit weeks, **0.415 on unseen weeks**, 0.415 per operator. Target 0.5 ✗.
  Before tuning it was 0.370 / 0.363.
- **Why we didn't force k = 5:** we know there are 5 personalities only because we generated them. Choosing k = 5
  for that reason would use the answer key. Silhouette picks k from the data alone (k=3: 0.331, **k=4: 0.371**,
  k=5: 0.344), which is what would happen on a real fleet. With 4 clusters, the two largest groups share one.
  Even a perfect 4-cluster answer that merged only those two groups scores 0.58, so the index reads low even
  when every other group is found cleanly.
- Efficiency index by personality, in the expected order: efficient +0.49, average +0.09, aggressive −0.09,
  idler −0.58, novice −0.79.

## 4. Predictive maintenance (failure in the next 48 engine hours)
**In plain words:** On 15 failures it had never seen, the model warned before 12, with a median of about a day
and a half of engine time to spare. With the safety floor, all 15 reached at least medium risk.

| Cross-validation, 15 held-out failures | PR-AUC | Failures caught @0.5 | Median lead (p ≥ 0.6) | False alarms / machine-week |
|---|---|---|---|---|
| Random score (= share of positive hours) | 0.10 | — | — | — |
| Baseline rule (worst 72 h trend, overdue service) | 0.25 | 1 of 15 | 0 h | 0.15 |
| XGBoost v1 | 0.57 | 9 of 15 | 21 h | 0.26 |
| **XGBoost v2 + safety floor (ships)** | **0.69** | **12 of 15** | **37 h** (target ≥ 12 h ✓) | **0.16** |

- **Hour-by-hour recall at 0.5 is 0.44, below the 0.75 target ✗.** The model rises as the fault builds, so it isn't
  above 0.5 for all 48 hours.
- Reliable: hydraulics 5 of 5 caught, engine 3 of 3, cooling 2 of 2. Weak: electrical 1 of 3, undercarriage 1 of 2.
  The 3 misses were M01 and M02 electrical and M08 undercarriage.
- **Safety floor:** a signal 6 σ or more worse than that machine's own normal forces at least medium risk (0.35).
  Failures reaching medium go from 12 of 15 to **15 of 15**. The cost: normal hours at medium or above rise from 2.4 % to 4.7 %.
- We used cross-validation because the test days hold only 2 failures. On those 2 (days 81–90), PR-AUC is 0.77,
  1 of 2 caught, 0 false alarms. Two failures is anecdotal.
- **M04 backtest (electrical failure, 2026-08-25, not in training):** the model first reached medium risk
  (p 0.53, component electrical) **23 engine hours before the failure**, about 59 calendar hours. It crossed high
  (0.6) 21 engine hours before, and its peak was 0.89. The health view stays green until then, drops straight to red
  at −23.0 h and ends at 0.32 (critical). Over the last 48 engine hours: 1,353 minutes green, 327 orange, 1,065 red.
  The orange minutes at −47.3 h come from an unrelated short hydraulic leak. Quote 23 h for M04, not the 37 h median.

## 5. Vision (pretrained models, laptop CPU)
**In plain words:** A normal laptop webcam spots a person behind the machine, estimates how far away they are,
and warns before they're inside 3 m. A cab camera tracks the signs of drowsiness.

| | Proximity / blindspot | Fatigue and attention |
|---|---|---|
| Models | YOLO11n + ByteTrack (person, car, truck) | MediaPipe FaceLandmarker (478 points) + YOLO11n phone class |
| Measured speed | ~21–30 fps at 640 px | ~23–24 fps with phone detection on |
| Output | Zones: **red < 3 m (critical), orange 3–7 m (warning), clear > 7 m**. +2 m in low visibility or high fatigue. Moving closer at > 0.5 m/s raises severity one level. | **Signals:** eye closure (EAR), PERCLOS over 60 s, yawns (MAR > 0.6 for > 1.5 s), head down (< −20° for > 2 s), phone (3 s of detections). Hours into shift and night shift are added to the score. Eyes closed > 2 s while moving → critical. |
| Known limit | Distance from one camera is ±20–30 %, so production fuses it with ultrasonic/radar (take the smaller distance) | Eye-closure threshold is calibrated per person in the first 30 s (fallback EAR < 0.22); lighting changes can need a recalibration |

- Live check: a 20 s proximity run posted 20 `blindspot_intrusion` events, which became 20 `safety_events` rows on one alert.
- We have not measured detection accuracy (for example missed people per 100). The fps figures and the live check are
  all we have measured.

## Also measured (no training)
- **Graded response (backend, 2026-09-24):** overheating on M04 went warn → derate after 2 min → recommend shutdown
  after 3 min (value rising) → escalated after 5 min (not acknowledged) → resolved after 15 min, in data time.
- **Plan re-evaluation:** in 15 test shifts where rain starts mid-shift, the plan changes in 5 (one task moves each time).
  In the example shown, all three tasks still finished, so the rain estimate erred on the safe side.
