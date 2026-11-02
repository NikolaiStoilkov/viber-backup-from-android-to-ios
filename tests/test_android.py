import io
import json
import tarfile
import tempfile
import unittest
import zlib
from pathlib import Path

from vibertransfer import android as A

DEVICES_OUTPUT = """List of devices attached
R5CX1234567            device usb:1-1 product:dm3qxeea model:SM_S918B device:dm3q transport_id:3
R5CX7654321            unauthorized usb:1-2 transport_id:4
emulator-5554          offline
0123456789ABCDEF       no permissions (user in plugdev group); see [http://developer.android.com/tools/device.html] usb:1-3

"""

DUMPSYS_OUTPUT = """Packages:
  Package [com.viber.voip] (8d1f2a3):
    userId=10345
    pkg=Package{5b0c1d2 com.viber.voip}
    codePath=/data/app/~~abc==/com.viber.voip-xyz==
    versionCode=2410005 minSdk=26 targetSdk=34
    versionName=24.1.0.5
    flags=[ HAS_CODE ALLOW_CLEAR_USER_DATA ]
    privateFlags=[ PRIVATE_FLAG_ACTIVITIES_RESIZE_MODE_RESIZEABLE ]
    pkgFlags=[ HAS_CODE ALLOW_CLEAR_USER_DATA ]
"""


class ParsersTest(unittest.TestCase):
    def test_adb_devices(self):
        devices = A.parse_adb_devices(DEVICES_OUTPUT)
        self.assertEqual([(d["serial"], d["state"]) for d in devices], [
            ("R5CX1234567", "device"), ("R5CX7654321", "unauthorized"),
            ("emulator-5554", "offline"), ("0123456789ABCDEF", "no permissions")])
        self.assertEqual(devices[0]["model"], "SM S918B")

    def test_adb_devices_empty(self):
        self.assertEqual(A.parse_adb_devices("* daemon started successfully\nList of devices attached\n\n"), [])

    def test_dumpsys(self):
        info = A.parse_dumpsys_package(DUMPSYS_OUTPUT)
        self.assertEqual(info, {"version_name": "24.1.0.5", "version_code": 2410005, "target_sdk": 34,
                                "allow_backup": False})
        info = A.parse_dumpsys_package(DUMPSYS_OUTPUT.replace("HAS_CODE ALLOW_CLEAR", "HAS_CODE ALLOW_BACKUP ALLOW_CLEAR"))
        self.assertTrue(info["allow_backup"])

    def test_adb_backup_possible(self):
        self.assertFalse(A.adb_backup_possible(34, {"allow_backup": False, "target_sdk": 34})[0])
        self.assertFalse(A.adb_backup_possible(34, {"allow_backup": None})[0])
        no, why = A.adb_backup_possible(33, {"allow_backup": True, "target_sdk": 34})
        self.assertFalse(no)
        self.assertIn("Android 12", why)
        self.assertTrue(A.adb_backup_possible(30, {"allow_backup": True, "target_sdk": 34})[0])
        self.assertTrue(A.adb_backup_possible(34, {"allow_backup": True, "target_sdk": 30})[0])

    def test_status(self):
        text = json.dumps({"run_id": "abc", "state": "copying", "files_done": 3})
        self.assertEqual(A.parse_status(text, "abc")["state"], "copying")
        self.assertIsNone(A.parse_status(text, "other-run"))
        self.assertIsNone(A.parse_status("cat: /sdcard/...: No such file or directory", "abc"))
        self.assertIsNone(A.parse_status("{broken", "abc"))


def make_ab(path: Path, members: dict, compressed=True, encryption="none"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    payload = zlib.compress(buf.getvalue()) if compressed else buf.getvalue()
    header = f"ANDROID BACKUP\n5\n{1 if compressed else 0}\n{encryption}\n".encode()
    path.write_bytes(header + payload)


class AndroidBackupFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_extract_and_organize(self):
        for compressed in (True, False):
            with self.subTest(compressed=compressed):
                work = self.dir / f"c{compressed}"
                work.mkdir()
                ab = work / "viber.ab"
                make_ab(ab, {"apps/com.viber.voip/_manifest": b"m",
                             "apps/com.viber.voip/db/viber_messages": b"SQLite format 3\x00...",
                             "apps/com.viber.voip/sp/prefs.xml": b"<map/>",
                             "apps/com.viber.voip/ef/media/a.jpg": b"jpg",
                             "apps/com.viber.voip/c/cache.bin": b"skip"}, compressed=compressed)
                names = A.extract_ab(ab, work / "x")
                self.assertEqual(len(names), 5)
                backup = work / "backup"
                backup.mkdir()
                self.assertEqual(A.organize_ab(work / "x", backup), {"raw": 2, "media": 1})
                self.assertTrue((backup / "raw" / "databases" / "viber_messages").is_file())
                self.assertTrue((backup / "media" / "external_files" / "media" / "a.jpg").is_file())
                self.assertFalse((backup / "raw" / "c").exists())

    def test_encrypted_is_refused(self):
        ab = self.dir / "enc.ab"
        make_ab(ab, {}, encryption="AES-256")
        with self.assertRaises(ValueError) as ctx:
            A.extract_ab(ab, self.dir / "x")
        self.assertIn("password", str(ctx.exception))

    def test_empty_backup_means_viber_refused(self):
        ab = self.dir / "empty.ab"
        ab.write_bytes(b"ANDROID BACKUP\n5\n1\nnone\n" + zlib.compress(b""))
        self.assertEqual(A.extract_ab(ab, self.dir / "x"), [])
        with self.assertRaises(ValueError) as ctx:
            A.organize_ab(self.dir / "x", self.dir)
        self.assertIn("does not allow", str(ctx.exception))

    def test_path_traversal_is_refused(self):
        ab = self.dir / "evil.ab"
        make_ab(ab, {"../../evil.txt": b"x"})
        with self.assertRaises(ValueError):
            A.extract_ab(ab, self.dir / "x")

    def test_not_a_backup(self):
        ab = self.dir / "nope.ab"
        ab.write_bytes(b"PK\x03\x04 zip file")
        with self.assertRaises(ValueError):
            A.extract_ab(ab, self.dir / "x")


if __name__ == "__main__":
    unittest.main()
