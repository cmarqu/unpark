import json
import tempfile
import unittest
from pathlib import Path

from unpark import _DOCS, detect_task_runners, parse_welcome, render_html, render_text

from test_parser import SAMPLE
from test_render_text import derived


class TestDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_justfile_and_npm_scripts_with_count(self):
        (self.root / "justfile").write_text("demo:\n\techo hi\n")
        (self.root / "package.json").write_text(json.dumps(
            {"scripts": {"dev": "vite", "build": "vite build"}}))
        runners = detect_task_runners(self.root)
        names = [r["name"] for r in runners]
        self.assertIn("just", names)
        self.assertIn("npm scripts", names)
        npm = [r for r in runners if r["name"] == "npm scripts"][0]
        self.assertEqual(npm["count"], 2)
        self.assertEqual(npm["hint"], "npm run")

    def test_package_json_without_scripts_is_not_a_runner(self):
        (self.root / "package.json").write_text(json.dumps({"name": "x"}))
        self.assertEqual(detect_task_runners(self.root), [])

    def test_empty_project_detects_nothing(self):
        self.assertEqual(detect_task_runners(self.root), [])

    def test_taskfile_make_mask_mise(self):
        for f in ("Taskfile.yml", "Makefile", "maskfile.md", "mise.toml"):
            (self.root / f).write_text("x\n")
        names = [r["name"] for r in detect_task_runners(self.root)]
        for n in ("task", "make", "mask", "mise"):
            self.assertIn(n, names)


class TestDisplay(unittest.TestCase):
    def test_briefing_acknowledges_runners(self):
        w = parse_welcome(SAMPLE)
        d = derived(runners=[{"name": "just", "file": "justfile",
                              "hint": "just --list", "count": None}])
        out = render_text(w, d, color=False)
        self.assertIn("TASK RUNNER", out)
        self.assertIn("just --list", out)
        self.assertIn("delegate", out)          # the doctrine, visible

    def test_briefing_silent_without_runners(self):
        w = parse_welcome(SAMPLE)
        out = render_text(w, derived(runners=[]), color=False)
        self.assertNotIn("TASK RUNNER", out)

    def test_dashboard_shows_runner_line(self):
        w = parse_welcome(SAMPLE)
        d = derived(runners=[{"name": "npm scripts", "file": "package.json",
                              "hint": "npm run", "count": 7}])
        out = render_html(w, d, token="tok")
        self.assertIn("npm run", out)
        self.assertIn("(7)", out)

    def test_manual_acknowledges_complementary_tools(self):
        body = dict(_DOCS).get("Task runners — friends, not rivals")
        self.assertIsNotNone(body)
        for needle in ("just", "Taskfile", "npm", "complementary",
                       "delegate", "re-entry"):
            self.assertIn(needle, body)


if __name__ == "__main__":
    unittest.main()
