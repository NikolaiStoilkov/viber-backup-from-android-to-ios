"""Turn the raw Viber databases into a portable per-conversation export (chats/).

Interface: a converter has a `name` and `convert(backup_dir, log) -> dict`. It
reads raw/ (and may look at media/) and writes chats/. The rest of the app only
uses that interface, so another output format can be added without touching the
transfer job; see `CONVERTERS` at the bottom.

We do NOT hard-code Viber's schema. Viber changes its database between
versions and doesn't document it, so the converter:
  1. finds every SQLite file under raw/ and records its tables and columns in
     schema_report.txt (and the log),
  2. looks for a messages table using the candidate names below,
  3. stops with a SchemaError that lists what it actually found if the required
     columns aren't there. Edit the candidate lists below to match your data.

Output (chats/):
  index.json             one entry per conversation: id, name, message count, files
  <id>_<name>.json       messages of one conversation
  <id>_<name>.txt        the same, as a readable transcript
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

# Candidate table/column names, checked in order. These reflect what the Viber
# Android database has been observed to use; they are verified at runtime and
# never assumed. Required keys must be found, optional ones may be missing.
MESSAGE_TABLES = ["messages"]
MESSAGE_COLUMNS = {
    "id": (["_id", "id", "message_id"], True),
    "conversation_id": (["conversation_id", "thread_id", "conversation"], True),
    "timestamp": (["date", "timestamp", "msg_date", "time"], True),
    "text": (["body", "text", "message"], False),
    "sender": (["address", "member_id", "participant_id", "sender"], False),
    "direction": (["send_type", "outgoing", "direction", "type"], False),
    "media_uri": (["extra_uri", "media_uri", "uri"], False),
}
CONVERSATION_TABLES = ["conversations"]
CONVERSATION_COLUMNS = {
    "id": (["_id", "id"], True),
    "name": (["name", "group_name", "title"], False),
}
# How to label values of the direction column. The raw value is always kept as
# well, and the log prints how often each value occurs so this can be checked.
DIRECTION_LABELS = {"send_type": {0: "received", 1: "sent"}, "outgoing": {0: "received", 1: "sent"}}

SQLITE_MAGIC = b"SQLite format 3\x00"


class SchemaError(Exception):
    """The database doesn't look like we expect. The message explains what was found."""


# ---------------------------------------------------------------- schema inspection

def is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def find_databases(raw_dir: Path) -> list[Path]:
    return sorted(p for p in Path(raw_dir).rglob("*") if p.is_file() and is_sqlite(p))


def open_copy(db: Path, tmp_dir: Path) -> sqlite3.Connection:
    """Open a temporary copy of db (plus its -wal/-shm/-journal) so raw/ stays byte-for-byte untouched.

    The -wal file matters: recent messages may only be in it until SQLite checkpoints.
    """
    for suffix in ["", "-wal", "-shm", "-journal"]:
        src = Path(str(db) + suffix)
        if src.exists():
            shutil.copy2(src, tmp_dir / (db.name + suffix))
    return sqlite3.connect(str(tmp_dir / db.name))


