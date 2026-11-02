"""The JSON-lines protocol the native Mac app uses, driven exactly like the app drives it."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import make_staging

ROOT = Path(__file__).resolve().parent.parent
FAKE_ADB = Path(__file__).with_name("fake_adb.py")


class EngineProtocolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        make_staging(home / "adb" / "staging_src")
        FAKE_ADB.chmod(FAKE_ADB.stat().st_mode | stat.S_IXUSR)
        (home / "helper.apk").write_bytes(b"fake apk")
        settings = home / "Library" / "Application Support" / "ViberTransfer" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"adb_path": str(FAKE_ADB), "apk_path": str(home / "helper.apk"),
                                        "skip_ios": True}))
        env = dict(os.environ, HOME=str(home), FAKE_ADB_HOME=str(home / "adb"), PYTHONIOENCODING="utf-8")
        env.pop("FAKE_ADB_NO_ROOT", None)
        self.proc = subprocess.Popen([sys.executable, "-m", "vibertransfer", "engine"], cwd=ROOT, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True)
        self.next_id = 0
        self.assertEqual(json.loads(self.proc.stdout.readline())["event"], "ready")

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.stdout.close()
        self.tmp.cleanup()

    def call(self, cmd, **params):
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"id": self.next_id, "cmd": cmd, **params}) + "\n")
        self.proc.stdin.flush()
        reply = json.loads(self.proc.stdout.readline())
        self.assertEqual(reply["id"], self.next_id)
        return reply

    def test_full_transfer_over_the_protocol(self):
        status = self.call("status")["result"]
        self.assertEqual(status["android"]["state"], "connected")
        self.assertEqual(status["android"]["viber"], "24.1.0.5")
        self.assertTrue(status["settings"]["skip_ios"])

        self.assertTrue(self.call("start")["ok"])
        log, log_from, state = [], 0, {}
        for _ in range(100):
            state = self.call("state", **{"from": log_from})["result"]
            log += state["log"]
            log_from = state["log_next"]
            if state.get("result"):
                break
            time.sleep(0.2)
        self.assertEqual(state["result"], "success", state.get("error"))
        self.assertEqual(state["summary"]["messages"], 4)
        self.assertEqual(len(log), log_from, "incremental log must add up to the full log")
        self.assertTrue(any("Exported 4 messages" in line for line in log))

    def test_errors_are_replies_not_crashes(self):
        reply = self.call("bogus")
        self.assertFalse(reply["ok"])
        self.assertIn("unknown command", reply["error"])
        reply = self.call("uninstall_helper")  # not confirmed
        self.assertFalse(reply["ok"])
        self.assertTrue(self.call("state")["ok"])

    def test_settings_are_saved(self):
        result = self.call("settings", values={"android_serial": "R5CX1234567", "unknown_key": 1})["result"]
        self.assertEqual(result["android_serial"], "R5CX1234567")
        self.assertNotIn("unknown_key", result)

    def test_exits_when_the_app_closes_stdin(self):
        self.proc.stdin.close()
        self.assertEqual(self.proc.wait(timeout=30), 0)


if __name__ == "__main__":
    unittest.main()
