import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import main

from test_parser import SAMPLE


class TestResolutionTransparency(unittest.TestCase):
    """Nested projects: the tool must say WHICH WELCOME.md it resolved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(SAMPLE.replace(
            "python3 -m http.server 5173", "echo quick"))
        (self.root / "web").mkdir()
        (self.root / "web" / "index.html").write_text("hi")
        (self.root / "sub").mkdir()  # a subdir with no WELCOME.md

    def tearDown(self):
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.state.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue() + err.getvalue()

    def test_run_names_project_when_resolved_from_elsewhere(self):
        rc, out = self.cli("-C", str(self.root / "sub"), "run", "build")
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out)          # project name
        self.assertIn(str(self.root.resolve()), out)  # and its path

    def test_run_stays_quiet_when_run_from_project_root(self):
        rc, out = self.cli("-C", str(self.root), "run", "build")
        self.assertEqual(rc, 0)
        self.assertNotIn("project:", out)

    def test_briefing_shows_project_path(self):
        rc, out = self.cli("-C", str(self.root / "sub"))
        self.assertEqual(rc, 0)
        self.assertIn(str(self.root.resolve()), out)

    def test_start_hints_when_recipe_not_background(self):
        # 'build' has no `background: true`; starting it should still work
        # but explain why it exited and how to declare a long-running demo
        rc, out = self.cli("-C", str(self.root), "start", "build")
        self.assertEqual(rc, 0)
        self.assertIn("background: true", out)
        self.assertIn("unpark run build", out)


if __name__ == "__main__":
    unittest.main()
