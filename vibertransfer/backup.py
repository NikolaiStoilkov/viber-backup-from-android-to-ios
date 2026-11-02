"""Backup folder layout, SHA-256 checksums and manifest.json.

A finished backup folder looks like this:

    <timestamp>/
      manifest.json     device info, Viber version, counts, sha256 + size of every other file
      raw/              untouched copy of Viber's app data (databases/, shared_prefs/, files/)
      media/            attachments found in Viber's shared-storage folders
      chats/            per-conversation export produced by convert.py
      schema_report.txt what the database schema looked like (written by convert.py)

The manifest lists every file except itself, so `verify_manifest()` can check the
whole folder later (for example before the iPhone step).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path

from . import __version__

MANIFEST_NAME = "manifest.json"
FORMAT_NAME = "vibertransfer-backup"
FORMAT_VERSION = 1


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def iter_files(root: Path):
    """All regular files under root, as POSIX-style relative paths, sorted for stable output."""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_file() and not full.is_symlink():
                yield full.relative_to(root).as_posix()


def build_file_index(root: Path, exclude: tuple[str, ...] = (MANIFEST_NAME,)) -> dict[str, dict]:
    """{relative_path: {"sha256": ..., "size": ...}} for every file under root."""
    index = {}
    for rel in iter_files(root):
        if rel in exclude:
            continue
        full = Path(root) / rel
        index[rel] = {"sha256": sha256_file(full), "size": full.stat().st_size}
    return index


def summarize(index: dict[str, dict]) -> dict:
    """File counts and byte totals per top-level folder."""
    out = {"files": 0, "bytes": 0, "by_folder": {}}
    for rel, info in index.items():
        top = rel.split("/", 1)[0] if "/" in rel else "."
        folder = out["by_folder"].setdefault(top, {"files": 0, "bytes": 0})
        folder["files"] += 1
        folder["bytes"] += info["size"]
        out["files"] += 1
        out["bytes"] += info["size"]
    return out


def create_manifest(root: Path, *, method: str, device: dict, viber: dict, conversion: dict | None = None,
                    transfer: dict | None = None, now: _dt.datetime | None = None) -> dict:
    """Hash every file under root and write manifest.json. Returns the manifest dict."""
    root = Path(root)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    index = build_file_index(root)
    totals = summarize(index)
    counts = {"files": totals["files"], "bytes": totals["bytes"], "by_folder": totals["by_folder"]}
    if conversion:
        counts["conversations"] = conversion.get("conversations", 0)
        counts["messages"] = conversion.get("messages", 0)
    manifest = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "tool_version": __version__,
        "created_at": now.isoformat(timespec="seconds"),
        "method": method,  # "root" or "adb-backup"
        "source_device": device,
        "viber": viber,
        "counts": counts,
        "conversion": conversion or {},
        "transfer": transfer or {},  # how the copy was verified (device checksums, .ab hash, ...)
        "files": index,
    }
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def load_manifest(root: Path) -> dict:
    data = json.loads((Path(root) / MANIFEST_NAME).read_text())
    if data.get("format") != FORMAT_NAME:
        raise ValueError(f"{root} does not contain a Viber Transfer manifest")
    return data


def verify_manifest(root: Path) -> list[str]:
    """Re-hash the folder and compare with manifest.json. Returns a list of problems (empty = OK)."""
    root = Path(root)
    expected = load_manifest(root)["files"]
    problems = []
    for rel, info in expected.items():
        full = root / rel
        if not full.is_file():
            problems.append(f"missing: {rel}")
        elif full.stat().st_size != info["size"]:
            problems.append(f"size mismatch: {rel}")
        elif sha256_file(full) != info["sha256"]:
            problems.append(f"checksum mismatch: {rel}")
    for rel in iter_files(root):
        if rel != MANIFEST_NAME and rel not in expected:
            problems.append(f"not in manifest: {rel}")
    return problems


def parse_sha256sum(text: str) -> dict[str, str]:
    """Parse `sha256sum` output ("<hash>  ./path" or "<hash> *path") into {relative_path: hash}."""
    result = {}
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"Unexpected line in checksum file: {line!r}")
        digest, path = parts[0].lower(), parts[1]
        if path.startswith(" ") or path.startswith("*"):
            path = path[1:]
        if path.startswith("./"):
            path = path[2:]
        result[path] = digest
    return result


def verify_against_checksums(root: Path, expected: dict[str, str]) -> list[str]:
    """Compare files under root with checksums computed on the phone. Returns problems."""
    problems = []
    for rel, digest in sorted(expected.items()):
        full = Path(root) / rel
        if not full.is_file():
            problems.append(f"missing after pull: {rel}")
        elif sha256_file(full) != digest:
            problems.append(f"checksum mismatch after pull: {rel}")
    return problems


def new_backup_dir(base: Path, now: _dt.datetime | None = None) -> Path:
    """Create a fresh timestamped folder like 2026-11-02_09-30-00 (with a suffix if it exists)."""
    now = now or _dt.datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    path = base / stamp
    n = 1
    while path.exists():
        n += 1
        path = base / f"{stamp}_{n}"
    path.mkdir()
    return path
