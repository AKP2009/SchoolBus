"""Print a Supabase access token for a demo login, for curl against the API.

    TOKEN=$(python backend/scripts/get_token.py)                    # priya@demo.site (manager)
    TOKEN=$(python backend/scripts/get_token.py ravi@demo.site)     # operator OP03
    curl -H "authorization: Bearer $TOKEN" localhost:8000/replay/status

Reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from backend/.env. Tokens last an hour.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
PASSWORD = "demo1234"  # create_demo_users.py


def main() -> None:
    load_dotenv(ENV_PATH)
    email = sys.argv[1] if len(sys.argv) > 1 else "priya@demo.site"
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    session = client.auth.sign_in_with_password({"email": email, "password": PASSWORD}).session
    if session is None:
        sys.exit(f"sign-in failed for {email}")
    print(session.access_token)


if __name__ == "__main__":
    main()
