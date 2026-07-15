import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import find_child_projects, find_project, main

from test_parser import SAMPLE

RECIPE_TOUCH = """\
---
name: docs-proj
tagline: Briefing lives in docs/
---

## Recipes

### touch

- dir: web

```sh
echo here > marker.txt
```
"""


class LocFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name)

    def tearDown(self):
        for var in ("UNPARK_STATE_DIR", "UNPARK_FILE"):
            os.environ.pop(var, None)
        self.tmp.cleanup()
        self.state.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue() + err.getvalue()


class TestDocsLocation(LocFixture):
    def setUp(self):
        super().setUp()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "WELCOME.md").write_text(RECIPE_TOUCH)
        (self.root / "web").mkdir()

    def test_find_project_resolves_docs_file_to_repo_root(self):
        root, file = find_project(self.root)
        self.assertEqual(root, self.root.resolve())
        self.assertEqual(file, (self.root / "docs" / "WELCOME.md").resolve())

    def test_briefing_works(self):
        rc, out = self.cli("-C", str(self.root))
        self.assertEqual(rc, 0)
        self.assertIn("docs-proj", out)

    def test_recipe_dirs_stay_repo_relative(self):
        rc, _ = self.cli("-C", str(self.root), "run", "touch")
        self.assertEqual(rc, 0)
        self.assertTrue((self.root / "web" / "marker.txt").exists())

    def test_top_level_file_wins_over_docs(self):
        (self.root / "WELCOME.md").write_text(
            SAMPLE.replace("name: photo-globe", "name: top-level"))
        root, file = find_project(self.root)
        self.assertEqual(file.name, "WELCOME.md")
        self.assertEqual(file.parent, self.root.resolve())

    def test_child_projects_detect_docs_variant(self):
        hub = self.root / "hub"
        proj = hub / "docs-child"
        (proj / "docs").mkdir(parents=True)
        (proj / "docs" / "WELCOME.md").write_text(RECIPE_TOUCH)
        kids = find_child_projects(hub)
        self.assertEqual([k.name for k in kids], ["docs-child"])


class TestExplicitFile(LocFixture):
    def setUp(self):
        super().setUp()
        (self.root / "notes").mkdir()
        self.file = self.root / "notes" / "reentry.md"
        self.file.write_text(SAMPLE.replace("name: photo-globe",
                                            "name: odd-location"))
        (self.root / "web").mkdir()

    def test_file_flag(self):
        rc, out = self.cli("-C", str(self.root),
                           "--file", "notes/reentry.md")
        self.assertEqual(rc, 0)
        self.assertIn("odd-location", out)

    def test_welcome_file_env(self):
        os.environ["UNPARK_FILE"] = "notes/reentry.md"
        rc, out = self.cli("-C", str(self.root))
        self.assertEqual(rc, 0)
        self.assertIn("odd-location", out)

    def test_missing_explicit_file_errors(self):
        rc, out = self.cli("-C", str(self.root), "--file", "gone.md")
        self.assertEqual(rc, 1)
        self.assertIn("gone.md", out)


class TestGlobalSkill(LocFixture):
    def test_install_global_writes_home_skill_only(self):
        fake_home = self.root / "home"
        fake_home.mkdir()
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(fake_home)
        try:
            proj = self.root / "proj"
            proj.mkdir()
            (proj / "WELCOME.md").write_text(SAMPLE)
            rc, out = self.cli("-C", str(proj), "skill", "--install",
                               "--global")
            self.assertEqual(rc, 0)
            skill = (fake_home / ".claude" / "skills" / "unpark-upkeep"
                     / "SKILL.md")
            self.assertTrue(skill.exists())
            self.assertIn("name: unpark-upkeep", skill.read_text())
            # a global install must not touch the repo
            self.assertFalse((proj / "AGENTS.md").exists())
            self.assertFalse((proj / ".claude").exists())
        finally:
            if old is not None:
                os.environ["HOME"] = old


if __name__ == "__main__":
    unittest.main()
