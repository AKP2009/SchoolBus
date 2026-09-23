-- =====================================================================
-- Smart Operator Assistant — seed data.
-- The six technique modules referenced by the training recommender
-- (ml/inference/task_time.py; docs/models.md §10). Run after the
-- migrations (002 adds training_modules.sim_module_id).
-- Idempotent: safe to run repeatedly.
-- =====================================================================

insert into training_modules
  (module_id, title, topic, format, duration_min, difficulty, target_metric,
   machine_types, languages, sim_module_id)
values
  ('TM-TECH-DIG-01',
   'Digging technique: clean passes, full buckets',
   'dig', 'video', 10, 2, 'efficiency',
   '{excavator}', '{en,hi,ta}', null),
  ('TM-TECH-TRENCH-01',
   'Trenching: walls, depth and spoil placement',
   'trench', 'video', 10, 2, 'efficiency',
   '{excavator}', '{en,hi,ta}', null),
  ('TM-TECH-LOAD-01',
   'Loading trucks fast without spillage',
   'load', 'video', 8, 1, 'efficiency',
   '{excavator,wheel_loader}', '{en,hi,ta}', null),
  ('TM-TECH-HAUL-01',
   'Hauling: gear choice, speed and braking on site roads',
   'haul', 'video', 12, 2, 'efficiency',
   '{articulated_truck}', '{en,hi,ta}', null),
  ('TM-TECH-GRADE-01',
   'Grading passes that finish level the first time',
   'grade', 'video', 9, 2, 'efficiency',
   '{dozer}', '{en,hi,ta}', null),
  ('TM-TECH-BACKFILL-01',
   'Backfilling without rework',
   'backfill', 'video', 8, 1, 'efficiency',
   '{dozer,wheel_loader}', '{en,hi,ta}', null)
on conflict (module_id) do nothing;
