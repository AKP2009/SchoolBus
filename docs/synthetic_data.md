# Synthetic data generator

All data is ours. The generator builds it with **hidden cause-and-effect rules**, so the models
have something real to learn and we can prove they learned it (we know the ground truth).
Output columns match `docs/schema.md` exactly.

## Config (`data/generator/config.yaml`)
```yaml
seed: 42
start_date: 2026-06-01
days: 90
telemetry_interval_min: 1
sites:
  - { site_id: S1, name: "NH-48 widening, Vellore", site_type: highway, lat: 12.9165, lon: 79.1325 }
  - { site_id: S2, name: "Katpadi quarry", site_type: quarry, lat: 12.9692, lon: 79.1459 }
machines:            # per site
  excavator: 2
  wheel_loader: 2
  dozer: 1
  articulated_truck: 1
operators: 20
shifts: { day: ["06:00", "14:00"], night: ["18:00", "02:00"] }
tasks_per_shift: [4, 6]
anomaly_rate: 0.03
failures_total: 25
output_dir: data/output
```
(12 machines, 20 operators, 90 days ≈ 600k telemetry rows.)

## Generation order
1. `sites`, `machines`, `operators` (with hidden `personality`)
2. `weather` — hourly per site
3. `shifts` — assign operators to machines; night shifts ~35% of shifts
4. `maintenance_log` — scheduled services every `service_interval_hours`; place failures first
5. `tasks` — plan, then compute actual duration with the hidden formula
6. `telemetry` — normal signals → pre-failure drift → injected anomalies → labels
7. `fatigue_log`, `safety_events`, `incidents`
8. `training_modules` (seed list), `training_records`
9. `shifts.handover_notes` from templates using what happened in the shift
10. Validation report (`data/output/validation.html`)

## Master data

**Machines:** model by type: excavator `Cat 320`, wheel_loader `Cat 950`, dozer `Cat D6`,
articulated_truck `Cat 745`. `year` 2016–2024, `total_engine_hours` 2,000–12,000.

**Operators:** `experience_years` 1–20, `skill_score = clip(0.3 + 0.035*experience + N(0, 0.08), 0.1, 0.98)`,
`certification_level` from experience (<3 → 1, <8 → 2, else 3), languages from {en, hi, ta}.
Personalities (hidden, for clustering validation):

| Personality | Share | Behaviour |
|---|---|---|
| efficient | 25% | idle 6–10%, smooth, time ratio 0.85–0.95 |
| average | 40% | idle 12–18% |
| idler | 15% | idle 28–40%, high-RPM idling (1200+ rpm) |
| aggressive | 10% | harsh maneuvers 3× more often, fuel +15%, fast but more anomalies |
| novice | 10% | slow (time ratio 1.2–1.4), more seatbelt and proximity events |

## Weather (hourly)
- Temperature: daily sine, min 24 °C at 05:00, max 36 °C at 14:00, ± N(0, 1.5)
- Rain: Markov chain — dry→rain 0.04/h, rain→dry 0.25/h (gives multi-hour spells);
  intensity gamma(k=2, θ=2) mm/h when raining
- Visibility 8,000 m normally; 1,500–4,000 m in heavy rain; dust_index 0.1–0.3, up to 0.7 at the quarry when dry and windy

## Tasks — hidden duration formula
Base productivity (m³ or tons per hour):

| machine_type | task types | base rate |
|---|---|---|
| excavator | dig 110, trench 60, load 140 | per hour |
| wheel_loader | load 180, backfill 150 | |
| dozer | grade 90, backfill 120 | |
| articulated_truck | haul 70 tons/h at 1 km (scale by 1 km / haul_distance) | |

