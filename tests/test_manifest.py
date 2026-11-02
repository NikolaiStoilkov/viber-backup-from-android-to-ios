import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from vibertransfer.backup import (MANIFEST_NAME, build_file_index, create_manifest, load_manifest, new_backup_dir,
                                  sha256_file, verify_manifest)

DEVICE = {"serial": "R5CX123", "manufacturer": "samsung", "model": "SM-S918B", "android_version": "14", "sdk": 34}
VIBER = {"package": "com.viber.voip", "version_name": "24.1.0.5", "version_code": 2410005}


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "raw" / "databases").mkdir(parents=True)
        (self.root / "raw" / "databases" / "viber_messages").write_bytes(b"db" * 100)
        (self.root / "media").mkdir()
        (self.root / "media" / "a.jpg").write_bytes(b"jpeg")
        (self.root / "chats").mkdir()
        (self.root / "chats" / "index.json").write_text("[]")

    def tearDown(self):
        self.tmp.cleanup()

    def test_manifest_contents(self):
        now = dt.datetime(2026, 11, 2, 9, 30, tzinfo=dt.timezone.utc)
        m = create_manifest(self.root, method="root", device=DEVICE, viber=VIBER,
                            conversion={"conversations": 2, "messages": 4}, transfer={"verified_files": 2}, now=now)
        self.assertEqual(m["format"], "vibertransfer-backup")
        self.assertEqual(m["created_at"], "2026-11-02T09:30:00+00:00")
        self.assertEqual(m["source_device"]["model"], "SM-S918B")
        self.assertEqual(m["viber"]["version_name"], "24.1.0.5")
        self.assertEqual(m["counts"]["files"], 3)
        self.assertEqual(m["counts"]["bytes"], 200 + 4 + 2)
        self.assertEqual(m["counts"]["by_folder"]["raw"], {"files": 1, "bytes": 200})
        self.assertEqual(m["counts"]["conversations"], 2)
        self.assertEqual(m["counts"]["messages"], 4)
        self.assertEqual(m["files"]["media/a.jpg"]["sha256"], sha256_file(self.root / "media" / "a.jpg"))
        self.assertNotIn(MANIFEST_NAME, m["files"])
        # It is written to disk and loads back identically.
        self.assertEqual(load_manifest(self.root), json.loads(json.dumps(m)))

    def test_verify_ok(self):
        create_manifest(self.root, method="root", device=DEVICE, viber=VIBER)
        self.assertEqual(verify_manifest(self.root), [])

    def test_verify_detects_changes(self):
        create_manifest(self.root, method="root", device=DEVICE, viber=VIBER)
        (self.root / "media" / "a.jpg").write_bytes(b"JPEG")  # same size, different content
        (self.root / "chats" / "index.json").unlink()
        (self.root / "media" / "new.png").write_bytes(b"x")
        problems = verify_manifest(self.root)
        self.assertIn("checksum mismatch: media/a.jpg", problems)
        self.assertIn("missing: chats/index.json", problems)
        self.assertIn("not in manifest: media/new.png", problems)

    def test_verify_detects_size_change(self):
        create_manifest(self.root, method="root", device=DEVICE, viber=VIBER)
        (self.root / "media" / "a.jpg").write_bytes(b"longer jpeg")
        self.assertEqual(verify_manifest(self.root), ["size mismatch: media/a.jpg"])

    def test_index_is_sorted_and_relative(self):
        self.assertEqual(list(build_file_index(self.root)),
                         ["chats/index.json", "media/a.jpg", "raw/databases/viber_messages"])

    def test_load_rejects_other_json(self):
        (self.root / MANIFEST_NAME).write_text('{"hello": 1}')
        with self.assertRaises(ValueError):
            load_manifest(self.root)

    def test_new_backup_dir_is_unique(self):
        now = dt.datetime(2026, 11, 2, 9, 30, 0)
        a = new_backup_dir(self.root / "backups", now)
        b = new_backup_dir(self.root / "backups", now)
        self.assertEqual(a.name, "2026-11-02_09-30-00")
        self.assertEqual(b.name, "2026-11-02_09-30-00_2")


if __name__ == "__main__":
    unittest.main()
