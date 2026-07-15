import io
import os
import queue
import re
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

from unpark import (
    gather_registered,
    parse_welcome,
    registry_add,
    render_html,
    render_portfolio_html,
    serve_dashboard,
    serve_docs,
)

from test_parser import SAMPLE
from test_render_text import derived


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name) / "proj"
        self.root.mkdir()
        (self.root / "WELCOME.md").write_text(SAMPLE)
        registry_add(self.root)

    def tearDown(self):
        os.environ.pop("UNPARK_CONFIG_DIR", None)
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.cfg.cleanup()
        self.state.cleanup()

    def fetch(self, url):
        return urllib.request.urlopen(url, timeout=5).read().decode()

    def serve(self, fn, *args):
        urls = queue.Queue()
        t = threading.Thread(target=lambda: (
            fn(*args, timeout=15.0, grace=1.0, on_bound=urls.put)),
            daemon=True)
        with redirect_stdout(io.StringIO()):
            t.start()
        return t, urls.get(timeout=5).rstrip("/")


class TestHeaderNav(Fixture):
    def test_dashboard_header_links_to_projects_and_manual(self):
        out = render_html(parse_welcome(SAMPLE), derived(), token="tok")
        header = out[:out.index("</header>")]
        self.assertIn('href="/projects"', header)
        self.assertIn('href="/manual"', header)

    def test_static_dashboard_has_no_nav(self):
        out = render_html(parse_welcome(SAMPLE), derived())
        self.assertNotIn('href="/projects"', out)


class TestProjectsPage(Fixture):
    def test_gather_registered(self):
        names = [p["name"] for p in gather_registered()]
        self.assertEqual(names, ["photo-globe"])

    def test_portfolio_html_can_carry_heartbeat_and_nav(self):
        page = render_portfolio_html(
            gather_registered(), self.root, token="tok",
            title="1 registered project",
            nav=[("/", "project"), ("/manual", "manual")])
        self.assertIn('TOKEN = "tok"', page)
        self.assertIn("/api/status", page)      # heartbeat poll
        self.assertIn('href="/manual"', page)
        self.assertIn("1 registered project", page)

    def test_dashboard_serves_projects_page(self):
        t, url = self.serve(serve_dashboard, self.root,
                            parse_welcome(SAMPLE))
        page = self.fetch(url + "/projects")
        self.assertIn("photo-globe", page)
        self.assertIn("TOKEN", page)            # keeps the server alive
        t.join(timeout=6)

    def test_docs_server_serves_projects_page_and_links_it(self):
        t, url = self.serve(serve_docs, self.root)
        manual = self.fetch(url + "/")
        self.assertIn('href="/projects"', manual)
        page = self.fetch(url + "/projects")
        self.assertIn("photo-globe", page)
        t.join(timeout=6)


if __name__ == "__main__":
    unittest.main()
