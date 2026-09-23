"""pytest bootstrap for backend tests: env defaults + import paths."""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
for path in (BACKEND_DIR, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Test defaults BEFORE app imports (env vars win over backend/.env).
os.environ.setdefault("DATA_SOURCE", "csv")
os.environ.setdefault("DEV_AUTH_BYPASS", "true")
os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "")