def read_schema(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """{table: [columns]} for every table in the database."""
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    except sqlite3.DatabaseError as e:
        raise SchemaError(f"The file could not be read as SQLite ({e}). It may be encrypted or damaged; "
                          "this tool does not try to bypass Viber's encryption.") from e
    return {t: [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')] for t in tables}


def resolve_columns(available: list[str], wanted: dict) -> tuple[dict, list[str]]:
    """Map our names to actual column names. Returns (mapping, missing_required)."""
    lower = {c.lower(): c for c in available}
    mapping, missing = {}, []
    for key, (candidates, required) in wanted.items():
        found = next((lower[c.lower()] for c in candidates if c.lower() in lower), None)
        if found:
            mapping[key] = found
        elif required:
            missing.append(f"{key} (tried: {', '.join(candidates)})")
    return mapping, missing


def schema_report(schemas: dict[str, dict[str, list[str]]]) -> str:
    lines = []
    for db, tables in schemas.items():
        lines.append(f"== {db}  ({len(tables)} tables)")
        for table, cols in tables.items():
            lines.append(f"  {table}: {', '.join(cols)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- helpers

def to_iso(value) -> str | None:
    """Epoch seconds/milliseconds/microseconds -> ISO 8601 UTC. Unit is guessed from magnitude."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v > 1e14:
        v /= 1e6
    elif v > 1e11:
        v /= 1e3
    try:
        return _dt.datetime.fromtimestamp(v, _dt.timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def safe_name(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")
    return slug[:limit] or "chat"


def media_index(media_dir: Path) -> dict[str, str]:
    """basename -> path relative to the backup folder, for linking messages to attachments by file name."""
    index = {}
    if media_dir.is_dir():
        for p in sorted(media_dir.rglob("*")):
            if p.is_file():
                index.setdefault(p.name, p.relative_to(media_dir.parent).as_posix())
    return index


# ---------------------------------------------------------------- converters

class Converter:
    name = "base"

    def convert(self, backup_dir: Path, log=print) -> dict:
        raise NotImplementedError


class ViberJsonConverter(Converter):
    """Viber Android SQLite -> chats/*.json + chats/*.txt."""

    name = "viber-json-v1"

    def convert(self, backup_dir: Path, log=print) -> dict:
        backup_dir = Path(backup_dir)
        raw_dir = backup_dir / "raw"
        dbs = find_databases(raw_dir)
        if not dbs:
            raise SchemaError(f"No SQLite databases were found under {raw_dir}. The copy from the phone may be "
                              "incomplete, or Viber stores its data somewhere this version doesn't know about.")

        with tempfile.TemporaryDirectory(prefix="vibertransfer_") as tmp:
            tmp_dir = Path(tmp)
            schemas, chosen = {}, None
            for db in dbs:
                rel = db.relative_to(backup_dir).as_posix()
                sub = tmp_dir / str(len(schemas))
                sub.mkdir()
                conn = open_copy(db, sub)
                try:
                    schemas[rel] = read_schema(conn)
                except SchemaError as e:
                    log(f"  {rel}: {e}")
                    conn.close()
                    continue
                table = next((t for t in MESSAGE_TABLES if t in schemas[rel]), None)
                if chosen is None and table:
                    cols, missing = resolve_columns(schemas[rel][table], MESSAGE_COLUMNS)
                    if not missing:
                        chosen = (rel, conn, table, cols)
                        continue
                    log(f"  {rel} has a '{table}' table but is missing: {'; '.join(missing)}")
                conn.close()

            report = schema_report(schemas)
            (backup_dir / "schema_report.txt").write_text(report)
            log("Database schema found on the phone:")
            for line in report.splitlines():
                log("  " + line)

            if chosen is None:
                raise SchemaError(
                    "None of the databases has a messages table with the columns this converter needs "
                    f"(tables tried: {', '.join(MESSAGE_TABLES)}). The full schema was saved to "
                    f"{backup_dir / 'schema_report.txt'}. Your raw backup is complete and safe in raw/; update the "
                    "candidate names at the top of vibertransfer/convert.py and run the conversion again "
                    "(python3 -m vibertransfer convert <backup folder>).")
            rel, conn, table, cols = chosen
            try:
                return self._export(backup_dir, rel, conn, table, cols, schemas[rel], log)
            finally:
                conn.close()

    def _export(self, backup_dir, db_rel, conn, table, cols, schema, log) -> dict:
        log(f"Using {db_rel}, table '{table}', columns: " + ", ".join(f"{k}={v}" for k, v in cols.items()))
        names = self._conversation_names(conn, schema, log)
        media = media_index(backup_dir / "media")

        keys = list(cols)
        select = ", ".join(f'"{cols[k]}"' for k in keys)
        rows = conn.execute(f'SELECT {select} FROM "{table}" ORDER BY "{cols["conversation_id"]}", '
                            f'"{cols["timestamp"]}", "{cols["id"]}"')
        by_conv = defaultdict(list)
        direction_values = Counter()
        labels = DIRECTION_LABELS.get(cols.get("direction", ""), {})
        for row in rows:
            r = dict(zip(keys, row))
            msg = {
                "id": r["id"],
                "time": to_iso(r["timestamp"]),
                "timestamp_raw": r["timestamp"],
                "sender": r.get("sender"),
                "direction": labels.get(r.get("direction")),
                "direction_raw": r.get("direction"),
                "text": r.get("text"),
            }
            if "direction" in r:
                direction_values[r["direction"]] += 1
            uri = r.get("media_uri")
            if uri:
                msg["media_uri"] = uri
                base = str(uri).rstrip("/").rsplit("/", 1)[-1]
                if base in media:
                    msg["media_file"] = media[base]
            by_conv[r["conversation_id"]].append(msg)

        if direction_values:
            log(f"Values of direction column '{cols['direction']}': " +
                ", ".join(f"{v!r}×{n}" for v, n in direction_values.most_common(10)) +
                f"  (labelled as {labels or 'unlabelled'})")

        out_dir = backup_dir / "chats"
        out_dir.mkdir(exist_ok=True)
        index, total = [], 0
        for conv_id, messages in by_conv.items():
            name = names.get(conv_id) or f"conversation {conv_id}"
            stem = f"{conv_id}_{safe_name(name)}"
            (out_dir / f"{stem}.json").write_text(json.dumps(
                {"conversation_id": conv_id, "name": name, "messages": messages}, indent=1, ensure_ascii=False))
            (out_dir / f"{stem}.txt").write_text(self._transcript(name, messages))
            index.append({"conversation_id": conv_id, "name": name, "messages": len(messages),
                          "json": f"chats/{stem}.json", "txt": f"chats/{stem}.txt",
                          "first": messages[0]["time"], "last": messages[-1]["time"]})
            total += len(messages)
        (out_dir / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))
        log(f"Exported {total} messages in {len(index)} conversations to chats/")
        return {"converter": self.name, "database": db_rel, "message_table": table, "columns": cols,
                "conversations": len(index), "messages": total}

    @staticmethod
    def _conversation_names(conn, schema, log) -> dict:
        table = next((t for t in CONVERSATION_TABLES if t in schema), None)
        if not table:
            log("No conversations table found; chats will be named by id.")
            return {}
        cols, missing = resolve_columns(schema[table], CONVERSATION_COLUMNS)
        if missing or "name" not in cols:
            log(f"Table '{table}' has no usable name column; chats will be named by id.")
            return {}
        return {cid: name for cid, name in conn.execute(f'SELECT "{cols["id"]}", "{cols["name"]}" FROM "{table}"')
                if name}

    @staticmethod
    def _transcript(name: str, messages: list[dict]) -> str:
        lines = [f"Viber conversation: {name}", f"Messages: {len(messages)}", ""]
        for m in messages:
            who = m["direction"] or m["sender"] or "?"
            body = m["text"] or ""
            if m.get("media_file") or m.get("media_uri"):
                body = (body + " " if body else "") + f"[attachment: {m.get('media_file') or m['media_uri']}]"
            lines.append(f"[{m['time'] or m['timestamp_raw']}] {who}: {body}")
        return "\n".join(lines) + "\n"


CONVERTERS = {ViberJsonConverter.name: ViberJsonConverter}
DEFAULT_CONVERTER = ViberJsonConverter.name


def get_converter(name: str = DEFAULT_CONVERTER) -> Converter:
    return CONVERTERS[name]()
