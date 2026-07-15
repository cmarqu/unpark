import io
import json
import os
import queue
import re
import signal
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

from unpark import (
    gather_registered,
    parse_welcome,
    registry_add,
    render_portfolio_html,
    serve_dashboard,
    spawn_project_dashboard,
)

from test_parser import SAMPLE


def kill_group(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        os.environ["UNPARK_NO_BROWSER"] = "1"
        self.proj = Path(self.tmp.name) / "spinny"
        self.proj.mkdir()
        (self.proj / "WELCOME.md").write_text(
            SAMPLE.replace("name: photo-globe", "name: spinny"))
        registry_add(self.proj)

    def tearDown(self):
        for v in ("UNPARK_CONFIG_DIR", "UNPARK_STATE_DIR",
                  "UNPARK_NO_BROWSER"):
            os.environ.pop(v, None)
        self.tmp.cleanup()
        self.cfg.cleanup()
        self.state.cleanup()


class TestSpawn(Fixture):
    def test_spawns_a_live_dashboard_for_the_project(self):
        res = spawn_project_dashboard(self.proj)
        self.assertIsNotNone(res)
        try:
            page = urllib.request.urlopen(res["url"], timeout=5).read().decode()
            self.assertIn("spinny", page)
        finally:
            kill_group(res["pid"])

    def test_returns_none_for_hopeless_target(self):
        # no briefing anywhere AND an empty registry: the child has
        # nothing at all to serve and must exit — reported as None
        from unpark import registry_remove
        registry_remove(self.proj)
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertIsNone(spawn_project_dashboard(empty))


class TestOpenButtons(Fixture):
    def test_projects_page_has_open_buttons(self):
        page = render_portfolio_html(gather_registered(), self.proj,
                                     token="tok", title="x")
        self.assertIn('data-path="%s"' % self.proj.resolve(), page)
        self.assertIn("openDash", page)

    def test_static_portfolio_has_no_buttons(self):
        page = render_portfolio_html(gather_registered(), self.proj)
        self.assertNotIn("openDash", page)


class TestOpenApi(Fixture):
    def serve(self):
        urls = queue.Queue()
        t = threading.Thread(target=lambda: serve_dashboard(
            self.proj, parse_welcome(SAMPLE), timeout=15.0, grace=1.0,
            on_bound=urls.put), daemon=True)
        with redirect_stdout(io.StringIO()):
            t.start()
        return t, urls.get(timeout=5).rstrip("/")

    def test_open_endpoint_spawns_registered_project_only(self):
        t, url = self.serve()
        page = urllib.request.urlopen(url + "/", timeout=5).read().decode()
        tok = re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)
        hdr = {"X-Unpark-Token": tok}

        # unregistered path is refused
        req = urllib.request.Request(
            url + "/api/open?p=" + urllib.parse.quote("/etc"),
            method="POST", headers=hdr, data=b"")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 400)

        # registered path spawns and reports a live url
        req = urllib.request.Request(
            url + "/api/open?p=" + urllib.parse.quote(str(self.proj.resolve())),
            method="POST", headers=hdr, data=b"")
        body = json.loads(urllib.request.urlopen(req, timeout=15).read())
        self.assertTrue(body["ok"])
        try:
            child = urllib.request.urlopen(body["url"], timeout=5).read().decode()
            self.assertIn("spinny", child)
        finally:
            kill_group(body["pid"])
        t.join(timeout=6)


if __name__ == "__main__":
    unittest.main()
