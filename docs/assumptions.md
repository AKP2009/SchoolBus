# Assumptions

The problem statement allows "available or assumed data". This page states ours, so judges
see the choices were deliberate.

## Machines and sensors
Machines already report hours, fuel, idle time, location and fault codes through telematics
(Cat Product Link / VisionLink provide this kind of data). We assume these additional sensors:

| Sensor | Gives us | Used by |
|---|---|---|
| Engine and hydraulic sensors | rpm, load, temperatures, pressures, fuel rate | Rules, anomaly, maintenance, twin |
| Battery monitor | voltage | Rules, twin |
| Vibration sensor | vibration RMS | Anomaly, maintenance |
| IMU | pitch, roll | Tip-over warning |
| Seatbelt switch | fastened / not | Seatbelt compliance |
| 4 exterior cameras | front, rear, left, right video | Proximity, blindspot |
| Ultrasonic / radar | distance to nearest object per sector | Proximity (fused with camera) |
| Cab camera | operator face | Fatigue, phone use |
| Cab tablet with mic and speaker | UI, voice | Everything |

## Thresholds
All thresholds (temperatures, pressures, tilt angles, distances) are **illustrative values
we chose**, not Caterpillar specifications. In production they come from the machine model's
service manual and are configured per machine type. They live in one config file
(`backend/app/alerts/thresholds.yaml`) for that reason.

## Camera distance
Distance from one camera uses a pinhole model with assumed real heights: person 1.7 m, car 1.5 m,
truck 3.0 m. It is a rough estimate (±20–30 %), which is why the zones have wide margins and why we
fuse with the assumed ultrasonic sensor (`min` of the two) in production.

## Data
- All data is synthetic (see `synthetic_data.md`), 1-minute telemetry, 2 sites, 12 machines, 20 operators, 90 days.
- Fault codes are our own, not Caterpillar codes.
- Operator names and personalities are fictional.

## Environment
- Site connectivity is intermittent → offline-first operator app.
- Operators wear gloves; cab vibrates; screens face glare and night → large targets, dark cab theme, voice.
- Operators speak English, Hindi or Tamil.
- Shifts: day 06:00–14:00, night 18:00–02:00.

## Safety principle
The system advises and escalates; it does not take control of the machine. Any automatic
intervention in production would follow Caterpillar's own machine-control safety systems.

## Privacy
Cab camera frames are processed on the device and never stored; only derived numbers
(EAR, PERCLOS, events) are saved. Operators can see their own fatigue and safety data.
