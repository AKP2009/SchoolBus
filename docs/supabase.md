# Tech stack and Supabase setup

## Stack

| Layer | Choice | Why |
|---|---|---|
| Database | **Supabase Postgres** | One place for all data, SQL, free tier is enough |
| Auth | **Supabase Auth** (email + password) | Operator and manager logins, roles via `profiles` |
| Live updates | **Supabase Realtime** (postgres_changes) | Alerts, incidents, tasks push to both apps instantly |
| Files | **Supabase Storage** | Incident photos, training videos, RAG source docs |
| Vector search | **pgvector** in Supabase | RAG chatbot without a separate vector DB |
| ML + backend | **FastAPI** (Python 3.11) | Models are Python; Supabase Edge Functions run Deno and can't load scikit-learn/LightGBM |
| Telemetry stream | FastAPI **WebSocket** | Minute-by-minute replay is high-frequency; keeping it out of Realtime keeps the DB small |
| Vision | Python service: OpenCV, Ultralytics YOLO, MediaPipe | Runs locally next to the webcam |
| Voice | faster-whisper, Piper TTS, LLM API | Local STT/TTS, LLM for intent |
| Frontend | React 18 + Vite + TypeScript + Tailwind | Fast to build; one app with `/operator` and `/manager` |
| Offline | vite-plugin-pwa (Workbox) + IndexedDB (`idb`) | Installable operator app with sync queue |
| Maps | Leaflet + react-leaflet, CARTO tiles | Free, simple |
| Charts | Recharts | Enough for all dashboard charts |
| ML libs | pandas, scikit-learn, LightGBM, XGBoost, SHAP | See `models.md` |
| Hosting (demo) | Supabase cloud · FastAPI local or Render/Railway · web on Vercel/Netlify | |

## Who talks to Supabase how

| Client | Key | Does |
|---|---|---|
| Web app | anon key + user JWT | Reads data under RLS, writes incidents/task updates/training progress, subscribes to Realtime |
| FastAPI | **service role key** | Writes alerts, predictions, health snapshots, handover summaries; reads everything |
| Vision service | none | Posts events to FastAPI, which writes to Supabase |
| Generator/loader | service role key | Bulk loads CSVs |

The service role key bypasses RLS. It exists only in `backend/.env` and `data/.env`. Never in the web app.

---

## Setup

### 1. Create the project
- supabase.com → New project. Region: **Mumbai (ap-south-1)** for low latency.
- Save the project ref, URL, anon key, service role key, DB password.

### 2. CLI
```bash
npm i -g supabase            # or: brew install supabase/tap/supabase
supabase login
supabase init                # creates supabase/ (already in repo)
supabase link --project-ref <project-ref>
supabase db push             # applies supabase/migrations/*.sql
supabase gen types typescript --linked > web/src/types/supabase.ts
```
Local development (optional, needs Docker): `supabase start` runs the full stack locally.

### 3. Environment files
```bash
# web/.env
VITE_SUPABASE_URL=https://<ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon>
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000

# backend/.env
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role>
DATABASE_URL=postgresql://postgres.<ref>:<pw-url-encoded>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
LLM_API_KEY=<key>
```
Commit `.env.example` files, never `.env`.

Use the **Session pooler** URL (Dashboard → Connect), not `db.<ref>.supabase.co`: the direct host is
IPv6-only on the free tier and unreachable from most home networks. URL-encode special characters
in the password (`#` → `%23`).

### 4. Auth
- Auth → Providers: enable Email, **disable email confirmation** for the demo.
- Demo accounts created by `backend/scripts/create_demo_users.py` using the admin API, with
  metadata that the `handle_new_user` trigger copies into `profiles`:
