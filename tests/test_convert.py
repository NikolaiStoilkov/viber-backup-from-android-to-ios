import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from vibertransfer.backup import sha256_file
from vibertransfer.convert import SchemaError, ViberJsonConverter, get_converter, to_iso

from helpers import make_viber_db


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "raw" / "databases" / "viber_messages"
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self):
        return ViberJsonConverter().convert(self.root, log=self.logs.append)

    def test_export(self):
        make_viber_db(self.db)
        (self.root / "raw" / "shared_prefs").mkdir()
        (self.root / "raw" / "shared_prefs" / "x.xml").write_text("<map/>")  # not SQLite: ignored
        (self.root / "media" / "Viber" / "media").mkdir(parents=True)
        (self.root / "media" / "Viber" / "media" / "IMG-1.jpg").write_bytes(b"jpg")
        before = sha256_file(self.db)

        result = self.convert()

        self.assertEqual(result["conversations"], 2)
        self.assertEqual(result["messages"], 4)
        self.assertEqual(result["database"], "raw/databases/viber_messages")
        self.assertEqual(sha256_file(self.db), before, "raw/ must stay untouched")

        index = json.loads((self.root / "chats" / "index.json").read_text())
        self.assertEqual([c["name"] for c in index], ["Maria", "Family group"])
        maria = json.loads((self.root / index[0]["json"]).read_text())
        msgs = maria["messages"]
        self.assertEqual([m["text"] for m in msgs], ["Hi!", "Hello back", None])
        self.assertEqual(msgs[0]["time"], "2023-11-14T22:13:20+00:00")
        self.assertEqual([m["direction"] for m in msgs], ["received", "sent", "received"])
        self.assertEqual(msgs[2]["media_file"], "media/Viber/media/IMG-1.jpg")

        txt = (self.root / index[0]["txt"]).read_text()
        self.assertIn("sent: Hello back", txt)
        self.assertIn("[attachment: media/Viber/media/IMG-1.jpg]", txt)
        self.assertIn("messages: _id, conversation_id", (self.root / "schema_report.txt").read_text())
        self.assertTrue(any("send_type" in line and "0×3, 1×1" in line for line in self.logs))

    def test_reads_rows_still_in_wal(self):
        conn = make_viber_db(self.db, wal=True, keep_open=True)
        try:
            self.assertTrue(Path(str(self.db) + "-wal").stat().st_size > 0)
            result = self.convert()
        finally:
            conn.close()
        self.assertEqual(result["messages"], 4)

    def test_unexpected_schema_fails_helpfully(self):
        self.db.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE messages (msg_key TEXT, payload BLOB)")
        conn.execute("CREATE TABLE stickers (_id INTEGER)")
        conn.commit()
        conn.close()
        with self.assertRaises(SchemaError) as ctx:
            self.convert()
        msg = str(ctx.exception)
        self.assertIn("schema_report.txt", msg)
        self.assertIn("raw/", msg)
        self.assertIn("stickers: _id", (self.root / "schema_report.txt").read_text())
        self.assertTrue(any("missing: id" in line for line in self.logs))

    def test_no_databases(self):
        (self.root / "raw").mkdir()
        with self.assertRaises(SchemaError) as ctx:
            self.convert()
        self.assertIn("No SQLite databases", str(ctx.exception))

    def test_names_fall_back_to_ids_without_conversations_table(self):
        make_viber_db(self.db)
        conn = sqlite3.connect(str(self.db))
        conn.execute("DROP TABLE conversations")
        conn.commit()
        conn.close()
        self.convert()
        index = json.loads((self.root / "chats" / "index.json").read_text())
        self.assertEqual(index[0]["name"], "conversation 10")

    def test_timestamp_units(self):
        self.assertEqual(to_iso(1700000000), "2023-11-14T22:13:20+00:00")
        self.assertEqual(to_iso(1700000000000), "2023-11-14T22:13:20+00:00")
        self.assertEqual(to_iso(1700000000000000), "2023-11-14T22:13:20+00:00")
        self.assertIsNone(to_iso(None))
        self.assertIsNone(to_iso(0))

    def test_registry(self):
        self.assertIsInstance(get_converter(), ViberJsonConverter)


if __name__ == "__main__":
    unittest.main()
