import io
import json
import os
import queue
import re
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import (
    _docs_context,
    parse_welcome,
    render_docs_html,
    render_html,
    serve_docs,
    start_recipe,
    state_dir,
    stop_recipe,
)

from test_parser import SAMPLE
from test_render_text import derived

BG = SAMPLE.replace("python3 -m http.server 5173",
                    'python -c "import time; time.sleep(30)"')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(BG)
        (self.root / "web").mkdir()
        self.w = parse_welcome(BG)

    def tearDown(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            stop_recipe(self.root, None)
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.state.cleanup()


class TestWorkspaceConfirmation(Fixture):
    def test_ws_init_asks_before_acting(self):
        out = render_html(self.w, derived(), token="tok")
        self.assertIn("confirm(", out)
        # the explanation must be IN the dialog, not just a tooltip
        self.assertIn("Save Workspace As", out.split("confirm(")[1][:600])


class TestManualRunningPanel(Fixture):
    def test_manual_project_view_has_process_controls(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertIn('id="running"', out)
        self.assertIn("doStop", out)
        self.assertIn("/logs/", out)          # log links
        self.assertIn('id="logfiles"', out)   # overview of all logs

    def test_dashboard_shows_log_overview_element(self):
        out = render_html(self.w, derived(), token="tok")
        self.assertIn('id="logfiles"', out)


class TestDocsServerProcessApi(Fixture):
    def serve(self):
        urls = queue.Queue()
        t = threading.Thread(target=lambda: serve_docs(
            self.root, timeout=15.0, grace=1.0, on_bound=urls.put),
            daemon=True)
        with redirect_stdout(io.StringIO()):
            t.start()
        return t, urls.get(timeout=5).rstrip("/")

    def test_status_stop_and_logs_work_on_docs_server(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            start_recipe(self.root, self.w, "demo")
        time.sleep(0.3)
        t, url = self.serve()
        page = urllib.request.urlopen(url + "/", timeout=5).read().decode()
        tok = re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)
        hdr = {"X-Unpark-Token": tok}

        req = urllib.request.Request(url + "/api/status", headers=hdr)
        status = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertEqual(status["running"][0]["recipe"], "demo")
        self.assertIn("demo", status["logs"])

        body = urllib.request.urlopen(url + "/logs/demo",
                                      timeout=5).read().decode()
        self.assertIn("demo.log", body)

        req = urllib.request.Request(url + "/api/stop/demo", method="POST",
                                     headers=hdr, data=b"")
        self.assertTrue(json.loads(
            urllib.request.urlopen(req, timeout=8).read())["ok"])
        req = urllib.request.Request(url + "/api/status", headers=hdr)
        status = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertEqual(status["running"], [])
        t.join(timeout=6)


if __name__ == "__main__":
    unittest.main()
