"""Pre-generate the demo's LLM output into backend/cache/demo.json (committed), so the demo never
needs a live LLM call on the free tier (docs/demo_script.md, "If something breaks").

* /chat: the web app's three suggestion chips (web/src/data/hooks.ts `chatSuggestions`), the first
  being the demo question "What does E-365 mean?" (demo_script.md 5:00).
* Handover summaries: SH-2026-08-19-M05-N (Ganesh's demo night shift) and SH-2026-08-19-M05-D
  (the day shift whose handover Ganesh reads at login, demo_script.md 0:30). They are also written
  to shifts.handover_summary; the backend puts them back after reset_demo_state.py clears them.

Only missing entries, or chat answers from an older knowledge base, are generated; `--force`
regenerates everything. Re-run after editing backend/kb/ (the chat answers are tied to its
version) and commit backend/cache/demo.json. Reads backend/.env.

    python backend/scripts/prewarm_demo.py
    python backend/scripts/prewarm_demo.py --force
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # Settings read .env from the working directory

from app.ai_repo import get_ai_repo  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.llm import get_llm  # noqa: E402
from app.services import handover  # noqa: E402
from app.services.chat import answer, detect_language  # noqa: E402
from app.services.chat_cache import kb_version, key, read_demo, write_demo  # noqa: E402

DEMO_QUESTIONS = [
    "What does E-365 mean?",
    "Someone walked behind my machine. What do I do?",
    "I feel very sleepy on night shift.",
]
DEMO_HANDOVERS = ["SH-2026-08-19-M05-N", "SH-2026-08-19-M05-D"]
PAUSE_S = 5.0  # between LLM calls (free-tier requests per minute)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--force", action="store_true", help="regenerate existing entries")
    args = ap.parse_args()

    repo, llm = get_ai_repo(), get_llm()
    model = get_settings().llm_model
    version = kb_version()
    demo = read_demo()
    chat, handovers = demo.setdefault("chat", {}), demo.setdefault("handovers", {})
    first = True

    def pause() -> None:
        nonlocal first
        if not first:
            time.sleep(PAUSE_S)
        first = False

    for q in DEMO_QUESTIONS:
        lang = detect_language(q, "en")
        k = key(q, lang)
        if not args.force and chat.get(k, {}).get("kb_version") == version:
            print(f"chat      cached   {q}")
            continue
        pause()
        r = answer(repo, llm, q, lang)
        chat[k] = {
            "question": q,
            "language": lang,
            "answer": r.answer,
            "sources": r.sources,
            "kb_version": version,
            "model": model,
            "created_at": datetime.now(UTC).isoformat(),
        }
        write_demo(demo)  # keep what we have if a later call fails
        print(f"chat      new      {q}\n{r.answer}\n")

    for sid in DEMO_HANDOVERS:
        if not args.force and sid in handovers:
            print(f"handover  cached   {sid}")
        else:
            pause()
            summary = handover.generate(repo, llm, sid)
            handovers[sid] = {
                "summary": summary,
                "model": model,
                "created_at": datetime.now(UTC).isoformat(),
            }
            write_demo(demo)
            print(f"handover  new      {sid}\n{summary}\n")
    restored = handover.restore_pregenerated(repo)
    print(
        f"done: {len(chat)} chat answers (kb {version}), {len(handovers)} handovers; "
        f"written to shifts: {', '.join(restored) or 'already there'}"
    )


if __name__ == "__main__":
    main()
