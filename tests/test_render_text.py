import unittest

from unpark import days_ago, parse_welcome, render_text

from test_parser import SAMPLE


def derived(**over):
    d = {
        "git": {"branch": "main", "last_date": "2026-07-03",
                "last_subject": "fix marker jitter", "dirty": 1},
        "stale_count": 2,
        "stale_latest": "fix marker jitter",
        "running": [],
        "today": "2026-07-11",
    }
    d.update(over)
    return d


class TestDaysAgo(unittest.TestCase):
    def test_days(self):
        self.assertEqual(days_ago("2026-07-03", "2026-07-11"), "8 days ago")

    def test_today_and_yesterday(self):
        self.assertEqual(days_ago("2026-07-11", "2026-07-11"), "today")
        self.assertEqual(days_ago("2026-07-10", "2026-07-11"), "yesterday")

    def test_bad_date(self):
        self.assertEqual(days_ago("", "2026-07-11"), "?")


class TestRenderText(unittest.TestCase):
    def setUp(self):
        self.w = parse_welcome(SAMPLE)

    def test_header_has_name_tagline_status(self):
        out = render_text(self.w, derived(), color=False)
        self.assertIn("photo-globe", out)
        self.assertIn("Photos on a spinning 3D globe", out)
        self.assertIn("parked", out)

    def test_staleness_warning_when_stale(self):
        out = render_text(self.w, derived(), color=False)
        self.assertIn("may be stale: 2 commits", out)
        self.assertIn("fix marker jitter", out)

    def test_no_staleness_warning_when_fresh(self):
        out = render_text(self.w, derived(stale_count=0), color=False)
        self.assertNotIn("stale", out)

    def test_goals_progress(self):
        out = render_text(self.w, derived(), color=False)
        self.assertIn("GOALS  1/2 done", out)

    def test_recipes_listed_with_hints(self):
        out = render_text(self.w, derived(), color=False)
        self.assertIn("demo", out)
        self.assertIn("Serves the globe with sample data.", out)
        self.assertIn("background", out)
        self.assertIn("http://localhost:5173", out)

    def test_git_line(self):
        out = render_text(self.w, derived(), color=False)
        self.assertIn("branch main", out)
        self.assertIn("1 uncommitted", out)

    def test_running_empty_hint(self):
        out = render_text(self.w, derived(), color=False)
        self.assertIn("unpark start demo", out)

    def test_running_entries(self):
        run = [{"recipe": "demo", "pid": 4242, "url": "http://localhost:5173"}]
        out = render_text(self.w, derived(running=run), color=False)
        self.assertIn("demo", out)
        self.assertIn("4242", out)

    def test_no_ansi_when_color_off(self):
        out = render_text(self.w, derived(), color=False)
        self.assertNotIn("\x1b[", out)

    def test_ansi_when_color_on(self):
        out = render_text(self.w, derived(), color=True)
        self.assertIn("\x1b[", out)

    def test_no_git_repo_handled(self):
        out = render_text(self.w, derived(git=None, stale_count=0), color=False)
        self.assertIn("not a git repository", out)


if __name__ == "__main__":
    unittest.main()