```
base_min      = quantity / base_rate * 60
f_skill       = 1.25 - 0.5 * skill_score
f_material    = {sand: 0.9, topsoil: 0.9, clay: 1.0, gravel: 1.05, rock: 1.4}
f_rain        = 1 + 0.015 * min(rain_mm, 20)
f_slope       = 1 + 0.02 * terrain_slope_deg
f_night       = 1.10 if night else 1.0
f_late        = 1 + 0.02 * max(0, hours_into_shift - 6)
f_health      = 1 + 0.3 * (1 - machine_health)
f_personality = efficient 0.92, average 1.0, idler 1.08, aggressive 0.9, novice 1.3
noise         = lognormal(mean=0, sigma=0.08)
actual_duration_min = base_min * f_skill * f_material * f_rain * f_slope * f_night * f_late * f_health * f_personality * noise
```
Quantities chosen so most tasks take 20–120 min. 5% of tasks get a random delay
(+15–60 min) with a `delay_reason` (waiting for truck, blocked access, refuelling).
`scheduled_start` = previous task end + 5–15 min.

## Telemetry — normal behaviour
Each minute has an activity state from the task timeline: `working`, `idle`, `travelling`, `off`.

| Signal | Working | Idle | Notes |
|---|---|---|---|
| engine_rpm | 1600–2000 | 800–1000 (idler: 1200–1400) | |
| engine_load_pct | 45–85 | 5–15 | |
| coolant_temp_c | 82–95 | 78–88 | + 0.15 × (ambient − 30) |
| engine_oil_temp_c | 90–105 | 80–92 | |
| oil_pressure_kpa | 280–420 | 150–250 | |
| hydraulic_pressure_bar | 180–320 (spiky) | 20–40 | 0 for trucks |
| hydraulic_oil_temp_c | 55–78 | 45–60 | slowly follows load |
| fuel_rate_lph | type-specific (excavator 14–20) | 2–4 (idler 5–7) | |
| fuel_level_pct | decreases by fuel_rate / tank size | | refuel at start of shift to 90–100 |
| battery_voltage | 27.2–28.4 | 26.8–27.8 | |
| vibration_rms_g | 0.3–0.7 | 0.1–0.2 | |
| ground_speed_kmh | 0–5 (truck 10–35 when travelling) | 0 | |
| pitch_deg, roll_deg | N(0, 2) + terrain_slope | | |
| gps | random walk within the site's work zone | fixed | |
| seatbelt_fastened | 99% true (novice 95%) | | |
| is_idle | false | true | |

Use AR(1) smoothing (`x_t = 0.85 x_{t-1} + 0.15 target + noise`) so signals look continuous.

## Pre-failure drift (what predictive maintenance learns)
For each failure at engine hour `t_f`, drift window `W ~ U(50, 200)` engine hours,
progress `p = clip((t − (t_f − W)) / W, 0, 1)`:

| Failed component | Drift |
|---|---|
| hydraulics | hydraulic_oil_temp_c += 15·p², hydraulic_pressure std × (1 + 2p) |
| engine | oil_pressure_kpa −= 120·p², vibration_rms_g += 0.6·p², coolant_temp_c += 8·p² |
| cooling | coolant_temp_c += 14·p² |
| electrical | battery_voltage −= 2.5·p² |
| undercarriage | vibration_rms_g while travelling += 0.8·p² |

Component mix: hydraulics 35%, engine 25%, cooling 15%, electrical 15%, undercarriage 10%.
Failure rows set `anomaly_label = true`, `anomaly_type = 'pre_failure_<component>'` only in the last 20% of W.
After failure: machine `down` 4–24 h, then repair row, signals reset to normal.

## Injected anomalies (≈3% of working minutes)

| anomaly_type | Duration | Signature |
|---|---|---|
| `overheating` | 20–40 min | coolant ramps +1 °C/min to 105–112, then cools after shutdown |
| `hydraulic_leak` | 10–30 min | hydraulic pressure drops 35–60% within 3 min, oil temp +8–15 °C |
| `excessive_idle` | 20–60 min | idle with rpm 1300–1500, fuel 6–8 L/h |
| `battery_fault` | 30–90 min | voltage sags to 22.5–23.8 |
| `sensor_glitch` | 1 min | one signal jumps to an impossible value (coolant 150 or 0), neighbours normal |
| `unsafe_operation` | 5–15 min | speed high while pitch/roll > 15°, or harsh load spikes |

Every injected minute gets `anomaly_label = true` and its `anomaly_type`. Add matching
`fault_code` for 50% of real faults (not for glitches).

## Fatigue and safety
```
perclos = clip(0.04 + 0.012*hours_into_shift + 0.04*night + N(0, 0.015), 0, 0.6)
```
Yawns and head-down events are Poisson with rates scaling with perclos. Fatigue level from the
formula in `models.md` §5.

