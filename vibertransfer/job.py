"""The transfer job: runs every step in order on a background thread.

The Mac app (through engine.py) polls `TransferJob.snapshot()` for progress, the log, plain-language
prompts ("Tap Allow on the phone") and choices it must answer.
"""
from __future__ import annotations

import datetime as _dt
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import android as A
from .backup import (create_manifest, load_manifest, parse_sha256sum, sha256_file, new_backup_dir,
                     verify_against_checksums)
from .convert import SchemaError, get_converter
from .ios import IPhone, local_push_list, pymobiledevice3_available
from .tools import BACKUPS_ROOT, DEFAULT_APK, Cancelled, Runner, human_size, require_tool

STEPS = [
    "Check tools",
    "Connect Android phone",
    "Root access on phone",
    "Copy Viber data on phone",
    "Pull backup to Mac",
    "Verify checksums",
    "Convert chats",
    "Write manifest",
    "Copy to iPhone",
]


class Failure(Exception):
    """A stop with a plain-language explanation for the user."""


class TransferJob:
    def __init__(self, settings: dict, backups_root: Path = BACKUPS_ROOT):
        self.settings = settings
        self.backups_root = backups_root
        self.lines: list[str] = []
        self.r = Runner(log=self.log)
        self.lock = threading.Lock()
        self.state = {"running": False, "step": None, "step_index": -1, "steps": STEPS, "progress": None,
                      "prompt": None, "choice": None, "result": None, "error": None, "summary": None,
                      "skipped": []}
        self._answer = None
        self._answer_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.backup_dir: Path | None = None
        self.android: A.Android | None = None
        self.staging_used = False

    # ------------------------------------------------------------ UI plumbing

    def log(self, msg: str) -> None:
        line = f"{_dt.datetime.now():%H:%M:%S}  {msg}"
        with self.lock:
            self.lines.append(line)
            del self.lines[:-5000]
        print(line, flush=True)

    def set(self, **kw) -> None:
        with self.lock:
            self.state.update(kw)

    def snapshot(self, log_from: int = 0) -> dict:
        with self.lock:
            snap = dict(self.state)
            snap["log"] = self.lines[log_from:]
            snap["log_next"] = len(self.lines)
        return snap

    def step(self, name: str) -> None:
        self.r.check_cancel()
        self.set(step=name, step_index=STEPS.index(name), progress=None, prompt=None)
        self.log(f"── {name}")

    def skip(self, name: str, why: str) -> None:
        self.log(f"── {name}: skipped ({why})")
        with self.lock:
            self.state["skipped"].append(name)

    def prompt(self, text: str | None) -> None:
        if text and text != self.state.get("prompt"):
            self.log("ACTION NEEDED: " + text)
        self.set(prompt=text)

    def ask(self, question: str, options: list[dict]) -> str:
        """Block until the UI answers with one of options[i]['value']."""
        self._answer_event.clear()
        self.set(choice={"question": question, "options": options})
        self.log("ACTION NEEDED: " + question)
        while not self._answer_event.wait(0.5):
            self.r.check_cancel()
        self.r.check_cancel()
        self.set(choice=None)
        return self._answer

    def answer(self, value: str) -> None:
        self._answer = value
        self._answer_event.set()

    def sleep(self, seconds: float) -> None:
        if self.r.cancel_event.wait(seconds):
            raise Cancelled()

    def start(self) -> None:
        self.set(running=True)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def cancel(self) -> None:
        self.log("Cancel requested: stopping and cleaning up…")
        self.r.cancel()
        self._answer_event.set()

    # ------------------------------------------------------------ the job

    def _run(self) -> None:
        try:
            summary = self._transfer()
            self.set(result="success", summary=summary)
            self.log("Done.")
        except Cancelled:
            self._cleanup(cancelled=True)
            self.set(result="cancelled")
            self.log("Cancelled. Temporary files were cleaned up; nothing was changed on either phone.")
        except Exception as e:  # noqa: BLE001 - every failure is shown to the user
            self._cleanup(cancelled=False)
            self.set(result="failed", error=str(e))
            self.log(f"STOPPED: {e}")
        finally:
            self.set(running=False, prompt=None, choice=None, progress=None)

    def _cleanup(self, cancelled: bool) -> None:
        """Remove our temporary files. Never touches Viber's data or anything we didn't create."""
        if self.android and self.staging_used:
            self.android.send_helper_action("CANCEL")
            self.log(f"Removing the temporary copy {A.STAGING} from the phone")
            if not self.android.cleanup_staging():
                self.log(f"Could not confirm removal of {A.STAGING}. It is only readable via adb; "
                         "run the transfer again or remove it from the helper app.")
        if self.backup_dir and self.backup_dir.exists():
            shutil.rmtree(self.backup_dir / "_incoming", ignore_errors=True)
            if cancelled and not (self.backup_dir / "manifest.json").exists():
                shutil.rmtree(self.backup_dir, ignore_errors=True)
                self.log(f"Removed the incomplete backup folder {self.backup_dir}")
            elif not cancelled:
                self.log(f"Partial backup kept for inspection: {self.backup_dir}")

    def _transfer(self) -> dict:
        s = self.settings
        self.step("Check tools")
        adb = require_tool("adb", s.get("adb_path", ""))
        self.log(f"adb: {adb}")

        self.step("Connect Android phone")
        self.android = droid = A.Android(self.r, adb)
        droid.serial = self._wait_for_android(droid, s.get("android_serial", ""))
        device = droid.device_info()
        self.log(f"Phone: {device['manufacturer']} {device['model']}, Android {device['android_version']} "
                 f"(SDK {device['sdk']}), serial {device['serial']}")
        viber = droid.package_info(A.VIBER_PKG)
        if not viber:
            raise Failure("Viber is not installed on the Android phone.")
        self.log(f"Viber {viber['version_name']} (code {viber['version_code']}, targetSdk {viber['target_sdk']}, "
                 f"allowBackup={viber['allow_backup']})")

        self.backup_dir = new_backup_dir(self.backups_root)
        self.log(f"Backup folder: {self.backup_dir}")

        method, transfer = self._copy_with_root(droid)
        if method is None:
            method, transfer = self._copy_with_adb_backup(droid, device, viber)

        self.step("Convert chats")
        conversion = self._convert()

        self.step("Write manifest")
        manifest = create_manifest(self.backup_dir, method=method, device=device, viber=viber,
                                   conversion=conversion, transfer=transfer)
        self.log(f"manifest.json written: {manifest['counts']['files']} files, "
                 f"{human_size(manifest['counts']['bytes'])}")

        self.step("Copy to iPhone")
        ios = self._restore_to_iphone(conversion)
        if ios["status"] == "skipped":
            self.skip("Copy to iPhone", ios["detail"])
        elif ios["status"] == "failed":
            self.log("iPhone copy failed: " + ios["detail"])
        return self._summary(manifest, ios)

    # ------------------------------------------------------------ Android

    def _wait_for_android(self, droid: A.Android, wanted: str) -> str:
        deadline = time.time() + 300
        while True:
            devices = droid.devices()
            if wanted:
                devices = [d for d in devices if d["serial"] == wanted]
            ready = [d for d in devices if d["state"] == "device"]
            if ready:
                self.prompt(None)
                if len(ready) > 1:
                    self.log(f"{len(ready)} phones connected; using {ready[0]['serial']} (choose one in Settings).")
                return ready[0]["serial"]
            if any(d["state"] == "unauthorized" for d in devices):
                self.prompt("On the Android phone, tap Allow on “Allow USB debugging?” "
                            "(tick “Always allow from this computer”).")
            elif any(d["state"] == "no permissions" for d in devices):
                raise Failure("adb has no permission to talk to the phone. Unplug it, plug it back in, and retry.")
            else:
                self.prompt("Connect the Android phone with a USB cable, unlock it, and make sure USB debugging "
                            "is on (Settings › Developer options).")
            if time.time() > deadline:
                raise Failure("No authorized Android phone was found within 5 minutes.")
            self.sleep(2)

    def _copy_with_root(self, droid: A.Android):
        """Returns (method, transfer_info), or (None, None) to fall back to adb backup."""
        self.step("Root access on phone")
        apk = Path(self.settings.get("apk_path") or DEFAULT_APK)
        if not apk.is_file():
            self.log(f"Helper app not found at {apk}. Build it with ./build-apk.sh. Trying the no-root method.")
            return None, None
        if not droid.has_su():
            self.log("No 'su' binary found on the phone, so it is not rooted. Trying the no-root method.")
            return None, None

        self.log("Installing Viber Transfer Helper on the phone…")
        droid.install_apk(apk)
        self.log("Stopping Viber on the phone so its database is not changing while we copy it.")
        droid.force_stop_viber()
        run_id = uuid.uuid4().hex[:12]
        self.staging_used = True
        droid.launch_helper(run_id)

        started = time.time()
        last = None
        while True:
            status = droid.read_helper_status(run_id)
            state = status.get("state") if status else None
            if state != last and status:
                self.log(f"Helper: {state}" + (f" – {status['message']}" if status.get("message") else ""))
                last = state
            if state is None and time.time() - started > 60:
                raise Failure("The helper app did not respond. Open “Viber Transfer Helper” on the phone and retry.")
            elif state == "waiting_for_user":
                self.prompt("On the phone, tap “Start copy” in Viber Transfer Helper.")
            elif state == "requesting_root":
                self.prompt("On the phone, tap Grant in the Magisk (or SuperSU) prompt for Viber Transfer Helper.")
            elif state in ("root_denied", "no_root"):
                self.prompt(None)
                self.log("Root was not granted to the helper app. Trying the no-root method.")
                self.staging_used = False
                return None, None
            elif state in ("copying", "checksumming"):
                if self.state["step"] != "Copy Viber data on phone":
                    self.step("Copy Viber data on phone")
                    for f in status.get("found", []):
                        self.log(f"  found {f['name']}: {f['path']} ({f['files']} files, {f['kb']} KB)")
                total = status.get("files_total") or 0
                if total:
                    self.set(progress=round(100 * status.get("files_done", 0) / total, 1))
                if state == "checksumming":
                    self.set(progress=None)
            elif state == "done":
                break
            elif state == "error":
                raise Failure("The helper app reported an error: " + status.get("message", "unknown"))
            self.sleep(1)

        self.step("Pull backup to Mac")
        incoming = self.backup_dir / "_incoming"
        droid.pull(A.STAGING, incoming)
        pulled = incoming / Path(A.STAGING).name

        self.step("Verify checksums")
        sums_file = pulled / "checksums.sha256"
        if not sums_file.is_file():
            raise Failure("The phone did not produce checksums.sha256, so the copy cannot be verified.")
        expected = parse_sha256sum(sums_file.read_text())
        problems = verify_against_checksums(pulled, expected)
        if problems:
            raise Failure(f"{len(problems)} files differ from the phone's copy, e.g. {problems[0]}. "
                          "The cable may have disconnected; please run the transfer again.")
        self.log(f"All {len(expected)} files match the checksums computed on the phone.")
        counts = A.organize_staging(pulled, self.backup_dir)
        shutil.rmtree(incoming, ignore_errors=True)
        self.log(f"raw/: {counts['raw']} files, media/: {counts['media']} files")
        droid.cleanup_staging()
        self.staging_used = False
        self.log("Removed the temporary copy from the phone.")
        return "root", {"verified_files": len(expected), "verified_against": "device_checksums.sha256"}

    def _copy_with_adb_backup(self, droid: A.Android, device: dict, viber: dict):
        possible, reason = A.adb_backup_possible(device.get("sdk"), viber)
        self.log(reason)
        if not possible:
            raise Failure("Root access isn't available and Viber's data can't be read without it. " + reason +
                          " Viber's own backup (Google Drive) can't be restored on an iPhone. To continue with this "
                          "tool, root the phone (e.g. with Magisk) and run the transfer again.")
        self.step("Copy Viber data on phone")
        self.log("Using adb backup (no root).")
        incoming = self.backup_dir / "_incoming"
        incoming.mkdir()
        ab = incoming / "viber.ab"
        self.prompt("On the phone, tap “Back up my data”. Leave the password field empty.")
        droid.adb_backup(ab)  # this one command both copies and transfers to the Mac
        self.prompt(None)
        self.skip("Pull backup to Mac", "adb backup already wrote the file to the Mac")

        self.step("Verify checksums")
        ab_hash = sha256_file(ab)
        self.log(f"viber.ab: {human_size(ab.stat().st_size)}, sha256 {ab_hash}")
        names = A.extract_ab(ab, incoming / "extracted")
        self.log(f"Backup archive contains {len(names)} files and unpacked cleanly.")
        counts = A.organize_ab(incoming / "extracted", self.backup_dir)
        shutil.rmtree(incoming, ignore_errors=True)
        self.log(f"raw/: {counts['raw']} files, media/: {counts['media']} files")
        return "adb-backup", {"ab_sha256": ab_hash, "ab_files": len(names)}

    def _convert(self) -> dict:
        try:
            return get_converter().convert(self.backup_dir, log=self.log)
        except SchemaError as e:
            self.log(f"Conversion failed: {e}")
            return {"error": str(e)}

    # ------------------------------------------------------------ iPhone

    def _restore_to_iphone(self, conversion: dict) -> dict:
        if self.settings.get("skip_ios"):
            return {"status": "skipped", "detail": "iPhone step turned off in Settings."}
        if conversion.get("error"):
            return {"status": "skipped", "detail": "Chats could not be converted, so there is nothing to copy."}
        if not pymobiledevice3_available():
            return {"status": "skipped", "detail": "pymobiledevice3 is not installed. Run ./run.sh --setup."}
        iphone = IPhone(self.r)
        deadline = time.time() + 180
        while True:
            info = iphone.detect()
            if info["state"] == "connected":
                self.prompt(None)
                break
            if info["state"] == "tool_missing" or time.time() > deadline:
                self.prompt(None)
                return {"status": "skipped", "detail": info["message"] + " The backup is complete on the Mac."}
            self.prompt(info["message"] + " (Waiting up to 3 minutes; the backup is already saved on the Mac.)")
            self.sleep(3)
        self.log(f"iPhone: {info['name']} ({info['model']}, iOS {info['ios_version']})")

        apps = iphone.list_file_sharing_apps(info["udid"])
        target = self.settings.get("ios_target_app") or ""
        if target and target not in {a["bundle_id"] for a in apps}:
            self.log(f"{target} is not installed or doesn't share files; choosing again.")
            target = ""
        if not target:
            if not apps:
                return {"status": "skipped", "detail": "No app on the iPhone accepts files over USB. Install a file "
                        "manager app that appears in Finder › iPhone › Files, then run the transfer again."}
            options = [{"value": a["bundle_id"], "label": f"{a['name']} ({a['bundle_id']})"} for a in apps]
            options.append({"value": "", "label": "Skip – keep the backup on the Mac only"})
            target = self.ask("Which iPhone app should receive the chat export? You'll find it in the Files app "
                              "under On My iPhone.", options)
            if not target:
                return {"status": "skipped", "detail": "You chose to keep the backup on the Mac only."}

        # The restore reads from the backup folder; make sure what we copy still matches the manifest.
        manifest = load_manifest(self.backup_dir)["files"]
        for rel, _ in local_push_list(self.backup_dir):
            if rel != "manifest.json" and sha256_file(self.backup_dir / rel) != manifest[rel]["sha256"]:
                raise Failure(f"{rel} changed since the backup was made; not copying to the iPhone.")

        result = iphone.push_export(info["udid"], target, self.backup_dir,
                                    progress=lambda d, t: self.set(progress=round(100 * d / t, 1) if t else None))
        if not result["verified"]:
            return {"status": "failed", "detail": f"{len(result['mismatched'])} files have the wrong size on the "
                    "iPhone, e.g. " + result["mismatched"][0], **result}
        detail = (f"Copied {result['files']} files ({human_size(result['bytes'])}) to {target}{result['remote_folder']}. "
                  "Open the Files app › On My iPhone to read them. Viber itself will not show these chats.")
        self.log(detail)
        return {"status": "succeeded", "detail": detail, **result}

    def _summary(self, manifest: dict, ios: dict) -> dict:
        counts = manifest["counts"]
        return {
            "backup_dir": str(self.backup_dir),
            "method": manifest["method"],
            "device": f"{manifest['source_device']['manufacturer']} {manifest['source_device']['model']}",
            "viber_version": manifest["viber"].get("version_name"),
            "files": counts["files"],
            "size": human_size(counts["bytes"]),
            "folders": {(k + "/" if k != "." else "other files"): {"files": v["files"], "size": human_size(v["bytes"])}
                        for k, v in counts["by_folder"].items()},
            "conversations": counts.get("conversations"),
            "messages": counts.get("messages"),
            "conversion_error": manifest["conversion"].get("error"),
            "verified": manifest["transfer"],
            "ios": ios,
        }
