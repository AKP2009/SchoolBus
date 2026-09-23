# Smart Operator Assistant for CAT Machinery

An intelligent companion for construction machine operators and the site managers who run them.
Built for the Caterpillar hackathon.

**For the operator (in-cab tablet):** today's tasks with realistic time estimates, a shift handover
brief, live machine health, proximity and blindspot warnings, fatigue-aware voice assistant,
incident reporting that works offline, and a training hub with a chatbot that knows the machine.

**For the site manager (web):** live fleet map, alerts and emergencies, maintenance risk,
and efficiency clusters that surface outlier machines and operators.

All data is synthetic, produced by our own simulator with deliberate cause-and-effect rules so
the models have real patterns to learn and we can prove they found them.

## Docs
| File | What's in it |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Rules and context for Claude Code |
| [docs/features.md](docs/features.md) | Every feature, how it works, priority |
| [docs/person_role.md](docs/person_role.md) | Who does what, and how |
| [docs/roadmap.md](docs/roadmap.md) | Ordered build plan with checkpoints |
| [docs/architecture.md](docs/architecture.md) | System diagram and data flows |
| [docs/models.md](docs/models.md) | Every model and rule: purpose, inputs, parameters, outputs |
| [docs/synthetic_data.md](docs/synthetic_data.md) | Data generator spec and hidden rules |
| [docs/schema.md](docs/schema.md) | Database schema (mirrors the migration) |
| [docs/supabase.md](docs/supabase.md) | Tech stack and Supabase setup |
| [docs/api_contract.md](docs/api_contract.md) | Endpoints, payloads, realtime channels |
| [docs/design.md](docs/design.md) | Design system: colours, type, components, rules |
| [docs/assumptions.md](docs/assumptions.md) | Assumed sensors, thresholds and constraints |
| [docs/demo_script.md](docs/demo_script.md) | The judged demo, step by step |

## Quick start
```bash
# 1. database
supabase link --project-ref <ref> && supabase db push
# 2. data
pip install -r data/generator/requirements.txt
python data/generator/generate.py --config data/generator/config.yaml
python data/generator/load_to_supabase.py --days 14
# 3. models
jupyter lab ml/          # run notebooks 01–05, artifacts land in ml/artifacts/
# 4. services
cd backend && uvicorn app.main:app --reload --port 8000
python vision/run.py --camera 0
cd web && npm install && npm run dev
```
Copy `.env.example` to `.env` in `backend/`, `vision/` and `web/` first.