Safety event base rates per operator-hour, multiplied by 2.5 when fatigue is high and by the
personality factor (novice ×2 for proximity/seatbelt, aggressive ×3 for harsh_maneuver):
proximity_breach 0.08, blindspot_intrusion 0.04, seatbelt_unfastened 0.02, phone_use 0.03,
harsh_maneuver 0.05, tip_risk 0.01, geofence_breach 0.01.
This is what produces the chart "most proximity breaches happen late in night shifts".

**Incidents:** 3% of critical safety events become incidents (mostly near_miss), plus 1 per failure (equipment_damage or spill_leak for hydraulics).

## Handover notes templates
Pick 1–3 based on what happened: "Hydraulic oil ran hot around {time}, kept load light."
/ "Refuelled to {x}%." / "Left bucket teeth worn, check." / "Task {n} not finished, {q} m³ left."
/ "No issues." Include a typo sometimes — real notes are messy, and the LLM summary should cope.

## Fault code table (also goes into the RAG knowledge base)
| Code | Meaning |
|---|---|
| E-110 | High coolant temperature |
| E-215 | Low engine oil pressure |
| E-360 | Low hydraulic oil level |
| E-365 | High hydraulic oil temperature |
| E-410 | Low system voltage |
| E-520 | Seatbelt switch fault |
(Our own codes, not Caterpillar's.)

## Validation checks (must pass before handing off)
- Row counts match config; no nulls in required columns; FKs valid.
- Plots: one normal day per machine type; each anomaly type; one failure drift.
- Correlations: duration vs rain > 0, vs skill < 0; perclos vs hours_into_shift > 0.
- Anomaly rate 2.5–3.5%; 20–30 failures; every personality present.
- Baseline check: a simple linear model on tasks should reach R² > 0.6 (else the signal is too weak).

All of these run in `data/generator/validation.ipynb` (last cell fails if any check fails);
the executed notebook is exported to `data/output/validation.html`.

## Output files
`data/output/{table}.csv` for small tables, `telemetry.parquet` (full), plus
`validation.html`. `load_to_supabase.py --days 14` loads the last 14 days.
Ground truth that is not a schema table goes to `data/output/truth/` (never loaded, never a
feature): `anomaly_events.csv` (one row per injected event and per labelled pre-failure window,
with its fault code and details) and `failures.csv` (component, failure/repair time, engine
hours, drift window W).

## Sample mode
`python data/generator/generate.py --config data/generator/config.yaml --sample` writes
1 day for M01 (excavator) + M06 (articulated truck) at S1 to `data/output/sample/*.csv`:
no anomalies, no failures, and a day and a night shift for both machines. Master data is built
for the full config and then filtered, so IDs and attributes match the full dataset. Without
`--sample` the CLI builds the full 90-day run (~30 s).

## Implementation decisions (not specified above)
- **CSV format.** Column names, order, enums and numeric scales are parsed from
  `001_init.sql`, and every table is validated before it is written. Timestamps are ISO-8601 UTC.
  Arrays are Postgres literals (`{en,ta}`), jsonb is a JSON string. `created_at`/`updated_at` are
  left to DB defaults. Event tables carry an explicit `id` from 1 so `incidents.linked_event_id` can
  point at a real safety event; the loader must `setval` the identity sequences after `COPY`.
- **Seeds.** Each stage has its own stream, `default_rng([seed, crc32(stage)])`, so changing one
  stage doesn't shift the others. Output is byte-identical across runs.
- **Planner.** Quantities are sized to fill ~95% of the shift from what a planner knows: base
  rate, material, slope and the operator's skill. Personality, rain, night, lateness, health and
  noise are unknown to it. A task still running at shift end is `delayed` (no `actual_end`,
  `actual_duration_min` null) and tasks never reached are `cancelled`. Only `completed` tasks
  are training targets.
- **Telemetry states.** Gaps between tasks are `travelling` (repositioning). A truck's queue at
  the loader counts as `working`. If the work finishes early, the machine idles for 5 min and then
  goes `off`, with no rows, so personality idle shares (efficient 6–10 % … idler 28–40 %) stay
  separable. Idle is topped up to the personality's share with 2–8 min bursts.
- **Idle share for aggressive and novice.** The spec gives no idle share for these two. The
  sample used aggressive 12–18 % (same as average) and novice 14–20 %; in the full data
  aggressive and average then have the same idle_pct, which fails "every personality has
  distinct idle_pct". Now aggressive 9–13 % (keeps the machine busy) and novice 19–25 %
  (hesitates). Measured operator means: efficient 8.9, aggressive 12.0, average 15.5,
  novice 22.7, idler 35.3 %, with no overlap between personalities.
- **Staffing.** Machines are staffed in a random order each shift. With a fixed order, the
  ~30 days where operators run out (rest days, 10 h rest after a night shift) always left the
  trucks unstaffed (75 day shifts vs 88 for the rest). The same shortage keeps night shifts at
  ~30 % instead of 35 %.
- **AR(1) speed.** α = 0.85 only for coolant and engine oil temperature (thermal mass), and 0.97
  for hydraulic oil temperature. Throttle-following signals (rpm, load, pressures, fuel rate,
  voltage, vibration, speed) use α = 0.3. With 0.85, a short idle spell shows 1300+ rpm and
  6+ L/h, which is the `excessive_idle` anomaly signature.
- **Seatbelt.** Unbuckled minutes happen while idle, in whole idle stretches, until the share is
  1 % (novice 5 %). Each `seatbelt_unfastened` event (only while moving) also unbuckles
  1–5 moving minutes in telemetry, so the rule engine has something to fire on.
- **Fatigue.** yawns/min ~ Poisson(0.02 + 1.2·max(0, perclos − 0.08)), head-down/min ~
  Poisson(0.005 + 0.8·max(0, perclos − 0.08)). With these rates, late night shifts reach `high`
  often enough for the ×2.5 safety multiplier to show. A `fatigue_high` event fires when the level
  turns high, at most once per 60 min.
- **Safety event fields.** proximity/blindspot: distance U(0.8, 7) m, `critical` below 3 m
  (models.md §6 zones), 40 % approaching. `phone_use` has sector `cab`. `tip_risk` is critical.
  `harsh_maneuver` is 70 % info. All events are `resolved = true` with `alert_id` null (alerts come
  from the backend rule engine at replay time).
- **Maintenance.** Each machine gets one "last service" row before the start date. Scheduled
  services use component `other`. Machine health (for `f_health`) =
  clip(1 − 0.2·hours_since_service/interval − 0.4·p, 0.3, 1), where p is the drift progress of
  the machine's next failure at shift start. `machines.csv` holds the end-of-run state (all
  repaired, `active`).
