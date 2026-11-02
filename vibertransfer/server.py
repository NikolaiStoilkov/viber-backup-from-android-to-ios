"""Local web UI: a tiny HTTP server on 127.0.0.1 that serves ui.html and a JSON API.

Only reachable from this Mac. Every POST must carry a per-launch random token
(embedded in the page) and the Host header must be localhost, so other web
pages open in the browser cannot drive the app.
"""
from __future__ import annotations

import json
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import android as A
from .ios import IPhone, pymobiledevice3_available
from .job import TransferJob
from .tools import DEFAULT_APK, Runner, find_tool, load_settings, save_settings, tool_report

UI_FILE = Path(__file__).with_name("ui.html")


class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
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
        base = {"serial": d["serial"], "model": d["model"], "count": len(devices)}
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
                iphone["label"] = {"connected": "Connected", "not_trusted": "Not trusted",
                                   "not_connected": "Not connected", "tool_missing": "Tools missing"}[iphone["state"]]
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
            return {"ok": False, "error": "A transfer is already running."}
        self.job = TransferJob(dict(self.settings))
        self.job.start()
        return {"ok": True}

    def state(self, log_from: int) -> dict:
        if not self.job:
            return {"running": False, "result": None, "log": [], "log_next": 0}
        return self.job.snapshot(log_from)

    def uninstall_helper(self, body: dict) -> dict:
        # The UI shows a confirmation dialog first; we also require the flag server-side.
        if body.get("confirmed") is not True:
            return {"ok": False, "error": "Not confirmed."}
        adb = find_tool("adb", self.settings.get("adb_path", ""))
        if not adb:
            return {"ok": False, "error": "adb not found."}
        out = A.Android(self.quiet, adb, self.settings.get("android_serial", "")).uninstall_helper()
        return {"ok": "Success" in out, "output": out}

    def reveal(self) -> dict:
        summary = self.job.state.get("summary") if self.job else None
        if summary:
            subprocess.run(["open", summary["backup_dir"]])
        return {"ok": bool(summary)}


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal for the job log
            pass

        def _send(self, code: int, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            return self.headers.get("Host") in allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden"})
            url = urlparse(self.path)
            if url.path == "/":
                html = UI_FILE.read_text().replace("__TOKEN__", app.token)
                return self._send(200, html, "text/html")
            if url.path == "/api/status":
                return self._send(200, app.status())
            if url.path == "/api/state":
                start = int(parse_qs(url.query).get("from", ["0"])[0])
                return self._send(200, app.state(start))
            if url.path == "/api/ios_apps":
                try:
                    return self._send(200, app.ios_apps())
                except Exception as e:  # noqa: BLE001
                    return self._send(200, {"apps": [], "error": str(e)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Token") != app.token:
                return self._send(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            path = urlparse(self.path).path
            if path == "/api/start":
                return self._send(200, app.start())
            if path == "/api/cancel":
                if app.job:
                    app.job.cancel()
                return self._send(200, {"ok": True})
            if path == "/api/answer":
                if app.job:
                    app.job.answer(str(body.get("value", "")))
                return self._send(200, {"ok": True})
            if path == "/api/settings":
                app.settings.update({k: v for k, v in body.items() if k in app.settings})
                save_settings(app.settings)
                return self._send(200, {"ok": True, "settings": app.settings})
            if path == "/api/uninstall_helper":
                return self._send(200, app.uninstall_helper(body))
            if path == "/api/reveal":
                return self._send(200, app.reveal())
            self._send(404, {"error": "not found"})

    return Handler


def serve(port: int = 8765, open_browser: bool = True) -> None:
    app = App()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"Viber Transfer is running at {url}  (Ctrl+C to quit)")
    for t in tool_report(app.settings):
        print(f"  {t['name']:16} {t['path'] or 'MISSING  -> ' + (t['install'] or '')}")
    if open_browser:
        subprocess.Popen(["open", url])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if app.job and app.job.state["running"]:
            app.job.cancel()
            app.job.thread.join(timeout=60)
        print("Bye.")
