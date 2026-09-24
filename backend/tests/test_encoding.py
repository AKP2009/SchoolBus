"""UTF-8 end to end: alert titles carry "—" and "°C", which Windows turns into "â€”" / "Â°C"
when UTF-8 bytes are decoded as cp1252 / ISO-8859-1 (a file read without an encoding, or a JSON
response without a charset read by PowerShell's Invoke-RestMethod)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ["backend/app", "backend/scripts", "backend/tests", "ml/inference", "vision"]
TEXT_IO = {"open", "read_text", "write_text"}
MOJIBAKE = ("â€", "Â°")


def _unencoded_text_io(path: Path) -> list[str]:
    """`open(...)`, `.read_text()`, `.write_text()` calls without encoding= (binary opens are
    fine). `.open` methods of other objects (e.g. cv2) are ignored: only bare open() counts."""
    bad = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        if name not in TEXT_IO or (name == "open" and not isinstance(f, ast.Name)):
            continue
        if any(k.arg == "encoding" for k in node.keywords):
            continue
        mode = node.args[1] if len(node.args) > 1 else None
        mode = mode or next((k.value for k in node.keywords if k.arg == "mode"), None)
        if isinstance(mode, ast.Constant) and "b" in str(mode.value):
            continue
        bad.append(f"{path.relative_to(REPO)}:{node.lineno} {name}()")
    return bad


def test_every_text_file_read_and_write_names_utf8():
    files = [
        p
        for d in SOURCE_DIRS
        for p in (REPO / d).rglob("*.py")
        if ".venv" not in p.parts and "__pycache__" not in p.parts
    ]
    assert files
    bad = [b for p in files for b in _unencoded_text_io(p)]
    assert not bad, "add encoding='utf-8':\n" + "\n".join(bad)


def test_thresholds_titles_keep_em_dash():
    from app.alerts.rules import load_thresholds

    text = repr(load_thresholds())
    assert "—" in text
    assert not any(m in text for m in MOJIBAKE)


def test_blindspot_alert_title_is_stored_with_em_dash(client, repo):
    from conftest import VISION_TOKEN, bearer

    body = {
        "type": "blindspot_intrusion",
        "machine_id": "M06",
        "operator_id": "OP05",
        "ts": "2026-06-01T02:00:00Z",
        "severity": "warning",
        "distance_m": 5.2,
        "sector": "left",
        "approaching": False,
    }
    r = client.post("/events", json=body, headers=bearer(VISION_TOKEN))
    assert r.status_code == 200, r.text
    title = repo.alerts[r.json()["alert_id"]]["title"]
    assert title == "Person 5.2 m on the left — blind spot"
    assert "—" in title and not any(m in title for m in MOJIBAKE)


@pytest.mark.parametrize("path", ["/health", "/no-such-endpoint"])
def test_json_responses_declare_utf8(client, path):
    """Invoke-RestMethod (Windows PowerShell 5.1) decodes charset-less JSON as ISO-8859-1."""
    r = client.get(path)
    assert r.headers["content-type"] == "application/json; charset=utf-8"
