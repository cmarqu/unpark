import io
import json
import os
import queue
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import parse_welcome, serve_dashboard

DASH = """\
---
name: dash-fixture
---

## Recipes

### sleepy

- background: true

```sh
python -c "import time; time.sleep(30)"
```
"""


class DashFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(DASH)
        self.w = parse_welcome(DASH)
        self.urls = queue.Queue()
        self.result = {}
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        self.url = self.urls.get(timeout=5).rstrip("/")

    def _serve(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.result["opened"] = serve_dashboard(
                self.root, self.w, timeout=15.0, grace=1.2,
                on_bound=self.urls.put)

    def tearDown(self):
        self.thread.join(timeout=10)
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.state.cleanup()

    def get(self, path, headers=None):
        req = urllib.request.Request(self.url + path, headers=headers or {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()

    def post(self, path, headers=None):
        req = urllib.request.Request(self.url + path, method="POST",
                                     headers=headers or {}, data=b"")
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode()

    def token(self, page):
        import re
        return re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)


class TestDashboard(DashFixture):
    def test_page_has_heartbeat_and_buttons_and_api_roundtrip(self):
        status, page = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("TOKEN", page)           # embedded session token
        self.assertIn("/api/status", page)     # heartbeat poll
        self.assertIn("sleepy", page)
        tok = self.token(page)
        hdr = {"X-Unpark-Token": tok}

        # status: initially nothing running (poll doubles as heartbeat)
        _, body = self.get("/api/status", hdr)
        self.assertEqual(json.loads(body)["running"], [])

        # start via the API
        status, body = self.post("/api/start/sleepy", hdr)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        _, body = self.get("/api/status", hdr)
        running = json.loads(body)["running"]
        self.assertEqual(running[0]["recipe"], "sleepy")

        # stop via the API
        status, body = self.post("/api/stop/sleepy", hdr)
        self.assertEqual(status, 200)
        _, body = self.get("/api/status", hdr)
        self.assertEqual(json.loads(body)["running"], [])

    def test_api_rejects_missing_or_wrong_token(self):
        _, page = self.get("/")
        status, _ = self.post("/api/start/sleepy")
        self.assertEqual(status, 403)
        status, _ = self.post("/api/start/sleepy",
                              {"X-Unpark-Token": "wrong"})
        self.assertEqual(status, 403)

    def test_server_exits_when_heartbeat_stops(self):
        _, page = self.get("/")
        hdr = {"X-Unpark-Token": self.token(page)}
        self.get("/api/status", hdr)   # one heartbeat
        # then silence: with grace=1.2s the server must exit on its own
        self.thread.join(timeout=6)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.result["opened"])


class TestDashboardNeverOpened(unittest.TestCase):
    def test_exits_false_when_nobody_ever_opens(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "WELCOME.md").write_text(DASH)
            with redirect_stdout(io.StringIO()):
                opened = serve_dashboard(root, parse_welcome(DASH),
                                         timeout=0.5, grace=1.0)
        self.assertFalse(opened)


if __name__ == "__main__":
    unittest.main()
