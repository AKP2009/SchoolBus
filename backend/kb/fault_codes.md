# Fault codes

Fault codes shown on the cab display of machines in this project (excavators, wheel loaders,
dozers and articulated trucks). **These are our own demo codes, not Caterpillar codes.** All
temperatures, pressures and voltages in this file are **assumed thresholds we chose for the demo,
not Caterpillar specifications.** On a real machine, use the limits in that model's service manual.

Every code follows the same safety principle: the system warns and guides you, it never stops the
machine by itself. If a warning keeps going, the app moves through stages: warn → reduce power
(derate) → recommend a safe shutdown → notify the site manager.

## Quick list of all fault codes

**What to do now:** for any code, keep control first: lower any raised load, stop on level ground
and keep people clear. Then look up the code below.

| Code | Meaning | Machine system | How urgent |
|---|---|---|---|
| E-110 | High coolant temperature | Cooling | Act now, stop working soon |
| E-215 | Low engine oil pressure | Engine | Most urgent: stop the engine as soon as it is safe |
| E-360 | Low hydraulic oil level | Hydraulics | Act now, possible leak |
| E-365 | High hydraulic oil temperature | Hydraulics | Act now, lighten the load |
| E-410 | Low system voltage | Electrical | Finish safely, call maintenance |
| E-520 | Seatbelt switch fault | Cab safety | Wear the belt, report the switch |

## What does fault code E-110 mean? (High coolant temperature)

**What to do now:**
1. Lower the bucket or attachment to the ground. Finish the current movement first only if
   stopping mid-way is unsafe (for example, a load is lifted).
2. Move to level, firm ground away from people and edges.
3. Reduce load: stop digging or pushing. Let the engine **idle for 3–5 minutes** so coolant keeps
   flowing and the temperature comes down. Do not switch off a very hot engine straight from full load.
4. Then shut down the engine if the temperature does not fall.
5. **Never open the radiator or coolant tank cap while hot.** Pressurised coolant can cause severe
   burns. Wait until it is cool to the touch.
6. After cooling, you may check the coolant level and look for mud or dust packed in the radiator.

**Meaning:** The engine coolant is hotter than it should be. In our system the warning starts
when coolant stays above 100 °C for 2 minutes and becomes critical above 105 °C (our assumed
limits). Normal working temperature is roughly 82–95 °C.

**Likely causes:** radiator fins blocked by dust or mud, low coolant level, a slipping or broken fan
belt, a stuck thermostat, working hard for a long time on a very hot afternoon, or a failing
cooling component (a slow rise over many days points to this).

**When to call maintenance:** if the code comes back within the same shift, if the temperature
passed 105 °C, if coolant is low or leaking, if the fan belt is loose or broken, or if you see
steam. Report it in the app so the next operator knows.

## What does fault code E-215 mean? (Low engine oil pressure)

**What to do now:**
1. Lower the attachment to the ground and bring the machine to a safe stop on level ground.
2. **Shut down the engine as soon as it is safe to do so.** This is the most urgent code: low oil
   pressure can destroy the engine within minutes. Do not keep working "to finish the task".
3. Do not restart the engine to "see if it goes away".
4. Once stopped and cool, check the oil level on the dipstick and look under the machine for oil.

**Meaning:** The engine is not getting enough oil pressure. In our system this is critical when oil
pressure is below 100 kPa while the engine runs above 1200 rpm (our assumed limit). Normal is about
280–420 kPa when working and 150–250 kPa at idle.

**Likely causes:** low engine oil level, an oil leak, a worn oil pump, a blocked filter, wrong or
diluted oil, or a failing sensor. A slow drop over days along with more engine vibration points to
engine wear.

**When to call maintenance:** always, immediately. The machine should not be run again until a
technician has checked it, even if the oil level looks fine (the pump or sensor may be at fault).

## What does fault code E-360 mean? (Low hydraulic oil level)

**What to do now:**
1. Lower the bucket, boom or blade to the ground slowly and under control.
2. Stop the machine on level ground and shut down the engine. Running the pump with low oil damages it.
3. Warn people nearby: hydraulic oil makes the ground slippery and it is a fire risk on hot parts.
4. **Never check for a leak with your hand.** A pinhole leak under pressure can inject oil through
   the skin, which is a medical emergency. Look for wet spots, drips and oil mist only.
5. If oil is spilling onto the ground, contain it if you have a spill kit and report a spill incident.

**Meaning:** The hydraulic tank level is low. In our data this code often appears together with a
sudden hydraulic pressure drop, which usually means **a leak** (a burst hose, loose fitting or
damaged cylinder seal).

