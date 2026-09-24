"""RAG knowledge base (models.md §7, supabase.md §8): markdown -> chunks -> embeddings.

Each file in backend/kb/ is one `documents` row (title = the `# ` heading). Every `##` section
answers one question and fits one chunk, so a chunk = one section with the heading kept in it
(the text before the first `##` is chunk 0). A section longer than ~600 tokens is split on
paragraphs with ~100 tokens of overlap (none today).

Embeddings: BAAI/bge-small-en-v1.5, normalised, 384-d. Queries get bge's retrieval instruction.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

KB_DIR = Path(__file__).resolve().parents[2] / "kb"
SOURCE_PREFIX = "backend/kb/"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
MAX_CHARS = 2400  # ~600 tokens
OVERLAP_CHARS = 400  # ~100 tokens

DOC_TYPES = {
    "fault_codes.md": "fault_codes",
    "troubleshooting_faq.md": "faq",
    "safety_rules.md": "safety",
    "operating_tips.md": "manual",
    "training_modules.md": "training",
}


@dataclass
class Chunk:
    chunk_index: int
    heading: str
    content: str


@dataclass
class KbDocument:
    file: str
    title: str
    doc_type: str
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def source(self) -> str:
        return SOURCE_PREFIX + self.file


def _split_long(text: str) -> list[str]:
    """Paragraph-packed pieces of at most MAX_CHARS with OVERLAP_CHARS carried over."""
    if len(text) <= MAX_CHARS:
        return [text]
    pieces: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) + 2 > MAX_CHARS:
            pieces.append(cur)
            cur = cur[-OVERLAP_CHARS:] + "\n\n" + para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        pieces.append(cur)
    return pieces


def parse_markdown(file: str, text: str) -> KbDocument:
    lines = text.splitlines()
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), Path(file).stem)
    doc = KbDocument(file=file, title=title, doc_type=DOC_TYPES.get(file, "manual"))
    sections: list[tuple[str, list[str]]] = [(title, [])]
    for ln in lines:
        if ln.startswith("## "):
            sections.append((ln[3:].strip(), []))
        elif not ln.startswith("# "):
            sections[-1][1].append(ln)
    for heading, body in sections:
        body_text = "\n".join(body).strip()
        if not body_text:
            continue
        for piece in _split_long(body_text):
            header = f"# {title}" if heading == title else f"# {title}\n## {heading}"
            doc.chunks.append(Chunk(len(doc.chunks), heading, f"{header}\n\n{piece}"))
    return doc


def load_kb(kb_dir: Path = KB_DIR) -> list[KbDocument]:
    return [
        parse_markdown(f, (kb_dir / f).read_text(encoding="utf-8"))
        for f in DOC_TYPES
        if (kb_dir / f).exists()
    ]


def chunk_metadata(doc: KbDocument, chunk: Chunk) -> dict[str, Any]:
    """Stored in document_chunks.metadata; match_document_chunks returns it, so /chat can cite
    title and chunk_index without another query."""
    return {
        "title": doc.title,
        "heading": chunk.heading,
        "chunk_index": chunk.chunk_index,
        "source": doc.source,
        "doc_type": doc.doc_type,
    }


@lru_cache
def _model() -> Any:
    # torch only: transformers otherwise imports TensorFlow when it is installed (Keras 3 breaks it)
    os.environ.setdefault("USE_TF", "0")
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL, device="cpu")


def embed_passages(texts: list[str]) -> list[list[float]]:
    vecs = _model().encode(texts, normalize_embeddings=True, batch_size=16)
    return [[float(x) for x in v] for v in vecs]


def embed_query(text: str) -> list[float]:
    vec = _model().encode(QUERY_INSTRUCTION + text, normalize_embeddings=True)
    return [float(x) for x in vec]