```python
supabase.auth.admin.create_user({
  "email": "ganesh@demo.site", "password": "demo1234", "email_confirm": True,
  "user_metadata": {"role": "operator", "full_name": "Ganesh Nair", "operator_id": "OP02", "site_id": "S1"}
})
```
- Create: 2–3 operators (one per demo persona), 1 manager, 1 admin.
- Accounts (all password `demo1234`, run `python backend/scripts/create_demo_users.py`, idempotent):

  | Email | Role | Name | operator_id | site | Language |
  |---|---|---|---|---|---|
  | ganesh@demo.site | operator | Ganesh Nair | OP02 | S1 | en |
  | naveen@demo.site | operator | Naveen Rao | OP07 | S1 | ta |
  | vijay@demo.site | operator | Vijay Singh | OP11 | S2 | hi |
  | priya@demo.site | manager | Priya Menon | – | S1 | en |
  | admin@demo.site | admin | Demo Admin | – | – | en |

  Operator names must match `operators` (the script checks, so load data first). The trigger only
  fires on insert and doesn't set `preferred_language`, so the script also upserts each `profiles`
  row and then verifies role, operator_id and site_id.

### 5. Row Level Security
Enabled on every table in `001_init.sql`. Summary:
- Signed-in users read shared fleet data (sites, machines, tasks, telemetry, alerts…).
- Operators read and write only their own personal rows (incidents, training, chat, fatigue).
- Managers (`is_manager()`) read everything, manage geofences, verify outliers, update incidents.
- Backend uses the service role, so it isn't restricted.

Test policies by signing in as an operator and trying to read another operator's `fatigue_log` — it should return nothing.

### 6. Realtime
Enabled for `alerts`, `safety_events`, `incidents`, `tasks`, `machine_health_snapshots`,
`maintenance_predictions`.
```ts
supabase.channel('site-S1-alerts')
  .on('postgres_changes',
      { event: 'INSERT', schema: 'public', table: 'alerts', filter: 'site_id=eq.S1' },
      (payload) => pushAlert(payload.new))
  .on('postgres_changes',
      { event: 'UPDATE', schema: 'public', table: 'alerts', filter: 'site_id=eq.S1' },
      (payload) => updateAlert(payload.new))
  .subscribe()
```
Operator app filters by `machine_id=eq.<current machine>`. Realtime respects RLS.

**Telemetry is not sent through Realtime.** The FastAPI replay engine streams it over
`ws://…/stream/{machine_id}` (see `api_contract.md`) and writes only alerts and health
snapshots to the DB. This keeps Realtime traffic and DB size low.

### 7. Storage buckets
| Bucket | Public | Contents | Path pattern |
|---|---|---|---|
| `incident-media` | no | Incident photos | `{site_id}/{client_id}/{n}.jpg` |
| `training-content` | yes | Videos, PDFs, scenario images | `{module_id}/…` |
| `documents` | no | RAG source files | `{doc_type}/{file}` |

Storage policies live in `supabase/migrations/002_storage.sql`:
- `incident-media`: any signed-in user can upload and read.
- `training-content`: public reads go through public URLs; only managers (`is_manager()`) can insert, update or delete.
- `documents`: managers only, for select, insert, update and delete. RAG ingest uses the service role.

The core of it:
```sql
insert into storage.buckets (id, name, public) values
  ('incident-media','incident-media', false),
  ('training-content','training-content', true),
  ('documents','documents', false)
on conflict do nothing;

create policy "incident media upload" on storage.objects for insert to authenticated
  with check (bucket_id = 'incident-media');
create policy "incident media read" on storage.objects for select to authenticated
  using (bucket_id = 'incident-media');
```

### 8. pgvector
Extension enabled in the migration. Embeddings are 384-d (`bge-small-en-v1.5`), HNSW cosine index.
Ingest script: `backend/scripts/ingest_docs.py` — read file → chunk → embed → insert into
`documents` + `document_chunks`. Query from FastAPI:
```python
res = supabase.rpc("match_document_chunks",
                   {"query_embedding": emb.tolist(), "match_count": 4, "min_similarity": 0.3}).execute()
```

### 9. Loading synthetic data
```bash
python data/generator/load_to_supabase.py --days 14 --reset   # ~35 s, ~60 MB
python data/generator/load_to_supabase.py --estimate-only       # size check only
```
Order matters because of foreign keys: sites → operators → machines → shifts → weather →
tasks → maintenance_log → telemetry → safety_events → fatigue_log → incidents →
training_modules → training_records. Use Postgres `COPY` via psycopg (`DATABASE_URL`) for
telemetry — it is far faster than REST inserts.

