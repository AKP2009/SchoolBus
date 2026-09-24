# Design system

One product, two modes, one set of rules. Every screen uses the tokens in this file.
If you need something that isn't here, add it here first, then use it.

## Who we design for

**Cab mode (`/operator`)** — an operator in a vibrating cab, wearing gloves, glancing at a
tablet for one or two seconds between movements, often in sun glare or at night, sometimes
with poor connectivity. The screen must be readable at arm's length and never demand attention
it doesn't need.

**Office mode (`/manager`)** — a site manager at a desk or on a laptop in the site office,
scanning many machines, comparing, drilling down. Denser, but still calm.

## Principles

1. **Glance, don't read.** The most important state (am I safe, what's next, is the machine OK)
   is readable in under two seconds. Numbers are big; words are few.
2. **Silence is the default.** The screen is quiet when everything is fine. Colour and sound
   are spent only on things that need action, so they keep their meaning.
3. **Colour is never alone.** Every status has a colour **and** an icon **and** a word.
   About 1 in 12 men has a colour-vision deficiency; a red/green-only signal fails them.
4. **One alert owns the screen.** When something critical happens, it takes over. Lower alerts wait in a queue.
5. **Say what to do, not what broke.** "Lower the bucket and move to level ground" beats
   "HYD_OIL_HIGH stage 3".
6. **Same word everywhere.** If a button says "Report incident", the confirmation says
   "Incident reported", and the manager sees "Incident". Never switch terms.

---

## Colour

The palette comes from the job site: hi-vis saffron from machine paint and safety vests,
graphite from steel and night shifts, concrete grey from the site office. Status colours
come from safety signage.

### Brand and interaction

| Token | Hex | Use |
|---|---|---|
| `saffron-500` | `#F2A900` | Primary buttons, active tab, focus ring, selected state, brand mark |
| `saffron-400` | `#FFBE2E` | Hover on dark surfaces |
| `saffron-600` | `#C98C00` | Pressed; primary on light surfaces when more contrast is needed |
| `on-saffron` | `#1A1A1A` | Text and icons on saffron |

**Saffron is for interaction only. Never use it to mean "warning".** Warning has its own orange,
with an icon, so an operator never confuses "tap here" with "danger".

### Cab mode surfaces (dark)

| Token | Hex | Use |
|---|---|---|
| `cab-base` | `#15191E` | App background |
| `cab-surface` | `#1F252C` | Panels, task cards |
| `cab-raised` | `#2A323B` | Modals, pressed rows, gauge tracks |
| `cab-border` | `#3A444F` | Dividers, outlines |
| `cab-text` | `#EDEFF2` | Primary text |
| `cab-text-2` | `#A7B0BA` | Secondary text, units |
| `cab-text-3` | `#6E7985` | Disabled, placeholders |

Dark is the only cab theme: it avoids night glare in the cab and saves battery.
A high-brightness daylight variant (P2) swaps `cab-base` to `#2A323B` and raises text to `#FFFFFF`.

### Office mode surfaces (light)

| Token | Hex | Use |
|---|---|---|
| `office-base` | `#EEF1F4` | App background (concrete) |
| `office-surface` | `#FFFFFF` | Panels, tables |
| `office-raised` | `#F7F8FA` | Table header, hover row |
| `office-border` | `#D5DBE1` | Dividers |
| `office-text` | `#1B2128` | Primary text |
| `office-text-2` | `#55606B` | Secondary text |
| `office-text-3` | `#8A94A0` | Disabled |
| `office-header` | `#1F252C` | Top bar and hero strip (graphite, ties office to cab) |

### Status (safety signage)

