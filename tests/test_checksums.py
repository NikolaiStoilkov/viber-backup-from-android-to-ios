import tempfile
import unittest
from pathlib import Path

from vibertransfer.android import organize_staging
from vibertransfer.backup import parse_sha256sum, verify_against_checksums

from helpers import make_staging

H = "a" * 64


class ParseSha256sumTest(unittest.TestCase):
    def test_formats(self):
        text = (f"{H}  ./internal/databases/viber_messages\n"
                f"{H.upper()} *binary/mode.bin\n"
                f"{H}  ./external/Viber Images/IMG 1.jpg\n"
                f"{H}  ./internal/files/we|ird.txt\r\n\n")
        self.assertEqual(parse_sha256sum(text), {
            "internal/databases/viber_messages": H,
            "binary/mode.bin": H,
            "external/Viber Images/IMG 1.jpg": H,
            "internal/files/we|ird.txt": H,
        })

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            parse_sha256sum("sha256sum: not found\n")


class VerifyPulledTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.staging = make_staging(Path(self.tmp.name) / "vibertransfer_staging")
        self.expected = parse_sha256sum((self.staging / "checksums.sha256").read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_match(self):
        self.assertEqual(len(self.expected), 4)
        self.assertEqual(verify_against_checksums(self.staging, self.expected), [])

    def test_corrupted_and_missing(self):
        (self.staging / "external" / "Viber" / "media" / "IMG-1.jpg").write_bytes(b"truncated")
        (self.staging / "internal" / "shared_prefs" / "prefs.xml").unlink()
        problems = verify_against_checksums(self.staging, self.expected)
        self.assertEqual(sorted(problems), [
            "checksum mismatch after pull: external/Viber/media/IMG-1.jpg",
            "missing after pull: internal/shared_prefs/prefs.xml",
        ])

    def test_organize_into_backup_layout(self):
        backup = Path(self.tmp.name) / "backup"
        backup.mkdir()
        counts = organize_staging(self.staging, backup)
        self.assertEqual(counts, {"raw": 2, "media": 1})
        self.assertTrue((backup / "raw" / "databases" / "viber_messages").is_file())
        self.assertTrue((backup / "media" / "Viber" / "media" / "IMG-1.jpg").is_file())
        self.assertTrue((backup / "device_checksums.sha256").is_file())
        self.assertTrue((backup / "device_inventory.json").is_file())
        self.assertFalse(self.staging.exists())


if __name__ == "__main__":
    unittest.main()
