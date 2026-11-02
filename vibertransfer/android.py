"""Talking to the Android phone over adb.

Two ways to get Viber's data:

1. Root (preferred). The companion app "Viber Transfer Helper" (android-helper/)
   asks Magisk/SuperSU for root, copies Viber's app data and media into
   STAGING, writes sha256 checksums next to them and makes the folder readable
   by adb. We then `adb pull` it.

2. `adb backup` (fallback, no root). Only works if Viber allows backup in its
   manifest AND (the phone runs Android < 12 OR Viber targets an SDK < 31).
   We check both before trying.

Nothing here writes to Viber's own data. The only things we ever delete on the
phone are our own staging folder (a copy we made) and, only after the user
confirms in a dialog, the helper app itself.
"""
from __future__ import annotations

import json
import re
import shutil
import tarfile
import time
import zlib
from pathlib import Path

from .tools import Runner

VIBER_PKG = "com.viber.voip"
HELPER_PKG = "com.vibertransfer.helper"
HELPER_ACTIVITY = f"{HELPER_PKG}/.MainActivity"
# /data/local/tmp is owned by the adb "shell" user, so after the helper chowns the
# copy to shell we can `adb pull` it. Other apps cannot read it. (Shared storage
# like /sdcard would expose the copy to any app with storage permission.)
STAGING = "/data/local/tmp/vibertransfer_staging"
STATUS_PATH = f"/sdcard/Android/data/{HELPER_PKG}/files/status.json"
STATUS_LOG_TAG = "VTStatus"


# ---------------------------------------------------------------- parsers (pure, unit-tested)

def parse_adb_devices(text: str) -> list[dict]:
    """Parse `adb devices -l`. States: device, unauthorized, offline, no permissions, ..."""
    devices = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        m = re.match(r"^(\S+)\s+(no permissions|unauthorized|offline|device|recovery|sideload|bootloader)\b(.*)$",
                     line)
        if not m:
            continue
        serial, state, rest = m.groups()
        props = dict(re.findall(r"(\w+):(\S+)", rest))
        devices.append({"serial": serial, "state": state, "model": props.get("model", "").replace("_", " ")})
    return devices


def parse_dumpsys_package(text: str) -> dict:
    """Pull versionName/versionCode/targetSdk/ALLOW_BACKUP out of `dumpsys package <pkg>`.

    Only the first package block is read (dumpsys can also list a disabled system copy).
    """
    info = {"version_name": None, "version_code": None, "target_sdk": None, "allow_backup": None}
    m = re.search(r"versionCode=(\d+)", text)
    if m:
        info["version_code"] = int(m.group(1))
    m = re.search(r"targetSdk=(\d+)", text)
    if m:
        info["target_sdk"] = int(m.group(1))
    m = re.search(r"versionName=(\S+)", text)
    if m:
        info["version_name"] = m.group(1)
    m = re.search(r"\bpkgFlags=\[([^\]]*)\]", text) or re.search(r"\bflags=\[([^\]]*)\]", text)
    if m:
        info["allow_backup"] = "ALLOW_BACKUP" in m.group(1).split()
    return info


def adb_backup_possible(device_sdk: int | None, viber: dict) -> tuple[bool, str]:
    """Can `adb backup` include Viber's data? Returns (possible, plain-language reason)."""
    if viber.get("allow_backup") is False:
        return False, "Viber opts out of Android backup (android:allowBackup=false), so adb backup gets no data."
    if viber.get("allow_backup") is None:
        return False, "Could not read Viber's backup flag from the phone."
    if device_sdk and device_sdk >= 31 and (viber.get("target_sdk") or 0) >= 31:
        return False, ("This phone runs Android 12 or newer and Viber targets Android 12+; Android excludes such "
                       "apps' data from adb backup.")
    return True, "Viber allows adb backup on this phone."


def parse_status(text: str, run_id: str) -> dict | None:
    """Parse the helper's status.json; ignore it if it is from an earlier run."""
    text = text.strip()
    if not text.startswith("{"):
        return None
    try:
        status = json.loads(text)
    except ValueError:
        return None
    return status if status.get("run_id") == run_id else None


