import re
import unittest

from unpark import parse_welcome, render_html

from test_parser import SAMPLE
from test_render_text import derived


class TestRenderHtml(unittest.TestCase):
    def setUp(self):
        self.w = parse_welcome(SAMPLE)

    def test_is_complete_html_document(self):
        out = render_html(self.w, derived())
        self.assertTrue(out.lstrip().lower().startswith("<!doctype html"))
        self.assertIn("</html>", out)
        self.assertIn("<title>photo-globe — unpark</title>", out)

    def test_contains_header_and_status(self):
        out = render_html(self.w, derived())
        self.assertIn("photo-globe", out)
        self.assertIn("Photos on a spinning 3D globe", out)
        self.assertIn("parked", out)

    def test_stale_banner_toggles(self):
        self.assertIn("may be stale", render_html(self.w, derived()))
        self.assertNotIn("may be stale",
                         render_html(self.w, derived(stale_count=0)))

    def test_goals_progress_and_items(self):
        out = render_html(self.w, derived())
        self.assertIn("1 / 2 done", out)
        self.assertIn("Cluster markers", out)

    def test_recipes_with_commands(self):
        out = render_html(self.w, derived())
        self.assertIn("unpark start demo", out)
        self.assertIn("unpark run build", out)
        self.assertIn("http://localhost:5173", out)

    def test_html_in_content_is_escaped(self):
        w = parse_welcome(
            "---\nname: x<script>\n---\n\n## What is this\n\n<script>alert(1)</script>\n"
        )
        out = render_html(w, derived(git=None, stale_count=0))
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_links_open_in_new_tab(self):
        # the page may be served ephemerally: no history to go back to,
        # so every link must open a fresh tab
        out = render_html(self.w, derived())
        for link in re.findall(r"<a\s[^>]*>", out):
            self.assertIn('target="_blank"', link)
            self.assertIn('rel="noopener"', link)

    def test_buttons_follow_the_theme(self):
        # without these, form controls render in the UA's light style
        # even when the rest of the page is dark
        out = render_html(self.w, derived(), token="tok")
        self.assertIn("color-scheme", out)
        self.assertRegex(out, r"button\s*\{[^}]*var\(--")

    def test_self_contained_and_theme_aware(self):
        out = render_html(self.w, derived())
        self.assertIn("prefers-color-scheme", out)
        self.assertNotIn("http://cdn", out)
        self.assertNotIn("<link", out)


if __name__ == "__main__":
    unittest.main()
