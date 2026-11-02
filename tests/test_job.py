"""End-to-end run of TransferJob against tests/fake_adb.py (no real phone needed)."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from vibertransfer.backup import verify_manifest
from vibertransfer.job import TransferJob

from helpers import make_staging

FAKE_ADB = Path(__file__).with_name("fake_adb.py")


class JobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        make_staging(self.home / "staging_src")
        FAKE_ADB.chmod(FAKE_ADB.stat().st_mode | stat.S_IXUSR)
        (self.home / "helper.apk").write_bytes(b"fake apk")
        os.environ["FAKE_ADB_HOME"] = str(self.home)
        os.environ.pop("FAKE_ADB_NO_ROOT", None)
        self.settings = {"adb_path": str(FAKE_ADB), "apk_path": str(self.home / "helper.apk"),
                         "android_serial": "", "ios_target_app": "", "skip_ios": True}

    def tearDown(self):
        os.environ.pop("FAKE_ADB_NO_ROOT", None)
        self.tmp.cleanup()

    def run_job(self) -> TransferJob:
        job = TransferJob(self.settings, backups_root=self.home / "backups")
        job.start()
        job.thread.join(timeout=60)
        self.assertFalse(job.thread.is_alive())
        return job

    def test_root_transfer(self):
        job = self.run_job()
        st = job.snapshot()
        self.assertEqual(st["result"], "success", st["error"] or "\n".join(st["log"]))
        summary = st["summary"]
        backup = Path(summary["backup_dir"])
        self.assertEqual(summary["method"], "root")
        self.assertEqual(summary["conversations"], 2)
        self.assertEqual(summary["messages"], 4)
        self.assertEqual(summary["verified"]["verified_files"], 4)
        self.assertEqual(summary["ios"]["status"], "skipped")
        for rel in ["manifest.json", "raw/databases/viber_messages", "media/Viber/media/IMG-1.jpg",
                    "chats/index.json", "device_checksums.sha256"]:
            self.assertTrue((backup / rel).is_file(), rel)
        self.assertFalse((backup / "_incoming").exists())
        self.assertEqual(verify_manifest(backup), [])
        self.assertFalse((self.home / "phone/data/local/tmp/vibertransfer_staging").exists(),
                         "staging copy must be removed from the phone")
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertEqual(manifest["source_device"]["model"], "SM-S918B")
        self.assertEqual(manifest["viber"]["version_name"], "24.1.0.5")

    def test_no_root_and_no_adb_backup_stops_clearly(self):
        os.environ["FAKE_ADB_NO_ROOT"] = "1"
        st = self.run_job().snapshot()
        self.assertEqual(st["result"], "failed")
        self.assertIn("Root access isn't available", st["error"])
        self.assertIn("allowBackup=false", st["error"])
        self.assertTrue(any("Partial backup kept" in line for line in st["log"]))

    def test_corrupted_pull_is_detected(self):
        (self.home / "staging_src" / "external" / "Viber" / "media" / "IMG-1.jpg").write_bytes(b"corrupt")
        st = self.run_job().snapshot()
        self.assertEqual(st["result"], "failed")
        self.assertIn("differ from the phone's copy", st["error"])
        self.assertFalse((self.home / "phone/data/local/tmp/vibertransfer_staging").exists())

    def test_cancel_before_start_cleans_up(self):
        job = TransferJob(self.settings, backups_root=self.home / "backups")
        job.r.cancel_event.set()
        job.start()
        job.thread.join(timeout=30)
        self.assertEqual(job.snapshot()["result"], "cancelled")


if __name__ == "__main__":
    unittest.main()
