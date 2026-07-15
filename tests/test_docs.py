import io
import json
import os
import queue
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import _docs_context, main, render_docs_html, serve_docs

from test_parser import SAMPLE


class TestHelpText(unittest.TestCase):
    def help_of(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            main([*args, "--help"])
        return out.getvalue()

    def test_top_level_help_mentions_the_important_things(self):
        text = self.help_of()
        self.assertNotIn("--html", text)     # flag removed: unpark manual is the door
        self.assertIn("unpark manual", text)
        self.assertIn("skill --install", text)
        self.assertIn("--global", text)
        self.assertIn("welcome back", text)

    def test_help_subcommand_prints_help(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["help"])
        self.assertEqual(rc, 0)
        self.assertIn("unpark manual", out.getvalue())
        self.assertIn("examples:", out.getvalue())

    def test_skill_help_explains_scopes(self):
        text = self.help_of("skill")
        self.assertIn("--global", text)
        self.assertIn(".claude/skills", text)

    def test_demo_help_explains_its_file_lifecycle(self):
        text = self.help_of("demo")
        self.assertIn("temporary and deleted", text)
        self.assertIn("dashboard closes", text)
        self.assertIn("DESTINATION", text)


class DocsFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(SAMPLE)

    def tearDown(self):
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        self.tmp.cleanup()
        self.home.cleanup()


class TestDocsContext(DocsFixture):
    def test_context_inside_project(self):
        ctx = _docs_context(self.root)
        self.assertEqual(ctx["root"], self.root.resolve())
        self.assertEqual(ctx["name"], "photo-globe")
        self.assertFalse(ctx["global_installed"])
        self.assertFalse(ctx["local_installed"])

    def test_context_outside_project(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = _docs_context(d)
        self.assertIsNone(ctx["root"])

    def test_context_sees_installed_skills(self):
        skill = Path(self.home.name) / ".claude" / "skills" / "unpark-upkeep"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("x")
        ctx = _docs_context(self.root)
        self.assertTrue(ctx["global_installed"])


class TestRenderDocs(DocsFixture):
    def test_manual_covers_the_essentials(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        for needle in ("WELCOME.md format", "front-matter", "background",
                       "needs", "portfolio", "UNPARK_FILE",
                       "UNPARK_STATE_DIR", "docs/WELCOME.md",
                       "unpark skill"):
            self.assertIn(needle, out)

    def test_page_states_where_you_are(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertIn(str(self.root.resolve()), out)   # the project path
        self.assertIn("photo-globe", out)
        self.assertIn(".claude/skills/unpark-upkeep", out)  # target paths

    def test_install_buttons_scopes(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertIn('data-scope="local"', out)
        self.assertIn('data-scope="global"', out)

    def test_local_button_disabled_outside_project(self):
        with tempfile.TemporaryDirectory() as d:
            out = render_docs_html(_docs_context(d), token="tok")
        self.assertIn("no project here", out)
        self.assertRegex(out, r'data-scope="local"[^>]*disabled')

    def test_manual_has_toc_with_section_anchors(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertIn('<nav class="toc"', out)
        for slug in ("you-are-here", "quick-start", "commands",
                     "the-welcome-md-format", "managed-processes",
                     "environment-variables"):
            self.assertIn('href="#%s"' % slug, out)
            self.assertIn('id="%s"' % slug, out)

    def test_manual_renders_commands_as_definition_rows(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertIn('<ul class="def">', out)
        self.assertRegex(out, r'<span class="k"><code>unpark init'
                              r'</code></span><span class="v">')

    def test_manual_is_self_contained_and_theme_aware(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertTrue(out.startswith("<!doctype html"))
        self.assertIn("<title>unpark — manual</title>", out)
        self.assertIn("prefers-color-scheme", out)
        self.assertIn("color-scheme: light dark", out)
        self.assertNotRegex(out, r'(src|href)="https?://')

    def test_skill_rows_show_install_state_badges(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertRegex(out, r'class="badge"[^>]*id="st-local"')
        skill = Path(self.home.name) / ".claude" / "skills" / "unpark-upkeep"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("x")
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertRegex(out, r'class="badge on"[^>]*id="st-global"')


class TestServeDocs(DocsFixture):
    def serve(self, grace=1.2):
        urls = queue.Queue()
        result = {}

        def run():
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result["opened"] = serve_docs(self.root, timeout=15.0,
                                              grace=grace,
                                              on_bound=urls.put)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t, urls.get(timeout=5).rstrip("/"), result

    def test_install_buttons_work_and_heartbeat_closes(self):
        t, url, result = self.serve()
        page = urllib.request.urlopen(url + "/", timeout=5).read().decode()
        tok = re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)
        hdr = {"X-Unpark-Token": tok}

        def post(path):
            req = urllib.request.Request(url + path, method="POST",
                                         headers=hdr, data=b"")
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode())

        self.assertTrue(post("/api/skill/global")["ok"])
        self.assertTrue((Path(self.home.name) / ".claude" / "skills"
                         / "unpark-upkeep" / "SKILL.md").exists())

        self.assertTrue(post("/api/skill/local")["ok"])
        self.assertTrue((self.root / "AGENTS.md").exists())
        self.assertTrue((self.root / ".claude" / "skills" / "unpark-upkeep"
                         / "SKILL.md").exists())

        # status reflects the installs (and acts as heartbeat)
        req = urllib.request.Request(url + "/api/status", headers=hdr)
        status = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertTrue(status["global_installed"])
        self.assertTrue(status["local_installed"])

        t.join(timeout=6)  # heartbeat silence → exit
        self.assertFalse(t.is_alive())
        self.assertTrue(result["opened"])

    def test_post_without_token_rejected(self):
        t, url, _ = self.serve(grace=0.8)
        urllib.request.urlopen(url + "/", timeout=5).read()
        req = urllib.request.Request(url + "/api/skill/global",
                                     method="POST", data=b"")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)
        t.join(timeout=6)


if __name__ == "__main__":
    unittest.main()