How the loader behaves:
- One transaction: a failed run changes nothing. Needs the session pooler (5432), refuses 6543.
- Master tables (sites, operators, machines, training_modules) are complete and **upserted**,
  never truncated, so profiles and alerts that reference them survive a reload.
- `--reset` truncates the other tables; without it the loader refuses if they already hold rows.
- `shifts`, `maintenance_log` and `training_records` load complete (small; history predates the
  window). tasks, telemetry and fatigue_log are filtered by shift; weather, safety_events and
  incidents by timestamp. An incident's `linked_event_id` outside the window is set to null.
- Event rows keep the generator's ids; identity sequences are moved past max(id) afterwards.
- training_modules = the generator's 10 plus `TM-CYC-01` (time_ratio for load/haul/grade/backfill)
  and `TM-SIM-01` (scenario pack for the 'needs safety coaching' cluster), so every trigger in
  `models.md` §10 has a module. `TM-SIM-01.scenario` is read from `backend/kb/scenarios.json`
  (12 scenarios); the load stops if that file is missing or an `answer` index is out of range.
- Nothing from `data/output/truth/` is loaded. `telemetry.anomaly_label/anomaly_type` are loaded
  because they are schema columns (evaluation only, never features).
- Warns and stops if the projected database size is above 400 MB (`--force` overrides).

**Free tier limits to respect:** 500 MB database, 1 GB storage. Load 14 days of telemetry
(~100k rows), keep the full 90 days in Parquet for training. Don't store training videos
larger than ~20 MB each.

### 10. Scheduled jobs
Run in FastAPI with APScheduler (simpler than pg_cron for Python models):
| Job | Every | Writes | Status |
|---|---|---|---|
| Predictive maintenance scoring | 10 min of replay time | `maintenance_predictions` | built (`backend/app/jobs/maintenance.py`) |
| Fleet metrics + clustering | on demand (`POST /analytics/cluster`) / daily 01:00 IST | `fleet_metrics_weekly` | built |
| Vision alert expiry | 10 s | resolves vision alerts quiet for 30 s | built (`EventService.expire`) |
| Training recommendations | daily | `training_recommendations` | next task |
| Handover summary | at shift end | `shifts.handover_summary` | next task |

`backend/app/jobs/scheduler.py` builds an `AsyncIOScheduler` started in the FastAPI lifespan when
`SCHEDULER_ENABLED` (default true; the tests run without it). Every job has `max_instances=1`, `coalesce=True`.
The maintenance job ticks every 5 s of wall-clock time and scores a machine once its replay clock has moved
10 min; with no replay running it does nothing.

---

## Web client setup
```ts
// web/src/lib/supabase.ts
import { createClient } from '@supabase/supabase-js'
import type { Database } from '../types/supabase'
export const supabase = createClient<Database>(
  import.meta.env.VITE_SUPABASE_URL, import.meta.env.VITE_SUPABASE_ANON_KEY)
```

## Offline sync pattern
1. Operator action writes to IndexedDB queue `{ client_id, table, payload, created_at }`.
2. UI updates immediately from the local copy (clock icon = not yet synced).
3. On `online` event or every 30 s: flush queue with
   `supabase.from(table).upsert(payload, { onConflict: 'client_id' })`.
4. Remove from queue on success. Retries are safe because of the unique `client_id`.
5. Cache for offline reading: today's tasks, handover summary, downloaded training modules.
6. The queue is one IndexedDB store per origin, so every open tab flushes it. Queued writes are
   always the operator's: they are sent with the cab's Supabase session (`web/src/data/api.ts`
   `cabDb`), whichever tab flushes, and flushing waits until the cab is signed in on that device.

Production would use a CRDT library (Yjs) for concurrent edits; we only have append-style
writes, so an idempotent queue is enough.