| Status | Cab hex | Office hex | Icon (lucide) | Word |
|---|---|---|---|---|
| OK / clear | `#2FA36B` | `#1F8A57` | `check-circle` | "OK", "Clear" |
| Info | `#4C9EEB` | `#2A6FB8` | `info` | "Note" |
| Warning | `#FF7A1A` | `#D9620A` | `alert-triangle` | "Warning" |
| Critical | `#E5484D` | `#C62F35` | `octagon-alert` | "Critical" |
| Emergency | `#E5484D` + pulsing border | `#C62F35` + pulsing border | `siren` | "Emergency" |
| Offline / unknown | `#8A94A0` | `#8A94A0` | `wifi-off` / `circle-help` | "Offline" |

Status tints for backgrounds: the status colour at 14% opacity on the surface.

### Data visualisation

Categorical series (never reuse status colours for plain data):
`#F2A900` saffron · `#4C9EEB` steel · `#2BB3A8` teal · `#9B7BE0` violet · `#C9B28A` sand · `#8A94A0` slate.
Sequential (risk heatmaps): `#1F252C` → `#C98C00` → `#F2A900`.
Status colours appear in charts only when the chart is literally about status (e.g. alerts by severity).

### Contrast
All text meets WCAG AA (4.5:1 body, 3:1 for ≥ 24px). Saffron on `cab-base` ≈ 9:1.
Check any new pairing at webaim.org/resources/contrastchecker before using it.

---

## Typography

| Family | Role |
|---|---|
| **DM Sans** | Everything in the UI: headings, body, buttons, labels |
| **DM Mono** | Live sensor readings, machine and task IDs, timestamps in logs — where digits must not shift width |

Playfair Display is used only on the pitch deck title slides, never inside the product.

Load from Google Fonts: DM Sans 400/500/700, DM Mono 400/500. Fallbacks:
`"DM Sans", system-ui, -apple-system, "Segoe UI", sans-serif` and
`"DM Mono", ui-monospace, "SFMono-Regular", Consolas, monospace`.
Use `font-variant-numeric: tabular-nums` on any number that updates live.

### Cab mode scale (min body 18px)
| Token | Size / line-height | Weight | Use |
|---|---|---|---|
| `cab-display` | 56 / 60 | 700 (Mono 500 for readings) | Hero reading: time left, distance to person |
| `cab-h1` | 32 / 38 | 700 | Screen title, critical alert title |
| `cab-h2` | 24 / 30 | 500 | Card titles, alert body |
| `cab-body` | 20 / 28 | 400 | Default text |
| `cab-small` | 18 / 24 | 400 | Units, secondary info (nothing smaller in cab mode) |

### Office mode scale
| Token | Size / line-height | Weight | Use |
|---|---|---|---|
| `office-h1` | 28 / 34 | 700 | Page title |
| `office-h2` | 20 / 26 | 500 | Section title |
| `office-h3` | 16 / 22 | 700 | Card title |
| `office-body` | 15 / 22 | 400 | Default |
| `office-small` | 13 / 18 | 400 | Table meta, captions |
| `office-kpi` | 36 / 40 | 500 Mono | KPI numbers |

**Type rules:** sentence case everywhere (buttons, titles, labels). No all-caps labels.
No bold-one-word-in-a-headline tricks. Line length ≤ 75 characters for any paragraph.

---

## Space, shape, layout

**Spacing scale (px):** 4, 8, 12, 16, 24, 32, 48, 64. Cab mode uses 16 as the minimum gap between tappable things.

**Radius:** 6 for inputs and chips, 10 for cards and buttons, 16 for modals and the alert takeover.
Radius follows hierarchy — don't put the same radius on everything.

**Elevation:** cab mode uses surface steps (base → surface → raised), no shadows.
Office mode uses one shadow for floating layers only (menus, modals): `0 8px 24px rgba(21,25,30,0.18)`.

**Touch targets:** cab mode ≥ 64 × 64 px (gloves), office mode ≥ 40 × 40 px.