**Likely causes:** a leaking or burst hydraulic hose, a loose fitting, a damaged cylinder seal, or
oil not topped up after service.

**When to call maintenance:** always. Do not top up and carry on without finding the leak. If
anyone was hit by a jet of oil, treat it as an injury and press SOS.

## What does fault code E-365 mean? (High hydraulic oil temperature)

**What to do now:**
1. Reduce the load now: take smaller bucket loads and stop holding the controls at the end of
   stroke (pushing against the relief valve heats the oil fastest).
2. If the warning stays, lower the attachment to the ground, park on level ground and let the
   machine idle so the cooler can bring the oil down.
3. If it reaches critical, follow the safe shutdown steps shown on screen.
4. Do not touch hydraulic lines, the tank or the cooler: they can cause burns.

**Meaning:** The hydraulic oil is running too hot. In our system the warning starts above 90 °C and
becomes critical above 95 °C (our assumed limits). Normal working range is about 55–78 °C.

**Likely causes:** long periods of heavy digging or holding the relief valve (pushing against a
stop), a blocked hydraulic oil cooler, low hydraulic oil level, a hot day, or internal wear in
the pump or valves. A slow rise over days together with unsteady pressure is an early sign of a
hydraulic failure.

**When to call maintenance:** if the code returns in the same shift, if the oil cooler is blocked,
if the oil level is low, or if the machine feels slow or weak. Log "hydraulic oil ran hot" in your
handover notes.

## What does fault code E-410 mean? (Low system voltage)

**What to do now:**
1. Finish the current movement safely and avoid shutting down far from the workshop: the engine may
   not restart.
2. Switch off electrical loads you don't need (extra work lights, heater or air conditioning fan).
3. If lights, display or warning buzzers become weak or start flickering, stop in a safe spot. Do not
   travel at night without working lights.
4. Do not try to jump-start or disconnect batteries yourself unless you are trained; batteries can
   release explosive gas.

**Meaning:** The 24 V electrical system voltage is low. In our system the warning starts when the
voltage stays below 24.0 V for 5 minutes (our assumed limit). Normal is about 27–28.4 V with the
engine running.

**Likely causes:** a failing alternator or loose alternator belt, a weak or old battery, corroded
or loose battery terminals, or a large electrical load (lights, heater) with a weak charging system.
A slow drop over several days points to the alternator or battery.

**When to call maintenance:** within the shift. Ask for the battery, terminals and alternator
belt to be checked before the next shift.

## What does fault code E-520 mean? (Seatbelt switch fault)

**What to do now:**
1. **Keep wearing your seatbelt.** A faulty switch does not make it safe to work unbuckled.
2. Check that the belt latches and holds when you pull it firmly.
3. If the belt itself does not latch or is torn, do not operate the machine. It protects you inside
   the rollover protective structure (ROPS) if the machine tips.

**Meaning:** The seatbelt switch is giving a signal that doesn't make sense (for example, the
belt reads "unfastened" when it is fastened, or the other way round). The seatbelt warning
cannot be trusted while this code is active.

**Likely causes:** a damaged or dirty buckle switch, a broken wire under the seat, or a worn belt.

**When to call maintenance:** at the end of the shift for a switch fault; immediately if the belt
does not latch or is damaged.

## What should I do when any fault code appears?

**What to do now:**
1. Keep control of the machine first: lower any raised load, stop on level ground, keep people clear.
2. Read the code and message on the screen. Tap it in the app or ask the assistant "What does
   E-xxx mean?"
3. Follow the steps the app shows. If the app recommends a safe shutdown, do it.
4. Report the code in the app and in your handover notes, even if it cleared by itself.
5. If you are unsure whether it is safe to continue, stop and ask your supervisor.

## Can a fault code be a false alarm or sensor glitch?

**What to do now:** treat every code as real until proven otherwise: reduce the load, follow the safe
steps for that code and report it. Don't ignore it or wait to see if it goes away.

**Meaning:** sometimes it is a glitch. A sensor glitch looks like **one reading jumping to an impossible value for about a
minute** (for example coolant showing 150 °C or 0 °C) while everything else stays normal: other
gauges steady, no smell, no steam, machine behaves normally. The app labels these "possible sensor
glitch" at info level.

A **real fault lasts for several minutes and usually moves more than one reading together**, for
example coolant rising and engine oil temperature rising with it, or hydraulic pressure dropping
while hydraulic oil gets hotter.

Even if you think it is a glitch, report it: a sensor that glitches can hide a real problem later.
