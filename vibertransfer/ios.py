"""iPhone detection and the "restore" step.

WHAT IS AND ISN'T POSSIBLE ON iOS (read this before expecting chats in Viber):

* Viber for iOS keeps its messages inside its own app sandbox. On a normal
  (non-jailbroken) iPhone, nothing on a Mac can write into another app's
  private data: Apple's `house_arrest` service only exposes the Documents
  folder of apps that set UIFileSharingEnabled, and AFC only exposes the
  Media partition.
* Viber publishes no import format for iOS, and its own backup (iCloud on
  iOS, Google Drive on Android) is per-platform.
* So this tool cannot make your Android chats appear inside Viber on the iPhone.

What the restore step DOES do: it copies the readable export (chats/ as .txt
and .json, media/ attachments, manifest.json) into the Documents folder of an
iPhone app you choose that has File Sharing enabled (a file manager, for
example). You can then browse the chats and attachments on the iPhone in the
Files app under "On My iPhone > <that app>". This is the same mechanism as
dragging files into Finder > iPhone > Files. We never pick Viber itself as the
target, and we only write into a new folder named ViberTransfer-<timestamp>.

Detection uses libimobiledevice (`idevice_id -l`, `ideviceinfo`,
`idevicepair`). The copy uses the pymobiledevice3 Python library
(lockdown -> house_arrest -> AFC), which is async in current versions.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .tools import Cancelled, Runner, find_tool

EXCLUDED_PREFIX = "com.viber"  # Viber's iOS bundle ids; we never write into Viber's container
PUSH_FOLDERS = ["chats", "media"]
PUSH_FILES = ["manifest.json"]


def parse_idevice_id(text: str) -> list[str]:
    """`idevice_id -l` prints one UDID per line (older versions may add ' (USB)')."""
    udids = []
    for line in text.splitlines():
        m = re.match(r"^\s*([0-9A-Fa-f-]{20,})\b", line)
        if m:
            udids.append(m.group(1))
    return udids


def parse_ideviceinfo(text: str) -> dict:
    info = {}
    for line in text.splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            info[k.strip()] = v.strip()
    return info


def is_excluded(bundle_id: str) -> bool:
    return bundle_id == EXCLUDED_PREFIX or bundle_id.startswith(EXCLUDED_PREFIX + ".")


def pymobiledevice3_available() -> bool:
    try:
        import pymobiledevice3  # noqa: F401
        return True
    except ImportError:
        return False


class IPhone:
    def __init__(self, runner: Runner):
        self.r = runner

    def detect(self) -> dict:
        """{'state': connected | not_trusted | not_connected | tool_missing, ...device fields}"""
        idevice_id = find_tool("idevice_id")
        if not idevice_id:
            return {"state": "tool_missing", "message": "libimobiledevice is not installed: brew install libimobiledevice"}
        out = self.r.run([idevice_id, "-l"], timeout=15, quiet=True, cancellable=False)
        udids = parse_idevice_id(out.stdout)
        if not udids:
            return {"state": "not_connected", "message": "No iPhone found. Connect it with a USB cable."}
        udid = udids[0]
        pair = find_tool("idevicepair")
        if pair:
            res = self.r.run([pair, "-u", udid, "validate"], timeout=15, quiet=True, cancellable=False)
            if "SUCCESS" not in res.stdout:
                return {"state": "not_trusted", "udid": udid,
                        "message": "Unlock the iPhone and tap Trust, then enter your passcode."}
        info_tool = find_tool("ideviceinfo")
        info = {}
        if info_tool:
            res = self.r.run([info_tool, "-u", udid], timeout=20, quiet=True, cancellable=False)
            if res.returncode != 0:
                return {"state": "not_trusted", "udid": udid,
                        "message": "Unlock the iPhone and tap Trust, then enter your passcode."}
            info = parse_ideviceinfo(res.stdout)
        return {"state": "connected", "udid": udid, "name": info.get("DeviceName", "iPhone"),
                "model": info.get("ProductType", ""), "ios_version": info.get("ProductVersion", "")}

    # ------------------------------------------------------------ pymobiledevice3 parts

    def list_file_sharing_apps(self, udid: str) -> list[dict]:
        """User apps whose Documents folder is reachable (UIFileSharingEnabled), Viber excluded."""
        return asyncio.run(self._list_apps(udid))

    @staticmethod
    async def _list_apps(udid: str) -> list[dict]:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.installation_proxy import InstallationProxyService

        async with await create_using_usbmux(serial=udid) as lockdown:
            apps = await InstallationProxyService(lockdown=lockdown).get_apps(application_type="User")
        result = []
        for bundle_id, info in apps.items():
            if info.get("UIFileSharingEnabled") and not is_excluded(bundle_id):
                result.append({"bundle_id": bundle_id,
                               "name": info.get("CFBundleDisplayName") or info.get("CFBundleName") or bundle_id})
        return sorted(result, key=lambda a: a["name"].lower())

    def push_export(self, udid: str, bundle_id: str, backup_dir: Path, progress=lambda done, total: None) -> dict:
        """Copy chats/, media/ and manifest.json into <app>/Documents/ViberTransfer-<backup name>/ and verify sizes."""
        if is_excluded(bundle_id):
            raise ValueError("Refusing to write into Viber's own app container.")
        return asyncio.run(self._push(udid, bundle_id, Path(backup_dir), progress))

    async def _push(self, udid: str, bundle_id: str, backup_dir: Path, progress) -> dict:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.house_arrest import HouseArrestService

        files = local_push_list(backup_dir)
        remote_root = f"/Documents/ViberTransfer-{backup_dir.name}"
        total_bytes = sum(size for _, size in files)
        done = 0
        async with await create_using_usbmux(serial=udid) as lockdown:
            async with await HouseArrestService.create(lockdown=lockdown, bundle_id=bundle_id,
                                                       documents_only=True) as afc:
                if await afc.exists(remote_root):
                    raise RuntimeError(f"{remote_root} already exists on the iPhone; not overwriting it.")
                await afc.makedirs(remote_root)
                self.r.log(f"Copying {len(files)} files to {bundle_id}:{remote_root}")
                made = {remote_root}
                try:
                    for rel, size in files:
                        if self.r.cancel_event.is_set():
                            raise Cancelled()
                        remote = f"{remote_root}/{rel}"
                        parent = remote.rsplit("/", 1)[0]
                        if parent not in made:
                            await afc.makedirs(parent)
                            made.add(parent)
                        await afc.push(str(backup_dir / rel), remote, progress_bar=False)
                        done += size
                        progress(done, total_bytes)
                    # Verify: every file exists on the phone with the same size.
                    mismatched = []
                    for rel, size in files:
                        st = await afc.stat(f"{remote_root}/{rel}")
                        if int(st.get("st_size", -1)) != size:
                            mismatched.append(rel)
                except BaseException:
                    # Remove only the folder this run created, so a cancelled copy leaves nothing half-done.
                    self.r.log(f"Removing the incomplete folder {remote_root} from the iPhone")
                    try:
                        await afc.rm(remote_root, force=True)
                    except Exception as e:  # noqa: BLE001
                        self.r.log(f"Could not remove {remote_root}: {e}")
                    raise
        return {"bundle_id": bundle_id, "remote_folder": remote_root, "files": len(files),
                "bytes": total_bytes, "verified": not mismatched, "mismatched": mismatched}


def local_push_list(backup_dir: Path) -> list[tuple[str, int]]:
    """(relative path, size) of everything the restore step copies. raw/ stays on the Mac."""
    files = []
    for name in PUSH_FILES:
        p = backup_dir / name
        if p.is_file():
            files.append((name, p.stat().st_size))
    for folder in PUSH_FOLDERS:
        for p in sorted((backup_dir / folder).rglob("*")) if (backup_dir / folder).is_dir() else []:
            if p.is_file():
                files.append((p.relative_to(backup_dir).as_posix(), p.stat().st_size))
    return files
