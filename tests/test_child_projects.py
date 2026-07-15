import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import find_child_projects, main

from test_parser import SAMPLE


class ChildFixture(unittest.TestCase):
    """examples/-style layout: parent project above, subprojects below."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        self.parent = Path(self.tmp.name)
        (self.parent / "WELCOME.md").write_text(
            SAMPLE.replace("name: photo-globe", "name: parent-repo"))
        (self.parent / "web").mkdir()
        self.examples = self.parent / "examples"
        self.examples.mkdir()
        for name in ("photo-globe", "other-demo"):
            d = self.examples / name
            d.mkdir()
            (d / "WELCOME.md").write_text(
                SAMPLE.replace("name: photo-globe", f"name: {name}"))

    def tearDown(self):
        os.environ.pop("UNPARK_STATE_DIR", None)
        os.environ.pop("UNPARK_CONFIG_DIR", None)
        self.tmp.cleanup()
        self.state.cleanup()
        self.cfg.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue() + err.getvalue()


class TestFindChildProjects(ChildFixture):
    def test_finds_immediate_children_with_welcome_md(self):
        names = [p.name for p in find_child_projects(self.examples)]
        self.assertEqual(sorted(names), ["other-demo", "photo-globe"])

    def test_empty_when_none(self):
        self.assertEqual(find_child_projects(self.parent / "web"), [])


class TestChildProjectHints(ChildFixture):
    def test_briefing_from_between_mentions_children(self):
        rc, out = self.cli("-C", str(self.examples))
        self.assertEqual(rc, 0)
        self.assertIn("parent-repo", out)      # still resolves upward
        self.assertIn("photo-globe", out)      # but names what's below you
        self.assertIn("other-demo", out)
        self.assertIn("-C", out)               # and how to target them

    def test_no_welcome_anywhere_up_shows_portfolio(self):
        # strip the parent's WELCOME.md: nothing resolves upward, so the
        # children become a portfolio overview (rc 0), not an error
        (self.parent / "WELCOME.md").unlink()
        rc, out = self.cli("-C", str(self.examples))
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out)
        self.assertIn("other-demo", out)
        self.assertIn("2 projects", out)

    def test_no_hint_when_standing_in_a_real_project(self):
        rc, out = self.cli("-C", str(self.examples / "photo-globe"))
        self.assertEqual(rc, 0)
        self.assertNotIn("other-demo", out)


if __name__ == "__main__":
    unittest.main()
