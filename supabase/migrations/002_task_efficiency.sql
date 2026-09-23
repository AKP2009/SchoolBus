-- =====================================================================
-- Smart Operator Assistant — migration 002: task efficiency columns,
-- technique-module chaining and the operator efficiency view.
-- Matches the metrics defined in ml/inference/task_time.py exactly.
-- Apply with: supabase db push   (after 001_init.sql)
-- =====================================================================

-- Task-time model outputs (Model 2) stored per task, per docs/schema.md
-- "model outputs are stored, not recomputed in the UI":
--   standard_min         = standard-operator p50 prediction (OOF for history)
--   expected_efficiency  = standard_min / predicted p50 for upcoming tasks
--   efficiency           = standard_min / actual_duration_min once completed
alter table tasks
  add column standard_min        numeric(7,1),
  add column expected_efficiency numeric(5,3),
  add column efficiency          numeric(5,3);

-- A training module can point at the scenario module that simulates it.
alter table training_modules
  add column sim_module_id text references training_modules (module_id);

-- ---------------------------------------------------------------------
-- v_operator_efficiency — one row per (operator_id, site_id, task_type).
-- Windows and rules mirror ml/inference/task_time.py:
--   efficiency = standard_min / actual_duration_min for completed, non-delayed tasks
--   as_of      = max(task_date) of completed tasks (NOT current_date: the data ends
--                before today)
--   avg window [as_of - 29, as_of], prev window [as_of - 59, as_of - 30]
--   avg_efficiency = duration-weighted = sum(standard_min) / sum(actual)
--   any metric with n < 3 is null
--   fleet_median = median efficiency of the site for that task_type in the avg window
--   trend = 'up' if avg >= 1.03 * prev, 'down' if avg <= 0.97 * prev, else 'flat'
--     (computed on unrounded values; output rounded to 3 dp)
-- ---------------------------------------------------------------------
create view v_operator_efficiency with (security_invoker = true) as
with eff as (
  select
    operator_id,
    site_id,
    task_type,
    task_date,
    (select max(t2.task_date) from tasks t2
     where t2.status = 'completed'
       and t2.actual_duration_min is not null
       and t2.delay_reason is null) as as_of,
    standard_min,
    actual_duration_min,
    standard_min / nullif(actual_duration_min, 0) as efficiency
  from tasks
  where status = 'completed'
    and actual_duration_min is not null
    and delay_reason is null
    and standard_min is not null
),
base as (
  select
    operator_id,
    site_id,
    task_type,
    (as_of - task_date) as age_days, -- >= 0 by construction: as_of is the max task_date
    standard_min,
    actual_duration_min,
    efficiency
  from eff
),
own as (
  select
    operator_id,
    site_id,
    task_type,
    count(*)                              filter (where age_days <= 29) as n_avg,
    count(*)                              filter (where age_days between 30 and 59) as n_prev,
    sum(standard_min)                     filter (where age_days <= 29) as std_avg,
    sum(actual_duration_min)              filter (where age_days <= 29) as act_avg,
    sum(standard_min)                     filter (where age_days between 30 and 59) as std_prev,
    sum(actual_duration_min)              filter (where age_days between 30 and 59) as act_prev
  from base
  group by operator_id, site_id, task_type
),
fleet as (
  select
    site_id,
    task_type,
    count(*) as n,
    percentile_cont(0.5) within group (order by efficiency) as med
  from base
  where age_days <= 29
  group by site_id, task_type
)
select
  o.operator_id,
  o.site_id,
  o.task_type,
  case when o.n_avg >= 3 then round(o.std_avg / nullif(o.act_avg, 0), 3) end as avg_efficiency,
  case
    when o.n_prev >= 3 then round(o.std_prev / nullif(o.act_prev, 0), 3)
  end as prev_efficiency,
  o.n_avg,
  o.n_prev,
  case when fl.n >= 3 then round(fl.med::numeric, 3) end as fleet_median,
  case
    when o.n_avg >= 3 and o.n_prev >= 3 then
      case
        when o.std_avg / nullif(o.act_avg, 0) >= 1.03 * (o.std_prev / nullif(o.act_prev, 0)) then 'up'
        when o.std_avg / nullif(o.act_avg, 0) <= 0.97 * (o.std_prev / nullif(o.act_prev, 0)) then 'down'
        else 'flat'
      end
  end as trend
from own o
left join fleet fl on fl.site_id = o.site_id and fl.task_type = o.task_type;

-- tasks RLS already applies through security_invoker; in Supabase the default privileges
-- grant select on public tables to authenticated.
grant select on v_operator_efficiency to authenticated;
