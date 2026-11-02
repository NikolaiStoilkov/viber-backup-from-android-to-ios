"""Command line entry point.

The user interface is the native Mac app in mac-app/ (./run.sh builds and opens it).

    python3 -m vibertransfer engine                    the backend the Mac app talks to (JSON over stdin/stdout)
    python3 -m vibertransfer verify <backup folder>    re-check every file against manifest.json
    python3 -m vibertransfer convert <backup folder>   re-run chat conversion (e.g. after editing convert.py)
    python3 -m vibertransfer selftest                  check that a packaged app has everything it needs
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .backup import create_manifest, load_manifest, verify_manifest
from .convert import SchemaError, get_converter
from .tools import DEFAULT_APK


def selftest() -> int:
    """Used by build-dmg.sh on the built app: imports everything and checks bundled files."""
    import asyncio
    import sqlite3

    from pymobiledevice3.lockdown import create_using_usbmux  # noqa: F401
    from pymobiledevice3.services.house_arrest import HouseArrestService  # noqa: F401
    from pymobiledevice3.services.installation_proxy import InstallationProxyService  # noqa: F401

    from .engine import Backend  # noqa: F401

    # Go as far as talking to usbmuxd, so modules pymobiledevice3 imports lazily are exercised too.
    # Without an iPhone attached this ends in a "no device" error, which is fine; an ImportError is not.
    try:
        asyncio.run(asyncio.wait_for(create_using_usbmux(autopair=False), 10))
        usbmux = True
    except ImportError as e:
        print(f"MISSING  module needed for the iPhone step: {e}")
        usbmux = False
    except Exception:  # noqa: BLE001 - no device / usbmuxd unavailable
        usbmux = True

    ok = True
    for label, good in [("sqlite3 works", sqlite3.connect(":memory:").execute("select 1").fetchone() == (1,)),
                        ("pymobiledevice3 imports", usbmux),
                        ("helper APK bundled", DEFAULT_APK.is_file())]:
        print(f"{'ok  ' if good else 'MISSING'}  {label}")
        ok = ok and (good or label == "helper APK bundled")  # the APK is optional (no-root fallback)
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = [a for a in (sys.argv[1:] if argv is None else argv) if not a.startswith("-psn_")]  # Finder adds -psn_*
    p = argparse.ArgumentParser(prog="vibertransfer")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("engine")
    sub.add_parser("verify").add_argument("folder", type=Path)
    sub.add_parser("convert").add_argument("folder", type=Path)
    sub.add_parser("selftest")
    args = p.parse_args(argv)

    if args.cmd == "engine":
        from .engine import run_engine
        return run_engine()

    if args.cmd == "selftest":
        return selftest()

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

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
