"""Finding external tools, running them, and app settings.

Everything that shells out goes through `run()` so the job can be cancelled and
so commands are logged in one place.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "ViberTransfer"
SETTINGS_FILE = APP_SUPPORT / "settings.json"
BACKUPS_ROOT = Path.home() / "Documents" / "ViberTransfer" / "backups"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APK = PROJECT_ROOT / "android-helper" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"

# Where to look besides $PATH. The Mac app is often started from Finder/launchd
# with a minimal PATH, so Homebrew and the Android SDK are listed explicitly.
EXTRA_DIRS = [
    "/opt/homebrew/bin",  # Homebrew on Apple Silicon
    "/usr/local/bin",  # Homebrew on Intel
    str(Path.home() / "Library" / "Android" / "sdk" / "platform-tools"),
    str(PROJECT_ROOT / ".venv" / "bin"),
]

INSTALL_HINTS = {
    "adb": "brew install --cask android-platform-tools",
    "idevice_id": "brew install libimobiledevice",
    "ideviceinfo": "brew install libimobiledevice",
    "idevicepair": "brew install libimobiledevice",
}

DEFAULT_SETTINGS = {
    "adb_path": "",  # empty = auto-detect
    "apk_path": "",  # empty = DEFAULT_APK
    "android_serial": "",  # empty = first authorized device
    "ios_target_app": "",  # bundle id of an iPhone app with File Sharing enabled
    "skip_ios": False,
}


class Cancelled(Exception):
    """Raised when the user pressed Cancel."""


class ToolMissing(Exception):
    def __init__(self, name: str):
        hint = INSTALL_HINTS.get(name, "")
        super().__init__(f"'{name}' was not found." + (f" Install it with:  {hint}" if hint else ""))
        self.name = name
        self.hint = hint


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        settings.update(json.loads(SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return settings


def save_settings(settings: dict) -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    clean = {k: settings.get(k, v) for k, v in DEFAULT_SETTINGS.items()}
    SETTINGS_FILE.write_text(json.dumps(clean, indent=2))


def find_tool(name: str, override: str = "") -> str | None:
    """Return an absolute path to `name`, or None. `override` (from Settings) wins."""
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    for d in EXTRA_DIRS:
        candidate = os.path.join(d, name)
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def require_tool(name: str, override: str = "") -> str:
    path = find_tool(name, override)
    if not path:
        raise ToolMissing(name)
    return path


def tool_report(settings: dict) -> list[dict]:
    """Status of every external tool, for the UI and the startup log."""
    report = []
    for name in ["adb", "idevice_id", "ideviceinfo", "idevicepair"]:
        override = settings.get("adb_path", "") if name == "adb" else ""
        path = find_tool(name, override)
        report.append({"name": name, "path": path, "install": None if path else INSTALL_HINTS.get(name)})
    try:
        import pymobiledevice3  # noqa: F401

        report.append({"name": "pymobiledevice3", "path": "python module", "install": None})
    except ImportError:
        report.append({"name": "pymobiledevice3", "path": None, "install": "./run.sh --setup  (installs into .venv)"})
    return report


class Runner:
    """Runs commands and lets another thread cancel the one in flight."""

    def __init__(self, log=print):
        self.log = log
        self.cancel_event = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def run(self, args: list[str], timeout: float | None = 60, check: bool = False, quiet: bool = False,
            cancellable: bool = True) -> subprocess.CompletedProcess:
        """Run a command, return CompletedProcess with text output.

        `cancellable=False` is used by cleanup steps that must finish even after Cancel.
        """
        if cancellable:
            self.check_cancel()
        if not quiet:
            self.log("$ " + " ".join(args))
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                errors="replace")
        if cancellable:
            with self._lock:
                self._proc = proc
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(args)}")
        finally:
            if cancellable:
                with self._lock:
                    self._proc = None
        if cancellable and self.cancel_event.is_set():
            raise Cancelled()
        result = subprocess.CompletedProcess(args, proc.returncode, out, err)
        if check and proc.returncode != 0:
            raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(args)}\n{(err or out).strip()}")
        return result


def human_size(n: int) -> str:
    size = float(n)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"
