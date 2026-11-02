"""Engine for the native Mac app: JSON requests on stdin, JSON responses on stdout.

The SwiftUI app (mac-app/) starts `vibertransfer engine` as a child process and
talks to it one JSON object per line. No network socket is involved.

    request:   {"id": 7, "cmd": "status"}
    response:  {"id": 7, "ok": true, "result": {...}}   or   {"id": 7, "ok": false, "error": "..."}

Commands: status, state {from}, start, cancel, answer {value}, settings {...},
ios_apps, uninstall_helper {confirmed: true}.

Everything else the engine prints (the job log, warnings) goes to stderr, which
the app saves to ~/Library/Logs/ViberTransfer/engine.log. When stdin closes
(the app quit or crashed) a running transfer is cancelled and cleaned up first.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from . import __version__
from . import android as A
from .ios import IPhone, pymobiledevice3_available
from .job import TransferJob
from .tools import DEFAULT_APK, Runner, find_tool, load_settings, save_settings, tool_report

IPHONE_LABELS = {"connected": "Connected", "not_trusted": "Not trusted", "not_connected": "Not connected",
                 "tool_missing": "Tools missing"}


class Backend:
    def __init__(self):
        self.settings = load_settings()
        self.job: TransferJob | None = None
        self.quiet = Runner(log=lambda m: None)
        self.status_lock = threading.Lock()

    # ---------------------------------------------------------------- device panels

    def android_status(self) -> dict:
        adb = find_tool("adb", self.settings.get("adb_path", ""))
        if not adb:
            return {"state": "tool_missing", "label": "adb not installed",
                    "message": "Install with: brew install --cask android-platform-tools"}
        droid = A.Android(self.quiet, adb)
        devices = droid.devices()
        wanted = self.settings.get("android_serial")
        if wanted:
            devices = [d for d in devices if d["serial"] == wanted]
        if not devices:
            return {"state": "not_connected", "label": "Not connected",
                    "message": "Connect the phone by USB and turn on USB debugging."}
        d = devices[0]
        base = {"serial": d["serial"], "model": d["model"]}
        if d["state"] == "unauthorized":
            return {**base, "state": "unauthorized", "label": "Unauthorized",
                    "message": "Tap Allow on the phone's “Allow USB debugging?” prompt."}
        if d["state"] != "device":
            return {**base, "state": d["state"], "label": d["state"].capitalize(),
                    "message": "Unplug and reconnect the phone."}
        droid.serial = d["serial"]
        viber = droid.package_info(A.VIBER_PKG)
        root = droid.has_su()
        return {**base, "state": "connected" if root else "no_root",
                "label": "Connected" if root else "Connected – no root",
                "viber": viber["version_name"] if viber else None,
                "message": ("Viber is not installed." if not viber else
                            "Root available." if root else
                            "No root found: the app will try adb backup, which most Viber versions block.")}

    def status(self) -> dict:
        with self.status_lock:
            try:
                android = self.android_status()
            except Exception as e:  # noqa: BLE001
                android = {"state": "error", "label": "Error", "message": str(e)}
            try:
                iphone = IPhone(self.quiet).detect()
                iphone["label"] = IPHONE_LABELS[iphone["state"]]
            except Exception as e:  # noqa: BLE001
                iphone = {"state": "error", "label": "Error", "message": str(e)}
        apk = Path(self.settings.get("apk_path") or DEFAULT_APK)
        return {"android": android, "iphone": iphone, "tools": tool_report(self.settings),
                "apk": {"path": str(apk), "exists": apk.is_file()}, "settings": self.settings,
                "busy": bool(self.job and self.job.state["running"])}

    def ios_apps(self) -> dict:
        if not pymobiledevice3_available():
            return {"apps": [], "error": "pymobiledevice3 is not installed (./run.sh --setup)."}
        info = IPhone(self.quiet).detect()
        if info["state"] != "connected":
            return {"apps": [], "error": info.get("message", "iPhone not connected.")}
        return {"apps": IPhone(self.quiet).list_file_sharing_apps(info["udid"])}

    # ---------------------------------------------------------------- actions

    def start(self) -> dict:
        if self.job and self.job.state["running"]:
            raise RuntimeError("A transfer is already running.")
        self.job = TransferJob(dict(self.settings))
        self.job.start()
        return {}

    def state(self, log_from: int) -> dict:
        if not self.job:
            return {"running": False, "log": [], "log_next": 0}
        return self.job.snapshot(log_from)

    def update_settings(self, values: dict) -> dict:
        self.settings.update({k: v for k, v in values.items() if k in self.settings})
        save_settings(self.settings)
        return self.settings

    def uninstall_helper(self, confirmed: bool) -> dict:
        # The app shows a confirmation dialog first; the flag is required here as well.
        if confirmed is not True:
            raise RuntimeError("Not confirmed.")
        adb = find_tool("adb", self.settings.get("adb_path", ""))
        if not adb:
            raise RuntimeError("adb not found.")
        out = A.Android(self.quiet, adb, self.settings.get("android_serial", "")).uninstall_helper()
        if "Success" not in out:
            raise RuntimeError(out or "adb uninstall failed")
        return {"output": out}

    def stop(self) -> None:
        """Cancel a running transfer and wait for its cleanup (used when the app goes away)."""
        if self.job and self.job.state["running"]:
            self.job.cancel()
            self.job.thread.join(timeout=120)

    def handle(self, cmd: str, req: dict):
        if cmd == "status":
            return self.status()
        if cmd == "state":
            return self.state(int(req.get("from", 0)))
        if cmd == "start":
            return self.start()
        if cmd == "cancel":
            if self.job:
                self.job.cancel()
            return {}
        if cmd == "answer":
            if self.job:
                self.job.answer(str(req.get("value", "")))
            return {}
        if cmd == "settings":
            return self.update_settings(req.get("values", {}))
        if cmd == "ios_apps":
            return self.ios_apps()
        if cmd == "uninstall_helper":
            return self.uninstall_helper(req.get("confirmed"))
        raise ValueError(f"unknown command: {cmd}")


def run_engine(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    sys.stdout = sys.stderr  # from here on, print() can't corrupt the protocol stream
    write_lock = threading.Lock()

    def send(obj: dict) -> None:
        line = json.dumps(obj, default=str, ensure_ascii=False)
        with write_lock:
            out.write(line + "\n")
            out.flush()

    backend = Backend()

    def serve(req: dict) -> None:
        rid = req.get("id")
        try:
            send({"id": rid, "ok": True, "result": backend.handle(str(req.get("cmd")), req)})
        except Exception as e:  # noqa: BLE001 - every error goes back to the app
            send({"id": rid, "ok": False, "error": str(e)})

    send({"event": "ready", "version": __version__})
    workers: list[threading.Thread] = []
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            send({"id": None, "ok": False, "error": "bad json"})
            continue
        # Each request on its own thread: a slow `status` (adb + iPhone probing) must not hold up `state`.
        worker = threading.Thread(target=serve, args=(req,), daemon=True)
        worker.start()
        workers = [w for w in workers if w.is_alive()] + [worker]
    backend.stop()
    for worker in workers:  # answer what was already asked before exiting
        worker.join(timeout=30)
    return 0
