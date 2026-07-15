import os
import tempfile
import unittest
from pathlib import Path

from unpark import _UPKEEP_TEXT, _docs_context, parse_welcome, render_docs_html, render_html

from test_parser import SAMPLE
from test_render_text import derived


class TestScopeClarity(unittest.TestCase):
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

    def install_global(self):
        d = Path(self.home.name) / ".claude" / "skills" / "unpark-upkeep"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x")

    def note_content(self, out):
        import re
        m = re.search(r'id="st-local-note">([^<]*)<', out)
        assert m, "note element missing"
        return m.group(1).strip()

    def test_manual_marks_local_as_redundant_when_global_present(self):
        self.install_global()
        out = render_docs_html(_docs_context(self.root), token="tok")
        note = self.note_content(out)
        self.assertIn("covered by your global install", note)
        self.assertIn("teammates", note)

    def test_no_redundancy_note_without_global(self):
        out = render_docs_html(_docs_context(self.root), token="tok")
        self.assertEqual(self.note_content(out), "")

    def test_dashboard_js_updates_the_note_live(self):
        out = render_html(parse_welcome(SAMPLE), derived(), token="tok")
        self.assertIn("su-local-note", out)
        self.assertIn("covered by your global install", out)  # in the JS

    def test_skill_text_scopes_the_local_install(self):
        self.assertIn("global skill is installed", _UPKEEP_TEXT)


if __name__ == "__main__":
    unittest.main()
