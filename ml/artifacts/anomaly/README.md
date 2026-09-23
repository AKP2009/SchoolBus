# Anomaly model v1 — result sheet

One Isolation Forest per machine type on 31 rolling-window telemetry features (models.md §1),
plus a sensor-glitch rule. Trained on days 1–70 (failure drift windows removed), tested on days
81–90 against the 87 injected anomalies in that period. Notebook: `ml/01_anomaly.ipynb`.

**In plain words:** The model can tell a broken sensor from a broken machine. It caught all 19
sensor glitches instantly, where the rule engine would have raised a false critical alarm. On its
own it misses too many real faults: 62% of events against a target of 80%. The rule engine already
catches most of them faster. Rules and model together catch every test event (100%) with a median
2-minute delay.

![recall and delay per anomaly type](recall_delay.png)

| Test, days 81–90 | Events caught | Median delay | False alerts / 100 machine-h |
|---|---|---|---|
| Rules only | 77% | 6 min | 91 |
| Model only | **62%** ✗ (target ≥ 80%) | **3 min** ✓ (target ≤ 5) | 50 |
| Rules + model (ships) | 100% | 2 min | 138 |

The model's 3-minute median is flattered by the 19 glitches it catches at 0 minutes. For real
faults, its per-type medians are 9–49 minutes for four of five types.

## Why the model misses (feature list and parameters as specified)
| Type | Recall / delay | Why |
|---|---|---|
| excessive_idle | 24% / 49 min | The signature (1300–1500 rpm, 6–8 L/h while idle) is how the *idler* operators normally idle (1200–1400 rpm, 5–7 L/h). That idling is in the training data as normal, and the model has no per-operator baseline. |
| unsafe_operation | 27% / 0 min | 57% of events are the tilt variant (pitch/roll 15–22° at speed). Pitch, roll and speed are not in the §1 feature list, so the model can't see them. The harsh-lever variant looks like hard digging. |
| hydraulic_leak | 50% / 12 min | A 35–60% pressure drop from a signal that normally swings 180–320 bar (20–40 at idle) stays inside the normal range. The oil temperature rise is slow. |
| battery_fault | 75% / 9 min | Only one signal moves (3 of 31 features). The forest picks split features at random, so a one-feature outlier is isolated late. The 5-min mean and the 3-min persistence rule add delay. |
| overheating | 100% / 18 min | Coolant climbs 1 °C/min from ~88 °C and needs ~15 min to leave the normal 78–99 °C band. |

**Common cause:** each model pools working and idle minutes, and their normal ranges differ widely.
61% of the model's false-alert minutes fall within 5 minutes of an idle↔working switch, against
27% of all normal minutes.

**Tuning round (validation only, one round):** lowering the threshold from the spec 0.97 train
quantile to 0.85 lifts validation recall from 0.53 to only 0.74, while false alerts rise from
50 to 136 per 100 machine-h. `hydraulic_leak` stays at 0.54. We kept the spec threshold.

## Method notes
- **Split:** time-based, 70 / 10 / 10 days. The scaler and forest are fitted on training minutes
  outside every failure drift window. Drift windows are rebuilt in engine hours and match the
  `pre_failure_*` labels on 37,142 of 37,143 rows.
- **Sensor glitch:** exactly one signal outside its sensor validity range (for example coolant
  0/150 °C, battery 0 V, or oil pressure 0 kPa while running), where that signal was plausible the
  minute before. The reading is replaced by the previous value before features are computed.
  **Real fault:** a model flag that persists ≥ 3 min, or that moves ≥ 2 signals with |z| ≥ 3.
- **Metrics:** an event counts as caught if a correctly classified alert fires between its start
  and 5 minutes after its end. Minute-level precision/recall/F1 per type (type vs normal minutes)
  are in `metrics.json`. `pre_failure_*` labels are excluded (they belong to predictive maintenance).
- `anomaly_label` / `anomaly_type` are never features. `score_anomaly()` gives the same score as
  the batch pipeline (checked on 25 random test minutes).