### Cab mode layout (tablet landscape, 1280 × 800 reference)
```
┌──────────────────────────────────────────────────────────────┐
│ status bar: machine M04 · shift 4h12m · fuel 62% · online ●   │  56px, always visible
├───────────────────────────────┬──────────────────────────────┤
│                               │                              │
│  PRIMARY ZONE                 │  SAFETY ZONE                 │
│  current task, time left,     │  top-down machine outline,   │
│  next task                    │  4 camera sectors, fatigue,  │
│                               │  seatbelt, tilt              │
│                               │                              │
├───────────────────────────────┴──────────────────────────────┤
│ [Tasks] [Machine] [Training] [Report]          (● Voice) (SOS)│  88px bottom bar
└──────────────────────────────────────────────────────────────┘
```
- Voice and SOS buttons are always visible on every cab screen. SOS is at the bottom right,
  never near a normal button, and requires a 1-second press-and-hold.
- Navigation lives at the bottom — easier to reach from the seat.
- Left-aligned text. Numbers right-aligned in lists.

### Office mode layout (1440 reference, 12-column grid, 24px gutters)
```
┌────────────────────────────────────────────────────────────────────────┐
│ graphite header: site selector · live alert count · user              │
├──────────┬─────────────────────────────────────────────────────────────┤
│ nav      │ page title                                                  │
│ Fleet    │ ┌─────────────── map (8 col) ─────────┐ ┌ alerts (4 col) ┐ │
│ Alerts   │ │                                      │ │ live feed      │ │
│ Health   │ │                                      │ │                │ │
│ Clusters │ └──────────────────────────────────────┘ └────────────────┘ │
│ Training │ ┌ maintenance risk (6) ┐ ┌ efficiency ranking (6) ┐       │
│ Settings │ └──────────────────────┘ └────────────────────────┘       │
└──────────┴─────────────────────────────────────────────────────────────┘
```

---

## Components

### Buttons
| Variant | Cab | Office |
|---|---|---|
| Primary | saffron fill, `on-saffron` text, 64px high | saffron fill, 40px |
| Secondary | `cab-raised` fill, `cab-text` | white fill, `office-border` outline |
| Destructive | critical fill, white text | critical outline, critical text |
| Ghost | text only, saffron | text only, `office-text` |

Labels are verbs that say what happens: "Start task", "Report incident", "Acknowledge".
Never "Submit", "OK", or "Click here".

### Alerts
| Level | Presentation | Sound | Dismiss |
|---|---|---|---|
| Info | Toast in the status bar area, 5 s | none | auto |
| Warning | Banner at top of primary zone, orange, icon + title + action | one short tone | "Got it" |
| Critical | **Takeover**: full-width panel over primary zone, red, big instruction, steps | repeating tone every 3 s until acknowledged | "Acknowledge" (hold 1 s) |
| Emergency | Takeover + pulsing border + manager notified | continuous | manager resolves |

Alert structure is always the same: **icon · title (what) · one-line instruction (what to do) ·
action button**. Example: ⚠ "Hydraulic oil hot — 94 °C" / "Switch to economy mode and reduce load" / [Got it].
Only one takeover at a time; others queue with a count badge.

### Gauges and readings
- Horizontal bar gauges (easier to glance than dials): track `cab-raised`, fill by status colour,
  value in DM Mono `cab-display` or `cab-h1`, unit in `cab-small`.
- Show the normal range as a faint band on the track.
- Trend arrow next to readings that are moving (↑ rising, ↓ falling).

### Task card
Title (task type + material) · quantity · predicted time "42 min" (Mono) with range "35–55" in
secondary text · top factors as small chips ("Rain +8 min") · state button ("Start task" / "Complete task").

### Safety panel (cab)
Top-down machine outline with four sectors. Sector fills: clear = no fill, orange 3–7 m,
red < 3 m, with the distance in metres inside the sector. Fatigue indicator: three-step bar
(low / medium / high) with a word. Seatbelt and tilt as icons with words.

### Digital twin
Flat side-view SVG of the machine, 5 regions (engine, cooling, hydraulics, electrical,
undercarriage). Regions filled with status tint, outline in status colour. Tap → drawer with
signals and a 60-minute sparkline.