- **Failure placement.** 25 failures, component drawn from the mix, placed at random on a random
  machine in engine-hour space (1–4 per machine). The whole drift window lies inside the run,
  windows on one machine never overlap, the next window starts ≥ 48 engine hours after the
  previous failure, and the last failure is ≥ 40 engine hours before the end (so the repair
  finishes). The failure minute is 20 min to 60 % into a shift: telemetry stops there, the running
  task becomes `delayed` and later tasks `cancelled`, both with delay_reason `machine breakdown`.
  Shifts starting during the 4–24 h downtime are dropped. The `failure` row carries
  `downtime_hours`; the `repair` row (at the end of downtime) carries `cost_inr` and the fix.
- **Drift details.** Engine hours per telemetry row = shift start hours + minutes/60, the same
  counting as maintenance. "Hydraulic pressure std × (1 + 2p)" scales the deviation from a
  15-min moving mean. Trucks have no hydraulic pressure signal, so on a truck a hydraulics failure
  shows only in oil temperature.
- **Anomaly rate definition.** The 2.5–3.5 % target (and `anomaly_rate: 0.03`) is the share of
  **all telemetry rows** carrying an **injected** anomaly (measured 3.00 %). `pre_failure_*`
  labels come on top: 25 failures × 20 % of a 50–200 h window is ~5.7 % of rows, so
  `anomaly_label` is true on ~8.7 % of rows. The anomaly model trains on data before failure
  windows (models.md §1), so these rows don't inflate its contamination estimate.
  **The anomaly model is evaluated on injected anomaly types only; pre_failure labels are
  evaluated by the predictive maintenance model.**
