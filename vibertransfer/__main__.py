"""Command line entry point.

    python3 -m vibertransfer                 start the app (opens the browser)
    python3 -m vibertransfer --port 9000 --no-browser
    python3 -m vibertransfer verify <backup folder>    re-check every file against manifest.json
    python3 -m vibertransfer convert <backup folder>   re-run chat conversion (e.g. after editing convert.py)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .backup import create_manifest, load_manifest, verify_manifest
from .convert import SchemaError, get_converter


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vibertransfer")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("verify").add_argument("folder", type=Path)
    sub.add_parser("convert").add_argument("folder", type=Path)
    args = p.parse_args(argv)

    if args.cmd == "verify":
        problems = verify_manifest(args.folder)
        for line in problems:
            print(line)
        print("OK: every file matches manifest.json" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    if args.cmd == "convert":
        old = load_manifest(args.folder)
        shutil.rmtree(args.folder / "chats", ignore_errors=True)
        try:
            conversion = get_converter().convert(args.folder)
        except SchemaError as e:
            print(f"Conversion failed: {e}", file=sys.stderr)
            conversion = {"error": str(e)}
        create_manifest(args.folder, method=old["method"], device=old["source_device"], viber=old["viber"],
                        conversion=conversion, transfer=old.get("transfer"))
        print("manifest.json updated")
        return 1 if conversion.get("error") else 0

    from .server import serve
    serve(args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
