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

Implementation notes (tasks): quantity targets `T ~ U(20, 120)` min of *base* time
(`quantity = T / 60 * base_rate`), so the realised duration keeps the full hidden formula —
material and slope are genuinely in it (rock is really ~+40%) and the models can learn them.
Tasks never start at or after the machine's failure time, so the failing shift just runs
fewer tasks. Tasks on `future_days` shifts keep only the plan (status `scheduled`, chain
spaced by the nominal target).

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

## Implementation decisions (part 1: master data, weather, shifts, maintenance, health)
- **RNG:** one `numpy.random.Generator` per module, `default_rng([seed, crc32(module)])`, so
  output is byte-identical per seed and editing one module doesn't shift another's draws.
- **`future_days`** (config, default 1): extra planned days after the history. They get weather,
  shifts and health, but no failures or services. "Now" for `machines` state = 06:00 local on the
  first future day.
- **Engine hours:** the engine runs for the whole shift. `machines.total_engine_hours` is the value
  at "now"; hours at `start_date` were 2,000–10,500, and older machines have more.
- **Roster:** each operator has a fixed weekly rest day, plus a 2% chance of leave on any day, giving
  ~5.8 shifts/week. Night crew = round(35% of available operators), filled first by whoever worked
  the night before (no night → day turnaround), then by operators with `preferred_shift = night`.
  Each operator has a usual machine.
- **Failures** are placed first, inside candidate shifts: day ≥ 10, ≥ 25 days apart on the same
  machine (so drift windows never overlap a previous repair), 1–3 per machine. Component counts
  follow the mix exactly (largest remainder): no undercarriage failures on wheeled machines, and no
  hydraulics failures on trucks, because their hydraulic pressure is 0. The failing shift's
  `end_time` = failure time. Other shifts overlapping the 4–24 h downtime are dropped.
  `downtime_hours` is on the `failure` row; the `repair` row has 0 and carries `cost_inr`.
- **Scheduled service** (`component = 'other'`) happens right after the shift in which
  hours_since_service reaches 500. Its downtime is 2–4 h, capped at the gap before the next shift.
- **`machine_health_daily.csv`** (helper, not a DB table) is evaluated at the end of each local day:
  baseline U(0.9, 1.0) − 0.005·age − Σ 0.4·p² over un-repaired failures.
- **Weather grid:** top of each UTC hour, from `start_date` through `start_date + days + future_days`
  inclusive. Visibility: 8,000 m dry, 6–8 km light rain, 4–6 km moderate rain, 1.5–4 km heavy rain
  (≥ 7.6 mm/h). Dust is 0.1 while it rains. Rain is rounded to 2 decimals *before* the visibility
  band is chosen, so the CSV is self-consistent (no 7.6 mm/h rows with moderate-rain visibility).
- **Personality** is drawn independently of experience. Nothing in part 1 reads it except validation.

## Output files
`data/output/{table}.csv` for small tables, `telemetry.parquet` (full), plus
`validation.html` (`python data/generator/validate.py`). `load_to_supabase.py --days 14`
loads the last 14 days.
`machine_health_daily.csv` is a generator-only helper (not a DB table): per machine and local
day, baseline − age − drift; it feeds the task formula (f_health) and the telemetry drift.
`tasks_truth.csv` (task_id + every duration factor + the noise) is validation-only:
never load it into the database, never use it as a model feature.
`future_days` (config, default 1) adds planned-only day(s) after the history — weather, shifts
and tasks (status `scheduled`), no failures/services; they exist for the live demo.
