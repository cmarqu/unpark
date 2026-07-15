import tempfile
import unittest
from pathlib import Path

from unpark import _docs_context, parse_welcome, render_docs_html, render_html

from test_parser import SAMPLE
from test_render_text import derived


class TestCopyInstructions(unittest.TestCase):
    def test_dashboard_embeds_instructions_and_copy_button(self):
        out = render_html(parse_welcome(SAMPLE), derived(), token="tok")
        self.assertIn("copyInstr", out)               # the handler
        self.assertIn('data-setup="copy"', out)       # the button
        # the payload itself rides along (JSON-embedded skill text)
        self.assertIn("Keeping it fresh", out)
        self.assertIn("Set up unpark for this project", out)

    def test_manual_has_copy_button_too(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "WELCOME.md").write_text(SAMPLE)
            out = render_docs_html(_docs_context(d), token="tok")
        self.assertIn("copyInstr", out)
        self.assertIn('data-setup="copy"', out)

    def test_static_pages_have_neither(self):
        out = render_html(parse_welcome(SAMPLE), derived())
        self.assertNotIn("copyInstr", out)


if __name__ == "__main__":
    unittest.main()
