-- =====================================================================
-- Smart Operator Assistant — initial schema
-- Source of truth for all table and column names.
-- Apply with: supabase db push
-- =====================================================================

create extension if not exists vector with schema extensions;
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------
create type user_role            as enum ('operator', 'manager', 'admin');
create type machine_type         as enum ('excavator', 'wheel_loader', 'dozer', 'articulated_truck');
create type machine_status       as enum ('active', 'idle', 'maintenance', 'down');
create type shift_type           as enum ('day', 'night');
create type task_type            as enum ('dig', 'trench', 'load', 'haul', 'grade', 'backfill');
create type material_type        as enum ('clay', 'sand', 'gravel', 'rock', 'topsoil');
create type task_status          as enum ('scheduled', 'in_progress', 'completed', 'delayed', 'cancelled');
create type severity_level       as enum ('info', 'warning', 'critical', 'emergency');
create type alert_source         as enum ('rule', 'anomaly_model', 'predictive_model', 'vision', 'operator', 'system');
create type alert_category       as enum ('internal', 'safety', 'maintenance', 'behaviour', 'emergency');
create type alert_stage          as enum ('warn', 'derate', 'recommend_shutdown', 'escalated', 'resolved');
create type safety_event_type    as enum ('seatbelt_unfastened', 'proximity_breach', 'blindspot_intrusion',
                                          'fatigue_high', 'phone_use', 'tip_risk', 'geofence_breach',
                                          'harsh_maneuver', 'overspeed', 'sos');
create type camera_sector        as enum ('front', 'rear', 'left', 'right', 'cab');
create type fatigue_level        as enum ('low', 'medium', 'high');
create type machine_component    as enum ('engine', 'hydraulics', 'cooling', 'electrical', 'brakes', 'undercarriage', 'other');
create type maintenance_event    as enum ('scheduled_service', 'inspection', 'repair', 'failure');
create type incident_type        as enum ('near_miss', 'collision', 'injury', 'equipment_damage', 'spill_leak', 'other');
create type incident_status      as enum ('open', 'investigating', 'closed');
create type report_channel       as enum ('form', 'voice', 'auto');
create type geofence_type        as enum ('no_go', 'pedestrian', 'power_line', 'trench', 'speed_limited');
create type training_format      as enum ('video', 'document', 'quiz', 'scenario', 'instructor_session');
create type recommendation_status as enum ('pending', 'accepted', 'dismissed', 'completed');
create type metric_entity        as enum ('operator', 'machine');

-- ---------------------------------------------------------------------
-- Reference tables
-- ---------------------------------------------------------------------
create table sites (
  site_id      text primary key,                  -- 'S1'
  name         text not null,
  site_type    text not null,                     -- 'highway', 'quarry', ...
  lat          double precision not null,
  lon          double precision not null,
  timezone     text not null default 'Asia/Kolkata',
  created_at   timestamptz not null default now()
);

create table operators (
  operator_id         text primary key,           -- 'OP01'
  site_id             text not null references sites,
  full_name           text not null,
  experience_years    numeric(4,1) not null check (experience_years >= 0),
  certification_level smallint not null check (certification_level between 1 and 3),
  languages           text[] not null default '{en}',
  preferred_shift     shift_type not null default 'day',
  skill_score         numeric(4,3) not null check (skill_score between 0 and 1),
  personality         text,                       -- synthetic hidden trait, never shown in UI
  created_at          timestamptz not null default now()
);

create table machines (
  machine_id             text primary key,        -- 'M01'
  site_id                text not null references sites,
  machine_type           machine_type not null,
  model                  text not null,           -- 'Cat 320'
  serial_no              text unique,
  year                   smallint,
  total_engine_hours     numeric(10,1) not null default 0,
  hours_since_service    numeric(8,1) not null default 0,
  service_interval_hours numeric(8,1) not null default 500,
  status                 machine_status not null default 'active',
  health_score           numeric(4,3) not null default 1 check (health_score between 0 and 1),
  created_at             timestamptz not null default now()
);

create table profiles (
  id                 uuid primary key references auth.users on delete cascade,
  role               user_role not null default 'operator',
  full_name          text,
  operator_id        text references operators,   -- set for operator accounts
  site_id            text references sites,
  preferred_language text not null default 'en',
  created_at         timestamptz not null default now()
);

