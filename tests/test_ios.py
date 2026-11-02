import tempfile
import unittest
from pathlib import Path

from vibertransfer.ios import is_excluded, local_push_list, parse_idevice_id, parse_ideviceinfo


class IosParsersTest(unittest.TestCase):
    def test_idevice_id(self):
        out = "00008110-001A2B3C4D5E801E\n00008030-000A11112222333E (USB)\nERROR: something\n"
        self.assertEqual(parse_idevice_id(out), ["00008110-001A2B3C4D5E801E", "00008030-000A11112222333E"])
        self.assertEqual(parse_idevice_id(""), [])

    def test_ideviceinfo(self):
        out = "DeviceName: Nikolay's iPhone\nProductType: iPhone16,1\nProductVersion: 18.6\n"
        info = parse_ideviceinfo(out)
        self.assertEqual(info["DeviceName"], "Nikolay's iPhone")
        self.assertEqual(info["ProductVersion"], "18.6")

    def test_viber_is_never_a_target(self):
        self.assertTrue(is_excluded("com.viber"))
        self.assertTrue(is_excluded("com.viber.share"))
        self.assertFalse(is_excluded("com.viberlike.files"))
        self.assertFalse(is_excluded("com.example.filemanager"))

    def test_push_list_leaves_raw_on_mac(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ["manifest.json", "raw/databases/viber_messages", "chats/index.json", "media/Viber/a.jpg",
                        "schema_report.txt"]:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("x")
            self.assertEqual([r for r, _ in local_push_list(root)],
                             ["manifest.json", "chats/index.json", "media/Viber/a.jpg"])


if __name__ == "__main__":
    unittest.main()
