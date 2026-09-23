"""Create the demo logins (docs/supabase.md §4) via the Supabase admin API.

Idempotent: a user that already exists gets its password and metadata reset, and its
`profiles` row is upserted, so running it twice gives the same result. Reads
SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from backend/.env.

    python backend/scripts/create_demo_users.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
PASSWORD = "demo1234"

# Operators: Ganesh (OP02, M05 on the demo night shift) is the demo persona
# (docs/demo_script.md, "Demo data"); Naveen prefers night shifts at S1 and speaks Tamil;
# Vijay is at the quarry (S2) and speaks Hindi, for the multilingual demo.
DEMO_USERS: list[dict[str, Any]] = [
    {
        "email": "ganesh@demo.site",
        "role": "operator",
        "full_name": "Ganesh Nair",
        "operator_id": "OP02",
        "site_id": "S1",
        "preferred_language": "en",
    },
    {
        "email": "naveen@demo.site",
        "role": "operator",
        "full_name": "Naveen Rao",
        "operator_id": "OP07",
        "site_id": "S1",
        "preferred_language": "ta",
    },
    {
        "email": "vijay@demo.site",
        "role": "operator",
        "full_name": "Vijay Singh",
        "operator_id": "OP11",
        "site_id": "S2",
        "preferred_language": "hi",
    },
    {
        "email": "priya@demo.site",
        "role": "manager",
        "full_name": "Priya Menon",
        "operator_id": None,
        "site_id": "S1",
        "preferred_language": "en",
    },
    {
        "email": "admin@demo.site",
        "role": "admin",
        "full_name": "Demo Admin",
        "operator_id": None,
        "site_id": None,
        "preferred_language": "en",
    },
]
PROFILE_FIELDS = ("role", "full_name", "operator_id", "site_id", "preferred_language")


def connect() -> Client:
    load_dotenv(ENV_PATH)
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit(f"SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in {ENV_PATH}")
    return create_client(url, key)


def check_operators(sb: Client) -> None:
    """Operator accounts reference operators rows; fail early if the data isn't loaded."""
    ids = [u["operator_id"] for u in DEMO_USERS if u["operator_id"]]
    rows = (
        sb.table("operators")
        .select("operator_id,full_name,site_id")
        .in_("operator_id", ids)
        .execute()
        .data
    )
    found = {r["operator_id"]: r for r in rows}
    for u in DEMO_USERS:
        op = u["operator_id"]
        if not op:
            continue
        if op not in found:
            sys.exit(f"{op} not in operators; load data first (data/generator/load_to_supabase.py)")
        if (found[op]["full_name"], found[op]["site_id"]) != (u["full_name"], u["site_id"]):
            sys.exit(
                f"{op} is {found[op]['full_name']} at {found[op]['site_id']} in the DB, "
                f"expected {u['full_name']} at {u['site_id']}"
            )


def existing_users(sb: Client) -> dict[str, str]:
    """email -> auth user id, across all pages."""
    out: dict[str, str] = {}
    page = 1
    while True:
        users = sb.auth.admin.list_users(page=page, per_page=200)
        for user in users:
            if user.email:
                out[user.email.lower()] = user.id
        if len(users) < 200:
            return out
        page += 1


def upsert_user(sb: Client, u: dict[str, Any], known: dict[str, str]) -> tuple[str, str]:
    # The handle_new_user trigger copies these into profiles on insert.
    metadata = {k: u[k] for k in ("role", "full_name", "operator_id", "site_id") if u[k]}
    uid = known.get(u["email"])
    if uid is None:
        res = sb.auth.admin.create_user(
            {
                "email": u["email"],
                "password": PASSWORD,
                "email_confirm": True,
                "user_metadata": metadata,
            }
        )
        uid, action = res.user.id, "created"
    else:
        sb.auth.admin.update_user_by_id(
            uid,
            {
                "password": PASSWORD,
                "email_confirm": True,
                "user_metadata": metadata,
            },
        )
        action = "updated"
    # The trigger only runs on insert and doesn't set preferred_language, so write the row
    # explicitly: this also repairs profiles of users that existed before the trigger.
    sb.table("profiles").upsert({"id": uid, **{k: u[k] for k in PROFILE_FIELDS}}).execute()
    return uid, action


def verify(sb: Client, ids: dict[str, str]) -> list[str]:
    rows = (
        sb.table("profiles")
        .select("id," + ",".join(PROFILE_FIELDS))
        .in_("id", list(ids.values()))
        .execute()
        .data
    )
    by_id = {r["id"]: r for r in rows}
    errors = []
    for u in DEMO_USERS:
        row = by_id.get(ids[u["email"]])
        if row is None:
            errors.append(f"{u['email']}: no profiles row")
            continue
        for k in PROFILE_FIELDS:
            if row[k] != u[k]:
                errors.append(f"{u['email']}: profiles.{k} = {row[k]!r}, expected {u[k]!r}")
    return errors


def main() -> None:
    sb = connect()
    check_operators(sb)
    known = existing_users(sb)
    ids: dict[str, str] = {}
    for u in DEMO_USERS:
        ids[u["email"]], action = upsert_user(sb, u, known)
        print(f"  {action:7} {u['email']}")

    errors = verify(sb, ids)
    if errors:
        sys.exit("Profile check failed:\n  " + "\n  ".join(errors))

    print(f"\nDemo logins (password for all: {PASSWORD})")
    print(f"{'email':20} {'role':9} {'name':12} {'operator':9} {'site':5} lang")
    for u in DEMO_USERS:
        print(
            f"{u['email']:20} {u['role']:9} {u['full_name']:12} "
            f"{u['operator_id'] or '-':9} {u['site_id'] or '-':5} {u['preferred_language']}"
        )
    print("\nprofiles rows verified: role, operator_id, site_id match.")


if __name__ == "__main__":
    main()
