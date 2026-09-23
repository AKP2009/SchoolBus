"""FastAPI backend package.

Adds the repo root to sys.path so `ml.*` modules are importable when uvicorn runs
from `backend/` (e.g. `uvicorn app.main:app --port 8000`).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