create table geofences (
  id             bigint generated always as identity primary key,
  site_id        text not null references sites,
  name           text not null,
  zone_type      geofence_type not null,
  polygon        jsonb not null,                   -- GeoJSON Polygon
  speed_limit_kmh numeric(5,1),
  active         boolean not null default true,
  created_by     uuid references profiles,
  created_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Operations
-- ---------------------------------------------------------------------
create table shifts (
  shift_id              text primary key,         -- 'SH-2026-09-01-M01-D'
  site_id               text not null references sites,
  operator_id           text not null references operators,
  machine_id            text not null references machines,
  shift_type            shift_type not null,
  shift_date            date not null,
  start_time            timestamptz not null,
  end_time              timestamptz not null,
  fuel_start_pct        numeric(5,2),
  fuel_end_pct          numeric(5,2),
  handover_notes        text,
  issues_reported       text[],
  handover_summary      text,                     -- LLM output for the next operator
  handover_generated_at timestamptz
);
create index on shifts (machine_id, start_time desc);
create index on shifts (operator_id, shift_date desc);

create table weather (
  id           bigint generated always as identity primary key,
  site_id      text not null references sites,
  ts           timestamptz not null,
  temp_c       numeric(4,1),
  humidity_pct numeric(5,2),
  rain_mm      numeric(5,2),
  wind_kmh     numeric(5,1),
  visibility_m numeric(7,1),
  dust_index   numeric(4,2),                      -- 0–1
  unique (site_id, ts)
);

create table tasks (
  task_id             text primary key,           -- 'T-SH-2026-09-01-M01-D-1'
  site_id             text not null references sites,
  shift_id            text not null references shifts,
  machine_id          text not null references machines,
  operator_id         text not null references operators,
  sequence_no         smallint not null,
  task_date           date not null,
  task_type           task_type not null,
  material_type       material_type not null,
  quantity            numeric(10,2) not null,
  unit                text not null check (unit in ('m3', 'tons')),
  terrain_slope_deg   numeric(4,1) not null default 0,
  haul_distance_m     numeric(8,1),
  priority            smallint not null default 2 check (priority between 1 and 3),
  scheduled_start     timestamptz,
  predicted_p10_min   numeric(7,1),
  predicted_p50_min   numeric(7,1),
  predicted_p90_min   numeric(7,1),
  prediction_factors  jsonb,                      -- [{feature, impact_min}]
  actual_start        timestamptz,
  actual_end          timestamptz,
  actual_duration_min numeric(7,1),               -- training target
  status              task_status not null default 'scheduled',
  delay_reason        text,
  updated_at          timestamptz not null default now()
);
create index on tasks (operator_id, task_date);
create index on tasks (shift_id, sequence_no);

create table telemetry (
  id                     bigint generated always as identity primary key,
  ts                     timestamptz not null,
  machine_id             text not null references machines,
  operator_id            text references operators,
  shift_id               text references shifts,
  engine_rpm             numeric(6,1),
  engine_load_pct        numeric(5,2),
  coolant_temp_c         numeric(5,2),
  engine_oil_temp_c      numeric(5,2),
  oil_pressure_kpa       numeric(6,1),
  hydraulic_pressure_bar numeric(6,1),
  hydraulic_oil_temp_c   numeric(5,2),
  fuel_rate_lph          numeric(5,2),
  fuel_level_pct         numeric(5,2),
  battery_voltage        numeric(4,2),
  vibration_rms_g        numeric(5,3),
  ground_speed_kmh       numeric(5,2),
  pitch_deg              numeric(5,2),
  roll_deg               numeric(5,2),
  gps_lat                double precision,
  gps_lon                double precision,
  seatbelt_fastened      boolean,
  is_idle                boolean,
  fault_code             text,
  anomaly_label          boolean not null default false,   -- ground truth, NEVER a training feature
  anomaly_type           text                               -- ground truth
);
create index on telemetry (machine_id, ts desc);
create index on telemetry (ts);

create table machine_health_snapshots (
  id                 bigint generated always as identity primary key,
  machine_id         text not null references machines,
  ts                 timestamptz not null default now(),
  overall_score      numeric(4,3) not null,
  engine_score       numeric(4,3),
  hydraulics_score   numeric(4,3),
  cooling_score      numeric(4,3),
  electrical_score   numeric(4,3),
  undercarriage_score numeric(4,3),
  anomaly_score      numeric(6,4),
  details            jsonb
);
create index on machine_health_snapshots (machine_id, ts desc);

-- ---------------------------------------------------------------------
-- Alerts and safety
-- ---------------------------------------------------------------------
create table alerts (
  id              bigint generated always as identity primary key,
  ts              timestamptz not null default now(),
  site_id         text not null references sites,
  machine_id      text references machines,
  operator_id     text references operators,
  source          alert_source not null,
  category        alert_category not null,
  alert_code      text not null,                  -- 'COOLANT_HIGH', 'PROXIMITY_RED', ...
  title           text not null,
  message         text not null,
  recommended_action text,
  severity        severity_level not null,
  stage           alert_stage not null default 'warn',
  anomaly_score   numeric(6,4),
  evidence        jsonb,                           -- signal values, window, model output
  acknowledged_by uuid references profiles,
  acknowledged_at timestamptz,
  resolved_at     timestamptz
);
create index on alerts (site_id, ts desc);
create index on alerts (machine_id, ts desc);
create index on alerts (resolved_at) where resolved_at is null;

create table safety_events (
  id          bigint generated always as identity primary key,
  ts          timestamptz not null default now(),
  site_id     text not null references sites,
  machine_id  text references machines,
  operator_id text references operators,
  event_type  safety_event_type not null,
  severity    severity_level not null,
  distance_m  numeric(6,2),
  sector      camera_sector,
  approaching boolean,
  details     jsonb,
  alert_id    bigint references alerts,
  resolved    boolean not null default false
);
create index on safety_events (site_id, ts desc);
create index on safety_events (operator_id, ts desc);

create table fatigue_log (
  id               bigint generated always as identity primary key,
  ts               timestamptz not null,
  operator_id      text not null references operators,
  shift_id         text references shifts,
  ear_avg          numeric(5,3),
  perclos_60s      numeric(5,3),
  yawn_count       smallint default 0,
  head_down_events smallint default 0,
  phone_detected   boolean default false,
  fatigue_score    numeric(4,3),
  fatigue_level    fatigue_level not null
);
create index on fatigue_log (operator_id, ts desc);

create table incidents (
  id                bigint generated always as identity primary key,
  client_id         uuid not null unique,          -- generated on device, makes offline sync idempotent
  ts                timestamptz not null,
  site_id           text not null references sites,
  machine_id        text references machines,
  operator_id       text references operators,
  incident_type     incident_type not null,
  severity          severity_level not null,
  description       text not null,
  injury            boolean not null default false,
  damage_description text,
  root_cause        text,
  reported_via      report_channel not null default 'form',
  voice_transcript  text,
  ai_summary        text,
  linked_alert_id   bigint references alerts,
  linked_event_id   bigint references safety_events,
  media_paths       text[] not null default '{}',  -- Storage paths in 'incident-media'
  status            incident_status not null default 'open',
  created_by        uuid references profiles,
  created_at        timestamptz not null default now()
);
create index on incidents (site_id, ts desc);

-- ---------------------------------------------------------------------
-- Maintenance
-- ---------------------------------------------------------------------
create table maintenance_log (
  id                    bigint generated always as identity primary key,
  machine_id            text not null references machines,
  event_date            timestamptz not null,
  engine_hours_at_event numeric(10,1) not null,
  component             machine_component not null,
  event_type            maintenance_event not null,
  downtime_hours        numeric(6,1) default 0,
  cost_inr              numeric(12,2),
  notes                 text
);
create index on maintenance_log (machine_id, event_date desc);

create table maintenance_predictions (
  id                  bigint generated always as identity primary key,
  machine_id          text not null references machines,
  predicted_at        timestamptz not null default now(),
  horizon_hours       smallint not null default 48,
  failure_probability numeric(5,4) not null,
  likely_component    machine_component,
  top_factors         jsonb,                       -- [{feature, shap}]
  model_version       text not null
);
create index on maintenance_predictions (machine_id, predicted_at desc);

-- ---------------------------------------------------------------------
-- Fleet analytics
-- ---------------------------------------------------------------------
create table fleet_metrics_weekly (
  id                       bigint generated always as identity primary key,
  entity_type              metric_entity not null,
  entity_id                text not null,          -- operator_id or machine_id
  site_id                  text not null references sites,
  week_start               date not null,
  productive_hours         numeric(7,2),
  fuel_per_productive_hour numeric(6,2),
  idle_pct                 numeric(5,2),
  productivity_per_hour    numeric(8,2),           -- m3 or tons per productive hour
  time_ratio               numeric(5,3),           -- actual / predicted p50
  anomaly_count            integer default 0,
  safety_event_count       integer default 0,
  efficiency_index         numeric(6,3),
  cluster_id               smallint,
  cluster_label            text,                   -- 'efficient', 'idle-heavy', ...
  is_outlier               boolean default false,
  outlier_reason           text,
  rank_in_site             smallint,
  verified_by              uuid references profiles,
  verified_at              timestamptz,
  unique (entity_type, entity_id, week_start)
);

-- ---------------------------------------------------------------------
-- Training
-- ---------------------------------------------------------------------
create table training_modules (
  module_id     text primary key,                  -- 'TM-FUEL-01'
  title         text not null,
  topic         text not null,
  format        training_format not null,
  duration_min  smallint,
  difficulty    smallint check (difficulty between 1 and 3),
  content_path  text,                              -- Storage path or URL
  target_metric text,                              -- 'idle_pct', 'harsh_maneuver', ...
  machine_types machine_type[],
  languages     text[] not null default '{en}',
  scenario      jsonb                              -- for format = 'scenario': steps, choices, answers
);

create table training_records (
  id           bigint generated always as identity primary key,
  client_id    uuid not null unique,
  operator_id  text not null references operators,
  module_id    text not null references training_modules,
  started_at   timestamptz not null default now(),
  completed_at timestamptz,
  score        numeric(5,2),
  passed       boolean
);

create table training_recommendations (
  id             bigint generated always as identity primary key,
  operator_id    text not null references operators,
  module_id      text not null references training_modules,
  reason         text not null,                    -- shown to operator
  trigger_metric text,
  trigger_value  numeric,
  status         recommendation_status not null default 'pending',
  created_at     timestamptz not null default now()
);

create table training_sessions (
  id              bigint generated always as identity primary key,
  site_id         text not null references sites,
  module_id       text references training_modules,
  instructor_name text not null,
  starts_at       timestamptz not null,
  duration_min    smallint not null default 60,
  capacity        smallint not null default 6
);

create table session_bookings (
  id          bigint generated always as identity primary key,
  session_id  bigint not null references training_sessions on delete cascade,
  operator_id text not null references operators,
  status      text not null default 'booked' check (status in ('booked', 'cancelled', 'attended')),
  created_at  timestamptz not null default now(),
  unique (session_id, operator_id)
);

-- ---------------------------------------------------------------------
-- RAG
-- ---------------------------------------------------------------------
create table documents (
  id           bigint generated always as identity primary key,
  title        text not null,
  source       text,                               -- where it came from
  doc_type     text not null,                      -- 'manual', 'safety', 'faq', 'fault_codes'
  language     text not null default 'en',
  storage_path text,
  created_at   timestamptz not null default now()
);

create table document_chunks (
  id          bigint generated always as identity primary key,
  document_id bigint not null references documents on delete cascade,
  chunk_index integer not null,
  content     text not null,
  embedding   extensions.vector(384) not null,      -- bge-small-en-v1.5 / all-MiniLM-L6-v2
  metadata    jsonb,
  unique (document_id, chunk_index)
);
create index on document_chunks using hnsw (embedding extensions.vector_cosine_ops);

create table chat_messages (
  id          bigint generated always as identity primary key,
  session_id  uuid not null,
  operator_id text references operators,
  role        text not null check (role in ('user', 'assistant')),
  content     text not null,
  sources     jsonb,                               -- [{document_id, chunk_index, title}]
  created_at  timestamptz not null default now()
);
create index on chat_messages (session_id, created_at);

create or replace function match_document_chunks(
  query_embedding extensions.vector(384),
  match_count int default 4,
  min_similarity float default 0.3
)
returns table (id bigint, document_id bigint, content text, metadata jsonb, similarity float)
language sql stable
set search_path = public, extensions
as $$
  select c.id, c.document_id, c.content, c.metadata,
         1 - (c.embedding <=> query_embedding) as similarity
  from document_chunks c
  where 1 - (c.embedding <=> query_embedding) >= min_similarity
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

-- ---------------------------------------------------------------------
-- Model registry
-- ---------------------------------------------------------------------
create table model_runs (
  id            bigint generated always as identity primary key,
  model_name    text not null,                     -- 'task_time', 'anomaly', ...
  version       text not null,
  trained_at    timestamptz not null default now(),
  params        jsonb,
  metrics       jsonb,
  artifact_path text,
  is_active     boolean not null default false,
  unique (model_name, version)
);

-- ---------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------
create view v_machine_latest with (security_invoker = true) as
select distinct on (t.machine_id) t.*
from telemetry t
order by t.machine_id, t.ts desc;

create view v_open_alerts with (security_invoker = true) as
select * from alerts where resolved_at is null order by ts desc;

create view v_machine_health_latest with (security_invoker = true) as
select distinct on (h.machine_id) h.*
from machine_health_snapshots h
order by h.machine_id, h.ts desc;

-- ---------------------------------------------------------------------
-- Auth helpers and profile trigger
-- ---------------------------------------------------------------------
create or replace function public.auth_role() returns user_role
language sql stable security definer set search_path = public as $$
  select role from profiles where id = auth.uid()
$$;

create or replace function public.auth_operator_id() returns text
language sql stable security definer set search_path = public as $$
  select operator_id from profiles where id = auth.uid()
$$;

create or replace function public.is_manager() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce((select role in ('manager', 'admin') from profiles where id = auth.uid()), false)
$$;

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into profiles (id, role, full_name, operator_id, site_id)
  values (
    new.id,
    coalesce((new.raw_user_meta_data->>'role')::user_role, 'operator'),
    new.raw_user_meta_data->>'full_name',
    new.raw_user_meta_data->>'operator_id',
    new.raw_user_meta_data->>'site_id'
  );
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------
-- Row Level Security
-- The backend uses the service role and bypasses RLS.
-- The web app uses the anon key and these policies.
-- ---------------------------------------------------------------------
do $$
declare t text;
begin
  -- Shared reference / fleet data: any signed-in user can read
  foreach t in array array[
    'sites','operators','machines','geofences','shifts','weather','tasks','telemetry',
    'machine_health_snapshots','alerts','safety_events','maintenance_log',
    'maintenance_predictions','training_modules','training_sessions','documents','document_chunks'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy "read_authenticated" on %I for select to authenticated using (true)', t);
  end loop;

  -- Manager-only data
  foreach t in array array['fleet_metrics_weekly','model_runs'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy "read_managers" on %I for select to authenticated using (public.is_manager())', t);
  end loop;
end $$;

alter table profiles enable row level security;
create policy "own_profile" on profiles for select to authenticated using (id = auth.uid() or public.is_manager());

-- Operator-owned data: operator sees own rows, manager sees all
alter table fatigue_log enable row level security;
create policy "fatigue_read" on fatigue_log for select to authenticated
  using (operator_id = public.auth_operator_id() or public.is_manager());

alter table training_records enable row level security;
create policy "tr_read"   on training_records for select to authenticated
  using (operator_id = public.auth_operator_id() or public.is_manager());
create policy "tr_insert" on training_records for insert to authenticated
  with check (operator_id = public.auth_operator_id());
create policy "tr_update" on training_records for update to authenticated
  using (operator_id = public.auth_operator_id());

alter table training_recommendations enable row level security;
create policy "rec_read"   on training_recommendations for select to authenticated
  using (operator_id = public.auth_operator_id() or public.is_manager());
create policy "rec_update" on training_recommendations for update to authenticated
  using (operator_id = public.auth_operator_id());

alter table session_bookings enable row level security;
create policy "book_read"   on session_bookings for select to authenticated
  using (operator_id = public.auth_operator_id() or public.is_manager());
create policy "book_write"  on session_bookings for insert to authenticated
  with check (operator_id = public.auth_operator_id());

alter table chat_messages enable row level security;
create policy "chat_read"   on chat_messages for select to authenticated
  using (operator_id = public.auth_operator_id() or public.is_manager());

alter table incidents enable row level security;
create policy "inc_read"    on incidents for select to authenticated
  using (operator_id = public.auth_operator_id() or public.is_manager());
create policy "inc_insert"  on incidents for insert to authenticated
  with check (operator_id = public.auth_operator_id() or public.is_manager());
create policy "inc_update"  on incidents for update to authenticated using (public.is_manager());

-- App writes allowed on shared tables
create policy "tasks_update_own" on tasks for update to authenticated
  using (operator_id = public.auth_operator_id() or public.is_manager());
create policy "alerts_ack" on alerts for update to authenticated using (true);
create policy "safety_insert_sos" on safety_events for insert to authenticated
  with check (event_type = 'sos' and operator_id = public.auth_operator_id());
create policy "geofence_manage" on geofences for all to authenticated
  using (public.is_manager()) with check (public.is_manager());
create policy "fleet_verify" on fleet_metrics_weekly for update to authenticated using (public.is_manager());

-- ---------------------------------------------------------------------
-- Realtime
-- ---------------------------------------------------------------------
alter publication supabase_realtime add table
  alerts, safety_events, incidents, tasks, machine_health_snapshots, maintenance_predictions;