### Maps (Leaflet)
Muted basemap (CARTO Positron for office, CARTO Dark Matter for cab). Machines as circles with a
type glyph, ring in status colour. Geofences: no-go as red hatch, pedestrian as blue outline,
speed zones as dashed saffron outline.

### Charts (Recharts)
No 3D, no gradients, no pies with more than 4 slices. Gridlines `office-border` at 50% opacity.
Axis labels `office-small`. Always label units. Tooltips show exact values in DM Mono.

### Empty, loading, offline, error
- **Empty:** say what will appear and how to get it. "No tasks yet. Your supervisor will assign today's work."
- **Loading:** skeleton blocks in `cab-raised` / `office-raised`, no spinners longer than 1 s without text.
- **Offline:** status bar shows `wifi-off` + "Offline — changes will sync". Queued items show a small clock icon.
- **Error:** what happened and what to do. "Couldn't load tasks. Check connection and pull to refresh." Never apologise, never vague.

---

## Motion
- Default transition: 150 ms ease-out for state changes (button press, drawer open).
- The **only** attention motion is the critical/emergency takeover: slide in 200 ms, then the
  border pulses at 1 Hz for emergency.
- No entrance animations on page load, no hover animations on cards.
- Respect `prefers-reduced-motion`: replace the pulse with a static thick border.

## Sound and haptics (cab)
- Warning: single 440 Hz tone, 200 ms. Critical: two-tone 880/660 Hz repeating every 3 s.
  Emergency: continuous alternating tone.
- Voice reads critical alert instructions aloud once, in the operator's language.
- Vibrate the tablet (where supported) on critical.

## Copy voice
Plain, calm, direct. Short sentences. Present tense. Say the number and the action.
"Person behind you — 2.4 m" not "Proximity hazard detected in rear sector".
Translations (Hindi, Tamil) keep the same structure; keep strings in `web/src/i18n/*.json`.

## Accessibility checklist
- Visible focus ring: 3px saffron outline, 2px offset.
- All icons have text labels or `aria-label`.
- Live alert region uses `aria-live="assertive"` for critical, `"polite"` otherwise.
- Nothing depends on colour alone. Minimum text sizes above are hard limits.

---

## Implementation

### CSS variables (`web/src/styles/tokens.css`)
```css
:root {
  --saffron-400:#FFBE2E; --saffron-500:#F2A900; --saffron-600:#C98C00; --on-saffron:#1A1A1A;
  --ok:#1F8A57; --info:#2A6FB8; --warning:#D9620A; --critical:#C62F35; --offline:#8A94A0;
  --bg:#EEF1F4; --surface:#FFFFFF; --raised:#F7F8FA; --border:#D5DBE1;
  --text:#1B2128; --text-2:#55606B; --text-3:#8A94A0; --header:#1F252C;
  --font-sans:"DM Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono:"DM Mono", ui-monospace, "SFMono-Regular", Consolas, monospace;
  --radius-sm:6px; --radius-md:10px; --radius-lg:16px;
}
[data-mode="cab"] {
  --ok:#2FA36B; --info:#4C9EEB; --warning:#FF7A1A; --critical:#E5484D;
  --bg:#15191E; --surface:#1F252C; --raised:#2A323B; --border:#3A444F;
  --text:#EDEFF2; --text-2:#A7B0BA; --text-3:#6E7985;
}
body { background:var(--bg); color:var(--text); font-family:var(--font-sans); }
.reading { font-family:var(--font-mono); font-variant-numeric:tabular-nums; }
```
Set `data-mode="cab"` on `<html>` for `/operator` routes and `data-mode="office"` for `/manager`
(the layouts do this). `data-mode` also works on any element, which is how `/styleguide` shows both modes.

