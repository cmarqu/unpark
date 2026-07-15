import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import unpark
from unpark import main

from test_parser import SAMPLE


class CliFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(SAMPLE)
        (self.root / "web").mkdir()

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
        return rc, out.getvalue(), err.getvalue()


class TestBriefing(CliFixture):
    def test_version(self):
        rc, out, _ = self.cli("--version")
        self.assertEqual(rc, 0)
        self.assertIn(unpark.__version__, out)

    def test_briefing_renders(self):
        rc, out, _ = self.cli("-C", str(self.root))
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out)
        self.assertIn("RECIPES", out)

    def test_root_found_from_subdirectory(self):
        rc, out, _ = self.cli("-C", str(self.root / "web"))
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out)

    def test_no_pager_flag_keeps_noninteractive_output_direct(self):
        rc, out, _ = self.cli("--no-pager", "-C", str(self.root))
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out)

    def test_missing_welcome_md_suggests_init(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = self.cli("-C", d)
        self.assertEqual(rc, 1)
        self.assertIn("unpark init", err)


class TestInit(CliFixture):
    def test_init_creates_named_template(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self.cli("-C", d, "init")
            self.assertEqual(rc, 0)
            text = (Path(d) / "WELCOME.md").read_text()
            w = unpark.parse_welcome(text)
            self.assertEqual(w.meta["name"], Path(d).name)
            self.assertTrue(w.recipes)  # template ships an example recipe

    def test_init_refuses_to_overwrite(self):
        rc, _, err = self.cli("-C", str(self.root), "init")
        self.assertEqual(rc, 1)
        self.assertIn("already exists", err)


class TestHtml(CliFixture):
    def test_html_written_to_file(self):
        dest = self.root / "report.html"
        rc, _, _ = self.cli("-C", str(self.root), "html", "-o", str(dest))
        self.assertEqual(rc, 0)
        self.assertTrue(dest.read_text().lstrip().lower()
                        .startswith("<!doctype html"))


class TestCheck(CliFixture):
    def test_check_passes_on_good_file(self):
        rc, out, _ = self.cli("-C", str(self.root), "check")
        self.assertEqual(rc, 0)

    def test_check_fails_on_missing_recipe_dir(self):
        (self.root / "WELCOME.md").write_text(
            SAMPLE.replace("- dir: web", "- dir: gone"))
        rc, out, err = self.cli("-C", str(self.root), "check")
        self.assertEqual(rc, 1)
        self.assertIn("gone", out + err)

    def test_check_fails_on_merge_conflict_markers(self):
        (self.root / "WELCOME.md").write_text(
            SAMPLE.replace(
                "## What is this",
                "## What is this\n\n<<<<<<< HEAD\nours\n=======\n"
                "theirs\n>>>>>>> branch\n"))
        rc, out, err = self.cli("-C", str(self.root), "check")
        self.assertEqual(rc, 1)
        self.assertIn("conflict", (out + err).lower())

    def test_briefing_warns_on_merge_conflict_markers(self):
        (self.root / "WELCOME.md").write_text(
            SAMPLE + "\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> other\n")
        rc, out, err = self.cli("-C", str(self.root))
        self.assertEqual(rc, 0)  # still brief, but loudly
        self.assertIn("conflict", (out + err).lower())

    def test_check_fails_on_unknown_needs(self):
        (self.root / "WELCOME.md").write_text(
            SAMPLE.replace("- dir: web", "- needs: nonexistent"))
        rc, out, err = self.cli("-C", str(self.root), "check")
        self.assertEqual(rc, 1)
        self.assertIn("nonexistent", out + err)

    def test_strict_check_fails_on_structure_warning(self):
        text = (self.root / "WELCOME.md").read_text()
        text = text.replace("## Recipes", "## State of things\n\n"
                            "No structured handoff yet.\n\n## Recipes")
        (self.root / "WELCOME.md").write_text(text)
        rc, out, err = self.cli("-C", str(self.root), "check", "--strict")
        self.assertEqual(rc, 1)
        self.assertIn("Known issues", out + err)


class TestDemo(CliFixture):
    def test_demo_stays_in_terminal_by_default(self):
        rc, out, err = self.cli("demo")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("photo-globe", out)
        self.assertIn("unpark demo --html", out)

    def test_demo_generates_a_real_project(self):
        with tempfile.TemporaryDirectory() as parent:
            target = Path(parent) / "photo-globe"
            rc, out, err = self.cli("demo", str(target))
            self.assertEqual(rc, 0, out + err)
            self.assertTrue((target / "WELCOME.md").is_file())
            self.assertTrue((target / "index.html").is_file())
            self.assertIn("photo-globe", out)


if __name__ == "__main__":
    unittest.main()
