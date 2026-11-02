#!/usr/bin/env python3
"""A stand-in for `adb` used by test_job.py. It simulates one rooted Samsung phone.

State lives in $FAKE_ADB_HOME:
  staging_src/   what the helper app would produce (copied to phone/staging on PREPARE)
  phone/         the simulated phone filesystem (staging copy + status.json)
Set FAKE_ADB_NO_ROOT=1 to simulate a phone without su.
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

HOME = Path(os.environ["FAKE_ADB_HOME"])
PHONE = HOME / "phone"
STAGING = "/data/local/tmp/vibertransfer_staging"
STATUS = "/sdcard/Android/data/com.vibertransfer.helper/files/status.json"
DUMPSYS = """Packages:
  Package [com.viber.voip] (8d1f2a3):
    versionCode=2410005 minSdk=26 targetSdk=34
    versionName=24.1.0.5
    pkgFlags=[ HAS_CODE ALLOW_CLEAR_USER_DATA ]
"""


def local(remote: str) -> Path:
    return PHONE / remote.lstrip("/")


def shell(cmd: str) -> None:
    if cmd.startswith("getprop"):
        print("samsung\nSM-S918B\n14\n34")
    elif cmd.startswith("pm path com.viber.voip"):
        print("package:/data/app/~~x==/com.viber.voip/base.apk")
    elif cmd.startswith("dumpsys package"):
        print(DUMPSYS)
    elif cmd.startswith("command -v su"):
        if not os.environ.get("FAKE_ADB_NO_ROOT"):
            print("/system/bin/su")
    elif cmd.startswith("am start") and ".PREPARE" in cmd:
        run_id = re.search(r"--es run_id (\S+)", cmd).group(1)
        # Pretend the user tapped Start copy and granted root, and the copy finished.
        shutil.copytree(HOME / "staging_src", local(STAGING))
        local(STATUS).parent.mkdir(parents=True, exist_ok=True)
        local(STATUS).write_text(json.dumps({"run_id": run_id, "state": "done", "files_done": 4, "files_total": 4}))
    elif cmd.startswith("cat " + STATUS):
        if local(STATUS).exists():
            print(local(STATUS).read_text())
    elif cmd.startswith(f"[ -e {STAGING} ]"):
        if local(STAGING).exists():
            print("yes")
    elif cmd.startswith(f"rm -rf {STAGING}"):
        shutil.rmtree(local(STAGING), ignore_errors=True)
    # am force-stop, logcat, other am start: no output


def main(argv):
    if argv[:1] == ["-s"]:
        argv = argv[2:]
    if argv[:2] == ["devices", "-l"]:
        print("List of devices attached\nR5CX1234567  device usb:1-1 product:dm3q model:SM_S918B device:dm3q\n")
    elif argv[0] == "shell":
        shell(argv[1])
    elif argv[0] == "install":
        print("Performing Streamed Install\nSuccess")
    elif argv[0] == "pull":
        src, dest = local(argv[1]), Path(argv[2])
        shutil.copytree(src, dest / src.name)
        print(f"{argv[1]}/: 4 files pulled.")
    elif argv[0] == "uninstall":
        print("Success")
    else:
        print(f"fake adb: unsupported {argv}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