**Added in `tokens.css` (web foundation):** `--on-status` (white text on a critical fill),
`--on-header` / `--on-header-2` (text on the graphite header, = `cab-text` / `cab-text-2`),
`--shadow-float`, `--series-1…6` and `--seq-1…3` (the data-vis colours above), and derived
`--tint-<status>` (status at 14%) and `--band` (gauge normal range), declared on `:root, [data-mode]`
so they resolve against the active palette. `tokens.css` is the only file in `web/src` with colour
values; the PWA manifest (`vite.config.ts`) and `public/icon.svg` repeat `cab-base` / `saffron-500`
because they can't read CSS variables.

### Tailwind (`tailwind.config.ts`)
```ts
theme: {
  extend: {
    colors: {
      saffron: { 400: 'var(--saffron-400)', 500: 'var(--saffron-500)', 600: 'var(--saffron-600)' },
      ok: 'var(--ok)', info: 'var(--info)', warning: 'var(--warning)',
      critical: 'var(--critical)', offline: 'var(--offline)',
      bg: 'var(--bg)', surface: 'var(--surface)', raised: 'var(--raised)', line: 'var(--border)',
      ink: { DEFAULT: 'var(--text)', 2: 'var(--text-2)', 3: 'var(--text-3)' },
    },
    fontFamily: { sans: ['var(--font-sans)'], mono: ['var(--font-mono)'] },
    borderRadius: { sm: 'var(--radius-sm)', md: 'var(--radius-md)', lg: 'var(--radius-lg)' },
  },
}
```
The implemented config **replaces** Tailwind's `colors` (not `extend`) so default palette classes such as
`bg-red-500` don't exist; it also adds the `cab-*` / `office-*` type scale as `text-cab-h1` etc.,
`min-h-touch-cab` (64px) / `min-h-touch-office` (40px), `bg-tint-<status>`, `bg-band`, `series-1…6`
and `header` / `header-ink` colours.
Icons: `lucide-react`, stroke width 2, 24px office / 32px cab.

**Added with the screens (feat/web-screens):**
- `DataState` wraps every data-backed block: skeleton while loading, error with "Try again", the
  screen's own empty text, and an "Offline — showing what was saved at HH:MM" line over cached data.
- `FleetMap` (Leaflet) is shared by the fleet, machine detail and geofence pages. Geofence and marker
  colours come from CSS classes (`.gf-nogo`, `.gf-pedestrian`, `.gf-speed` in `index.css`) because Leaflet
  writes SVG attributes, which can't read CSS variables. No-go zones use an SVG hatch pattern.
- **Basemap:** CARTO Positron now watermarks tiles without an API key, so maps use OpenStreetMap tiles
  desaturated with a CSS filter (`.basemap-muted`), which keeps the muted look.
- **Categorical chart order** (validated with the dataviz palette checker on white): steel `series-2`,
  `saffron-600`, teal `series-3`, violet `series-4`. The listed order put teal next to steel (ΔE 13 in
  normal vision, below the 15 floor) and saffron-500 is too light on white. Contrast against white is
  under 3:1, so categorical charts always add a marker shape per series, a worded legend and a table.
- `DigitalTwin` takes `machineType`: a dozer drawing (blade, push arms, hood, radiator, cab, tracks)
  besides the excavator; wheel loader and truck still use the excavator outline.
- `CabLayout` takes the alert queue, the banner and a status-bar toast as props (the operator shell
  feeds them from the live stream); nav has Tasks · Machine · Safety · Training · Report.
- Alert takeover steps are machine-aware (`lib/alertSteps.ts`: "Lower the blade" on a dozer).

## Do / don't
| Do | Don't |
|---|---|
| Use saffron for the one main action on a screen | Use saffron for warnings or decoration |
| Pair every status colour with icon + word | Rely on red vs green alone |
| Give numbers the biggest type on cab screens | Put paragraphs on cab screens |
| Keep the cab screen calm when all is OK | Animate or colour things that need no action |
| Write the action in every alert | Show raw codes like `HYD_OIL_HIGH` to operators |
| Use tokens | Hard-code hex values in components |
