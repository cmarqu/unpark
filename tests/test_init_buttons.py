import io
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import unpark as W
from unpark import _docs_context, main, render_docs_html, serve_docs

from test_parser import SAMPLE


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)

    def tearDown(self):
        os.environ.pop("UNPARK_CONFIG_DIR", None)
        self.tmp.cleanup()
        self.cfg.cleanup()


class TestContextAndRender(Fixture):
    def test_context_flags_repo_root(self):
        self.assertTrue(_docs_context(self.root)["is_repo_root"])
        sub = self.root / "sub"
        sub.mkdir()
        self.assertFalse(_docs_context(sub)["is_repo_root"])

    def test_init_buttons_only_in_briefingless_repo_root(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertIn('data-init="top"', out)
        self.assertIn('data-init="docs"', out)

        # with a briefing: no init buttons
        (self.root / "WELCOME.md").write_text(SAMPLE)
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertNotIn('data-init=', out)

    def test_no_buttons_outside_a_repo(self):
        with tempfile.TemporaryDirectory() as d:
            out = render_docs_html(_docs_context(d), token="tok")
        self.assertNotIn('data-init=', out)

    def with_home(self, home):
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            return render_docs_html(_docs_context(self.root), token="tok")
        finally:
            if old is not None:
                os.environ["HOME"] = old

    def test_tip_is_copyable_and_reassures_when_skill_installed(self):
        with tempfile.TemporaryDirectory() as home:
            skill = Path(home) / ".claude" / "skills" / "unpark-upkeep"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("x")
            out = self.with_home(home)
        self.assertIn('data-copy="phrase"', out)          # copy button
        self.assertIn("Set up unpark for this project", out)
        self.assertIn("already", out)                     # reassurance
        self.assertIn("installed globally", out)

    def test_tip_warns_when_no_skill_anywhere(self):
        with tempfile.TemporaryDirectory() as home:
            out = self.with_home(home)
        self.assertIn("not installed yet", out)
        self.assertIn("install globally", out)            # points at button

    def test_tip_survives_creation_while_briefing_is_a_skeleton(self):
        # after the init button ran: a template skeleton exists — the
        # fill-it-with-your-LLM guidance must still be on the page
        with redirect_stdout(io.StringIO()):
            rc = main(["-C", str(self.root), "init"])
        self.assertEqual(rc, 0)
        ctx = _docs_context(self.root)
        self.assertTrue(ctx["is_skeleton"])
        out = render_docs_html(ctx, token="tok")
        self.assertIn("Set up unpark for this project", out)
        self.assertIn('data-copy="phrase"', out)

    def test_tip_gone_once_briefing_has_real_content(self):
        (self.root / "WELCOME.md").write_text(SAMPLE)
        ctx = _docs_context(self.root)
        self.assertFalse(ctx["is_skeleton"])
        out = render_docs_html(ctx, token="tok")
        self.assertNotIn('data-copy="phrase"', out)

    def test_dashboard_shows_the_tip_for_skeletons_too(self):
        from unpark import parse_welcome, render_html, _TEMPLATE, _derived
        with redirect_stdout(io.StringIO()):
            main(["-C", str(self.root), "init"])
        w = parse_welcome((self.root / "WELCOME.md").read_text())
        out = render_html(w, _derived(self.root, w), token="tok")
        self.assertIn("Set up unpark for this project", out)
        self.assertIn('data-copy="phrase"', out)
        # prominence: the tip must come BEFORE any content section
        self.assertLess(out.index("Set up unpark for this project"),
                        out.index("<section>"))


class TestInitApi(Fixture):
    def serve(self):
        urls = queue.Queue()
        t = threading.Thread(target=lambda: serve_docs(
            self.root, timeout=15.0, grace=1.0, on_bound=urls.put),
            daemon=True)
        with redirect_stdout(io.StringIO()):
            t.start()
        return t, urls.get(timeout=5).rstrip("/")

    def post(self, url, path, tok):
        req = urllib.request.Request(url + path, method="POST",
                                     headers={"X-Unpark-Token": tok},
                                     data=b"")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())

    def test_init_docs_variant_and_double_init_refused(self):
        t, url = self.serve()
        page = urllib.request.urlopen(url + "/", timeout=5).read().decode()
        tok = re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)

        body = self.post(url, "/api/init?loc=docs", tok)
        self.assertTrue(body["ok"])
        self.assertTrue((self.root / "docs" / "WELCOME.md").is_file())

        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post(url, "/api/init?loc=top", tok)
        self.assertEqual(cm.exception.code, 400)
        t.join(timeout=6)


class TestHtmlRouting(Fixture):
    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue() + err.getvalue()

    def test_html_in_briefingless_repo_serves_docs(self):
        calls = {}
        real = W.serve_docs
        W.serve_docs = lambda *a, **kw: calls.setdefault("docs", True)
        try:
            rc, _ = self.cli("-C", str(self.root), "html")
        finally:
            W.serve_docs = real
        self.assertEqual(rc, 0)
        self.assertIn("docs", calls)


if __name__ == "__main__":
    unittest.main()