- **Anomaly placement.** Events are drawn by type share (overheating 18 %, hydraulic_leak 15 %,
  excessive_idle 20 %, battery_fault 12 %, sensor_glitch 20 %, unsafe_operation 15 %), then placed
  on a shift weighted by its length and the operator's personality (aggressive ×2 overheating and
  hydraulic_leak, ×3 unsafe_operation; idler ×3 excessive_idle; novice ×2 unsafe_operation).
  Each event keeps 15 normal minutes on both sides and never overlaps another event or a labelled
  pre-failure window. Overheating, leaks and unsafe operation start on a working minute. No
  hydraulic_leak on trucks. ~810 events per run.
- **Anomaly shapes.** Every event returns to normal inside its labelled minutes (no unlabelled
  tail). `overheating`: coolant climbs ≥ 1 °C/min to 105–112, then the last ~30 % (≥ 8 min) is an
  idle cool-down (rpm ~900, `is_idle`) decaying back to normal; engine oil temperature follows at
  0.6×. `hydraulic_leak`: pressure falls 35–60 % over 3 min and stays down; oil temperature rises
  8–15 °C and falls back over the last 30 %. `excessive_idle`: `is_idle` with rpm 1300–1500,
  fuel 6–8 L/h, load 10–20 %, fixed GPS. `battery_fault`: 3-min sag to 22.5–23.8 V (±0.08 V
  noise), 3-min recovery. `sensor_glitch`: one minute, one of coolant 150 or 0, oil pressure 0,
  battery 0 V, hydraulic oil 150 °C. `unsafe_operation`: trucks and half of the others tilt (roll
  or pitch 15–22°) while moving fast (truck 25–35 km/h, others 3.5–6); the rest do harsh lever
  work (load alternating 95–100 / 35–50 %, hydraulic pressure 340–380 bar, vibration 0.9–1.4 g).
  Each unsafe_operation also logs a `tip_risk` or `harsh_maneuver` safety event. Fuel level is
  recomputed after injection.
- **Fault codes.** A coin flip per real fault (overheating, hydraulic_leak, battery_fault and
  each failure) decides whether it carries a code; the measured share is ~55 %. The code is set
  only on the rows past the fault threshold: E-110 while coolant > 105, E-360 from the 3rd minute
  of a leak, E-410 while voltage < 24, and for failures the last 10 % of W (engine E-215, cooling
  E-110, hydraulics E-365, electrical E-410, undercarriage none). sensor_glitch, excessive_idle
  and unsafe_operation never get a code. E-520 is unused.
- **Failure incidents.** One per failure, 5–30 min after it, `critical`, reported by `form`,
  with a `root_cause` naming the drifting signal: `spill_leak` for hydraulics (20–120 L of
  oil), `equipment_damage` otherwise.
- **Correlation checks** use duration per unit of work (`actual_duration_min / base_min`),
  because the planner already sizes quantity by skill: raw duration vs skill is only −0.05,
  per-unit is −0.50.
- **Speed.** AR(1) runs as `scipy.signal.lfilter` (same recurrence). The bounded random walks and
  GPS stay per-minute loops over plain floats. Events and handover look up a shift's telemetry
  rows through a groupby index. Full run ≈ 30 s (parquet + CSV writing ≈ 9 s).
- **Handover.** The "ran hot" note fires above 78 °C hydraulic oil (top of the normal range; the
  rule engine warns at 90). An extra template, "People walking near the machine, watch the {sector}
  side.", is used after ≥ 2 proximity/blindspot events. `issues_reported` holds tags such as
  `unfinished_task`. 25 % of shifts get one typo. A breakdown is always noted ("Machine broke
  down at {time}, …", tag `breakdown`). Faults the operator sees on the dash are noted 70 % of the
  time (`coolant_high`, `hydraulic_pressure_low`, `battery_low`), a sensor glitch 30 %
  (`sensor_glitch_suspected`); behaviour anomalies (excessive idle, unsafe operation) never.
- **Demo persona.** OP03 is always "Ravi Kumar" at S1, to match the demo login (supabase.md §4).
