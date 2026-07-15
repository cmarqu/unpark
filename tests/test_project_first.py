import io
import json
import os
import queue
import re
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import unpark as W
from unpark import main, parse_welcome, render_html, serve_dashboard

from test_parser import SAMPLE
from test_render_text import derived


class TestDashboardMentionsManual(unittest.TestCase):
    def setUp(self):
        self.w = parse_welcome(SAMPLE)

    def test_served_dashboard_links_to_manual(self):
        out = render_html(self.w, derived(), token="tok")
        self.assertIn('href="/manual"', out)
        self.assertIn("re-entry briefing", out)  # the one-line what-is-this

    def test_static_file_has_no_manual_link(self):
        out = render_html(self.w, derived())  # -o mode: no server behind it
        self.assertNotIn('href="/manual"', out)


class TestDashboardServesManual(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home.name
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(SAMPLE)
        (self.root / "web").mkdir()

    def tearDown(self):
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.state.cleanup()
        self.home.cleanup()

    def test_manual_route_status_fields_and_skill_install(self):
        urls = queue.Queue()
        t = threading.Thread(target=lambda: (
            redirect_stdout(io.StringIO()).__enter__(),
            serve_dashboard(self.root, parse_welcome(SAMPLE),
                            timeout=15.0, grace=1.0, on_bound=urls.put)),
            daemon=True)
        t.start()
        url = urls.get(timeout=5).rstrip("/")

        page = urllib.request.urlopen(url + "/", timeout=5).read().decode()
        tok = re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)
        hdr = {"X-Unpark-Token": tok}

        # same server serves the manual
        man = urllib.request.urlopen(url + "/manual", timeout=5).read().decode()
        self.assertIn("unpark — manual", man)
        self.assertIn('data-scope="global"', man)

        # unified status: dashboard + skill fields (heartbeat for both pages)
        req = urllib.request.Request(url + "/api/status", headers=hdr)
        status = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertIn("running", status)
        self.assertTrue(status["has_project"])
        self.assertFalse(status["global_installed"])

        # skill install works from the dashboard server too
        req = urllib.request.Request(url + "/api/skill/global", method="POST",
                                     headers=hdr, data=b"")
        body = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertTrue(body["ok"])
        self.assertTrue((Path(self.home.name) / ".claude" / "skills"
                         / "unpark-upkeep" / "SKILL.md").exists())
        t.join(timeout=6)


class TestWbhProjectFirst(unittest.TestCase):
    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue() + err.getvalue()

    def test_manual_inside_project_serves_dashboard(self):
        calls = {}
        real_dash, real_docs = W.serve_dashboard, W.serve_docs
        try:
            W.serve_dashboard = lambda *a, **kw: calls.setdefault("dash", True)
            W.serve_docs = lambda *a, **kw: calls.setdefault("docs", True)
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / "WELCOME.md").write_text(SAMPLE)
                rc, _ = self.cli("-C", d, "manual")
        finally:
            W.serve_dashboard, W.serve_docs = real_dash, real_docs
        self.assertEqual(rc, 0)
        self.assertIn("dash", calls)
        self.assertNotIn("docs", calls)

    def test_manual_outside_project_serves_docs(self):
        calls = {}
        real_dash, real_docs = W.serve_dashboard, W.serve_docs
        try:
            W.serve_dashboard = lambda *a, **kw: calls.setdefault("dash", True)
            W.serve_docs = lambda *a, **kw: calls.setdefault("docs", True)
            with tempfile.TemporaryDirectory() as d:
                rc, _ = self.cli("-C", d, "manual")
        finally:
            W.serve_dashboard, W.serve_docs = real_dash, real_docs
        self.assertEqual(rc, 0)
        self.assertIn("docs", calls)
        self.assertNotIn("dash", calls)


if __name__ == "__main__":
    unittest.main()