def extract_ab(ab_path: Path, out_dir: Path) -> list[str]:
    """Unpack an Android backup (.ab) file into out_dir. Returns member names.

    Format: 4 text header lines ("ANDROID BACKUP", version, compressed 0/1,
    encryption) then a tar stream, zlib-compressed if the flag is 1. Encrypted
    backups are refused: we don't handle the user's backup password.
    """
    with open(ab_path, "rb") as f:
        header = [f.readline().decode("ascii", "replace").strip() for _ in range(4)]
        if header[0] != "ANDROID BACKUP":
            raise ValueError("Not an Android backup file (bad header).")
        if header[3] != "none":
            raise ValueError("The backup is encrypted. Run it again and leave the backup password empty on the phone.")
        # Decompress in chunks to a temporary .tar next to out_dir (backups can be several GB).
        out_dir = Path(out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        tar_path = out_dir.parent / (out_dir.name + ".tar")
        decomp = zlib.decompressobj() if header[2] == "1" else None
        with open(tar_path, "wb") as tmp:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                tmp.write(decomp.decompress(chunk) if decomp else chunk)
            if decomp:
                tmp.write(decomp.flush())
    try:
        return _extract_tar(tar_path, out_dir)
    finally:
        tar_path.unlink()


def _extract_tar(tar_path: Path, out_dir: Path) -> list[str]:
    names = []
    if tar_path.stat().st_size == 0:  # what adb writes when the app refused to be backed up
        return names
    with tarfile.open(tar_path, mode="r:") as tar:
        for member in tar.getmembers():
            target = (out_dir / member.name).resolve()
            if not str(target).startswith(str(out_dir) + "/"):  # refuse ../ paths
                raise ValueError(f"Unsafe path in backup: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                names.append(member.name)
    return names


# Android backup tar "domain" tokens -> our layout.
AB_DOMAINS = {"db": "raw/databases", "sp": "raw/shared_prefs", "f": "raw/files", "r": "raw/root",
              "ef": "media/external_files", "c": None, "a": None}


def organize_ab(extracted: Path, backup_dir: Path, package: str = VIBER_PKG) -> dict:
    """Move apps/<pkg>/<domain>/... from an extracted .ab into raw/ and media/."""
    app_root = Path(extracted) / "apps" / package
    if not app_root.is_dir():
        raise ValueError("The backup contains no Viber data. Viber probably does not allow adb backup.")
    moved = {"raw": 0, "media": 0}
    for domain_dir in sorted(app_root.iterdir()):
        if not domain_dir.is_dir():
            continue  # _manifest
        dest_rel = AB_DOMAINS.get(domain_dir.name, f"raw/{domain_dir.name}")
        if dest_rel is None:
            continue  # cache / apk
        dest = Path(backup_dir) / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(domain_dir), str(dest))
        moved[dest_rel.split("/")[0]] += sum(1 for p in dest.rglob("*") if p.is_file())
    if moved["raw"] == 0:
        raise ValueError("The backup contains no Viber databases. Viber probably does not allow adb backup.")
    return moved


def organize_staging(pulled: Path, backup_dir: Path) -> dict:
    """Move a pulled staging folder into the backup layout.

    pulled/internal/<dir>  -> raw/<dir>      (databases, shared_prefs, files)
    pulled/external/<name> -> media/<name>   (Viber's folders on shared storage)
    pulled/checksums.sha256, inventory.json -> device_checksums.sha256, device_inventory.json
    """
    pulled, backup_dir = Path(pulled), Path(backup_dir)
    for src_name, dest_name in [("internal", "raw"), ("external", "media")]:
        src = pulled / src_name
        dest = backup_dir / dest_name
        dest.mkdir(exist_ok=True)
        if src.is_dir():
            for child in sorted(src.iterdir()):
                shutil.move(str(child), str(dest / child.name))
    for src_name, dest_name in [("checksums.sha256", "device_checksums.sha256"),
                                ("inventory.json", "device_inventory.json")]:
        if (pulled / src_name).exists():
            shutil.move(str(pulled / src_name), str(backup_dir / dest_name))
    shutil.rmtree(pulled, ignore_errors=True)
    return {"raw": sum(1 for p in (backup_dir / "raw").rglob("*") if p.is_file()),
            "media": sum(1 for p in (backup_dir / "media").rglob("*") if p.is_file())}


# ---------------------------------------------------------------- device access

class Android:
    def __init__(self, runner: Runner, adb: str, serial: str = ""):
        self.r = runner
        self.adb = adb
        self.serial = serial

    def _base(self) -> list[str]:
        return [self.adb, "-s", self.serial] if self.serial else [self.adb]

    def devices(self) -> list[dict]:
        out = self.r.run([self.adb, "devices", "-l"], timeout=20, quiet=True, cancellable=False).stdout
        return parse_adb_devices(out)

    def shell(self, command: str, timeout: float | None = 60, check: bool = False, quiet: bool = False,
              cancellable: bool = True) -> str:
        res = self.r.run(self._base() + ["shell", command], timeout=timeout, check=check, quiet=quiet,
                         cancellable=cancellable)
        return res.stdout

    def device_info(self) -> dict:
        props = ["ro.product.manufacturer", "ro.product.model", "ro.build.version.release", "ro.build.version.sdk"]
        out = self.shell("; ".join(f"getprop {p}" for p in props), quiet=True).splitlines()
        out += [""] * (len(props) - len(out))
        sdk = out[3].strip()
        return {"serial": self.serial, "manufacturer": out[0].strip(), "model": out[1].strip(),
                "android_version": out[2].strip(), "sdk": int(sdk) if sdk.isdigit() else None}

    def package_info(self, package: str) -> dict | None:
        """None if not installed, else parsed dumpsys info."""
        if "package:" not in self.shell(f"pm path {package}", quiet=True):
            return None
        info = parse_dumpsys_package(self.shell(f"dumpsys package {package}", quiet=True))
        info["package"] = package
        return info

    def has_su(self) -> bool:
        """Whether an `su` binary is visible to adb shell. (The real test is the Magisk prompt.)"""
        out = self.shell("command -v su 2>/dev/null || ls /system/xbin/su /system/bin/su /sbin/su 2>/dev/null",
                         quiet=True, cancellable=False)
        return "su" in out

    def install_apk(self, apk: Path) -> None:
        res = self.r.run(self._base() + ["install", "-r", str(apk)], timeout=180)
        if "Success" not in res.stdout:
            raise RuntimeError("Installing the helper app failed:\n" + (res.stderr or res.stdout).strip())

    def launch_helper(self, run_id: str) -> None:
        self.shell(f"am start -n {HELPER_ACTIVITY} -a {HELPER_PKG}.PREPARE --es run_id {run_id}", check=True)

    def send_helper_action(self, action: str) -> None:
        """CANCEL or CLEANUP. Never raises: used during cleanup."""
        try:
            self.shell(f"am start -n {HELPER_ACTIVITY} -a {HELPER_PKG}.{action}", timeout=20, cancellable=False)
        except Exception as e:  # noqa: BLE001 - best effort
            self.r.log(f"Could not send {action} to the helper app: {e}")

    def read_helper_status(self, run_id: str) -> dict | None:
        out = self.shell(f"cat {STATUS_PATH} 2>/dev/null", timeout=20, quiet=True)
        status = parse_status(out, run_id)
        if status is None:
            # Fallback: the helper also logs every status update to logcat.
            out = self.shell(f"logcat -d -v raw -s {STATUS_LOG_TAG}:I | tail -n 1", timeout=20, quiet=True)
            status = parse_status(out, run_id)
        return status

    def force_stop_viber(self) -> None:
        # Stops Viber so its database isn't being written while we copy. No data is touched.
        self.shell(f"am force-stop {VIBER_PKG}")

    def pull(self, remote: str, local: Path) -> None:
        local.mkdir(parents=True, exist_ok=True)
        self.r.run(self._base() + ["pull", remote, str(local)], timeout=None, check=True)

    def staging_exists(self) -> bool:
        return "yes" in self.shell(f"[ -e {STAGING} ] && echo yes", timeout=20, quiet=True, cancellable=False)

    def cleanup_staging(self) -> bool:
        """Remove our own staging copy. Returns True if it is gone."""
        assert STAGING.startswith("/data/local/tmp/vibertransfer"), "refusing to delete outside our staging dir"
        self.shell(f"rm -rf {STAGING}", timeout=120, quiet=True, cancellable=False)
        if not self.staging_exists():
            return True
        # Files may still be root-owned (e.g. cancelled mid-copy); the helper deletes them with root.
        self.send_helper_action("CLEANUP")
        for _ in range(20):
            time.sleep(1)
            if not self.staging_exists():
                return True
        return False

    def adb_backup(self, dest: Path) -> None:
        """Runs `adb backup`; blocks until the user confirms on the phone (or 30 min)."""
        self.r.run(self._base() + ["backup", "-f", str(dest), "-noapk", VIBER_PKG], timeout=1800, check=True)

    def uninstall_helper(self) -> str:
        return self.r.run(self._base() + ["uninstall", HELPER_PKG], timeout=60).stdout.strip()
