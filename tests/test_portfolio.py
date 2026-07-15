import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from unpark import (gather_portfolio, main, pick_registered_project,
                    registry_add, render_portfolio_html,
                    render_portfolio_text)

FMT = """\
---
name: {name}
tagline: {tagline}
status: {status}
updated: {updated}
---

## Goals

- [x] first
- [ ] second

## Recipes

### demo

- background: true

```sh
sleep 30
```
"""


def git(cwd, *args, date=None):
    env = dict(os.environ,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   capture_output=True)


class PortfolioFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        self.hub = Path(self.tmp.name)

        # fresh, active project with git history
        a = self.hub / "alpha"
        a.mkdir()
        (a / "WELCOME.md").write_text(FMT.format(
            name="alpha", tagline="The fresh one", status="active",
            updated="2026-07-10"))
        git(a, "init", "-q", "-b", "main")
        git(a, "add", ".")
        git(a, "commit", "-qm", "recent work", date="2026-07-10T10:00:00")

        # parked project whose briefing lags behind its commits
        b = self.hub / "beta"
        b.mkdir()
        (b / "WELCOME.md").write_text(FMT.format(
            name="beta", tagline="The stale one", status="parked",
            updated="2026-01-01"))
        git(b, "init", "-q", "-b", "main")
        git(b, "add", ".")
        git(b, "commit", "-qm", "old base", date="2026-01-01T10:00:00")
        git(b, "commit", "-qm", "sneaky fix", "--allow-empty",
            date="2026-03-05T10:00:00")

        # a plain directory: not a project, must be ignored
        (self.hub / "downloads").mkdir()

    def tearDown(self):
        os.environ.pop("UNPARK_STATE_DIR", None)
        os.environ.pop("UNPARK_CONFIG_DIR", None)
        self.tmp.cleanup()
        self.state.cleanup()
        self.cfg.cleanup()


class TestGather(PortfolioFixture):
    def test_collects_only_projects_with_facts(self):
        ps = gather_portfolio(self.hub)
        self.assertEqual([p["name"] for p in ps], ["alpha", "beta"])
        alpha, beta = ps
        self.assertEqual(alpha["status"], "active")
        self.assertEqual(alpha["goals"], (1, 2))
        self.assertEqual(alpha["last_date"], "2026-07-10")
        self.assertEqual(alpha["stale_count"], 0)
        self.assertEqual(beta["stale_count"], 1)

    def test_sorted_alphabetically(self):
        # give beta the NEWEST commit: names must still order the overview
        git(self.hub / "beta", "commit", "-qm", "newest", "--allow-empty",
            date="2026-07-11T09:00:00")
        ps = gather_portfolio(self.hub)
        self.assertEqual([p["name"] for p in ps], ["alpha", "beta"])

    def test_sidebar_appears_only_for_many_projects(self):
        few = gather_portfolio(self.hub)
        page = render_portfolio_html(few, self.hub, today="2026-07-11")
        self.assertNotIn('class="sidebar"', page)

        many = few * 4  # 8 entries
        page = render_portfolio_html(many, self.hub, today="2026-07-11")
        self.assertIn('class="sidebar"', page)
        self.assertIn('href="#p-alpha"', page)
        self.assertIn('id="p-alpha"', page)


class TestRenderPortfolio(PortfolioFixture):
    def test_text_table(self):
        out = render_portfolio_text(gather_portfolio(self.hub),
                                    self.hub, today="2026-07-11",
                                    color=False)
        self.assertIn("2 projects", out)
        single = render_portfolio_text(gather_portfolio(self.hub)[:1],
                                       self.hub, today="2026-07-11",
                                       color=False)
        self.assertIn("1 project", single)
        self.assertNotIn("1 projects", single)  # grammar guard
        self.assertIn("alpha", out)
        self.assertIn("The fresh one", out)
        self.assertIn("parked", out)
        self.assertIn("stale", out)      # beta flagged
        self.assertIn("1/2", out)        # goal progress
        self.assertIn("\n  alpha", out)  # stable identity row
        self.assertIn("\n      The fresh one", out)  # separate detail row
        self.assertNotIn("downloads", out)

    def test_html_page(self):
        out = render_portfolio_html(gather_portfolio(self.hub),
                                    self.hub, today="2026-07-11")
        self.assertTrue(out.lstrip().lower().startswith("<!doctype html"))
        self.assertIn("alpha", out)
        self.assertIn("The stale one", out)
        self.assertIn("prefers-color-scheme", out)


class TestPortfolioCli(PortfolioFixture):
    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_briefing_falls_back_to_portfolio(self):
        rc, out, _ = self.cli("-C", str(self.hub))
        self.assertEqual(rc, 0)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_empty_hub_still_suggests_init(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = self.cli("-C", d)
        self.assertEqual(rc, 1)
        self.assertIn("unpark init", err)

    def _register_examples(self):
        registry_add(self.hub / "alpha")
        registry_add(self.hub / "beta")

    def test_projects_uses_the_shared_pager_policy(self):
        self._register_examples()
        with patch("unpark.app.display_text") as display:
            rc, _, _ = self.cli("projects")
        self.assertEqual(rc, 0)
        self.assertIn("alpha", display.call_args.args[0])
        self.assertIn("beta", display.call_args.args[0])

    def test_projects_name_opens_that_briefing(self):
        self._register_examples()
        rc, out, _ = self.cli("--no-pager", "project", "beta")
        self.assertEqual(rc, 0)
        self.assertIn("The stale one", out)

    def test_duplicate_project_name_lists_paths_instead_of_guessing(self):
        text = (self.hub / "beta" / "WELCOME.md").read_text()
        (self.hub / "beta" / "WELCOME.md").write_text(
            text.replace("name: beta", "name: alpha"))
        self._register_examples()
        rc, _, err = self.cli("project", "alpha")
        self.assertEqual(rc, 1)
        self.assertIn("multiple registered projects", err)
        self.assertIn(str(self.hub / "alpha"), err)
        self.assertIn(str(self.hub / "beta"), err)

    def test_picker_selects_before_the_briefing(self):
        projects = gather_portfolio(self.hub)
        output = io.StringIO()
        with redirect_stdout(output):
            selected = pick_registered_project(projects, ask=lambda _: "2")
        self.assertEqual(selected["name"], "beta")
        self.assertIn("  1. alpha", output.getvalue())
        self.assertIn("  2. beta", output.getvalue())
        self.assertNotIn("The fresh one", output.getvalue())


if __name__ == "__main__":
    unittest.main()
