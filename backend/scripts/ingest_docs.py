"""Load the RAG knowledge base (backend/kb/*.md) into `documents` + `document_chunks`
(models.md §7, docs/supabase.md §8).

One document per file (title = its `# ` heading, source = `backend/kb/<file>`), one chunk per
`##` section with the heading kept in the chunk, embedded with BAAI/bge-small-en-v1.5
(normalised, 384-d). Idempotent: a file's document row is found by `source` and updated (its id
stays stable, so chat_messages.sources keep pointing at it), its chunks are replaced, and
documents of kb files that no longer exist are deleted. Reads backend/.env.

    python backend/scripts/ingest_docs.py            # ingest
    python backend/scripts/ingest_docs.py --dry-run  # print the chunks, touch nothing
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # Settings read .env from the working directory

from app.services.kb import (  # noqa: E402
    EMBED_DIM,
    MAX_CHARS,
    SOURCE_PREFIX,
    chunk_metadata,
    embed_passages,
    load_kb,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the chunks, write nothing")
    args = ap.parse_args()

    docs = load_kb()
    total = sum(len(d.chunks) for d in docs)
    longest = max(len(c.content) for d in docs for c in d.chunks)
    print(f"{len(docs)} documents, {total} chunks, longest chunk {longest} chars (max {MAX_CHARS})")
    if args.dry_run:
        for d in docs:
            print(f"\n{d.source}  '{d.title}'  doc_type={d.doc_type}")
            for c in d.chunks:
                print(f"  [{c.chunk_index:2}] {len(c.content):5} chars  {c.heading}")
        return

    from app.db import get_supabase

    sb = get_supabase()
    existing = {
        r["source"]: r["id"]
        for r in sb.table("documents")
        .select("id,source")
        .like("source", f"{SOURCE_PREFIX}%")
        .execute()
        .data
    }
    for d in docs:
        vectors = embed_passages([c.content for c in d.chunks])
        assert all(len(v) == EMBED_DIM for v in vectors)
        fields = {"title": d.title, "source": d.source, "doc_type": d.doc_type, "language": "en"}
        if d.source in existing:
            doc_id = existing[d.source]
            sb.table("documents").update(fields).eq("id", doc_id).execute()
            sb.table("document_chunks").delete().eq("document_id", doc_id).execute()
        else:
            doc_id = sb.table("documents").insert(fields).execute().data[0]["id"]
        rows = [
            {
                "document_id": doc_id,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "embedding": v,
                "metadata": chunk_metadata(d, c),
            }
            for c, v in zip(d.chunks, vectors, strict=True)
        ]
        sb.table("document_chunks").insert(rows).execute()
        print(f"  document {doc_id:3}  {len(rows):2} chunks  {d.source}")

    stale = [i for s, i in existing.items() if s not in {d.source for d in docs}]
    if stale:
        sb.table("documents").delete().in_("id", stale).execute()  # chunks cascade
        print(f"  deleted {len(stale)} document(s) whose kb file is gone")
    n = sb.table("document_chunks").select("id", count="exact").limit(0).execute().count
    print(f"done: document_chunks now holds {n} rows")


if __name__ == "__main__":
    main()
