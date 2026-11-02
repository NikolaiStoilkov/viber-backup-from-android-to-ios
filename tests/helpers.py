"""Shared fixtures: a small fake Viber database and a fake staging folder.

The schema here is mock data shaped like the candidate names in convert.py;
it is not a copy of Viber's real schema.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

MESSAGES = [
    # _id, conversation_id, address, date (ms), body, send_type, extra_uri
    (1, 10, "+359888000001", 1700000000000, "Hi!", 0, None),
    (2, 10, "+359888000001", 1700000060000, "Hello back", 1, None),
    (3, 10, "+359888000001", 1700000120000, None, 0, "file:///storage/emulated/0/Viber/media/IMG-1.jpg"),
    (4, 20, "+359888000002", 1700000500000, "Group hello", 0, None),
]
CONVERSATIONS = [(10, "Maria"), (20, "Family group")]


def make_viber_db(path: Path, wal: bool = False, keep_open: bool = False):
    """Create the mock DB. With wal=True the rows stay in the -wal file (no checkpoint).

    Returns the open connection when keep_open is set (closing it would checkpoint the WAL).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE conversations (_id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE messages (_id INTEGER PRIMARY KEY, conversation_id INTEGER, address TEXT, "
                 "date INTEGER, body TEXT, send_type INTEGER, extra_uri TEXT)")
    conn.executemany("INSERT INTO conversations VALUES (?, ?)", CONVERSATIONS)
    conn.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)", MESSAGES)
    conn.commit()
    if keep_open:
        return conn
    conn.close()
    return None


def make_staging(root: Path) -> Path:
    """A folder shaped like what copy.sh leaves in /data/local/tmp/vibertransfer_staging."""
    make_viber_db(root / "internal" / "databases" / "viber_messages")
    (root / "internal" / "shared_prefs").mkdir(parents=True)
    (root / "internal" / "shared_prefs" / "prefs.xml").write_text("<map/>")
    (root / "external" / "Viber" / "media").mkdir(parents=True)
    (root / "external" / "Viber" / "media" / "IMG-1.jpg").write_bytes(b"\xff\xd8fakejpeg")
    (root / "inventory.json").write_text(json.dumps({"package": "com.viber.voip", "sources": []}))
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "checksums.sha256":
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(root).as_posix()}")
    (root / "checksums.sha256").write_text("\n".join(lines) + "\n")
    return root
