import io
import os
import queue
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import main, parse_welcome, serve_dashboard, start_recipe

BG = """\
---
name: fixture
---

## Recipes

### chatty

- background: true

```sh
echo hello-from-the-log
sleep 30
```
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(BG)
        self.w = parse_welcome(BG)

    def tearDown(self):
        from unpark import stop_recipe
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            stop_recipe(self.root, None)
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.state.cleanup()

    def serve(self):
        urls = queue.Queue()
        t = threading.Thread(target=lambda: serve_dashboard(
            self.root, self.w, timeout=15.0, grace=1.0,
            on_bound=urls.put), daemon=True)
        with redirect_stdout(io.StringIO()):
            t.start()
        return t, urls.get(timeout=5).rstrip("/")

    def quiet_start(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            start_recipe(self.root, self.w, "chatty")
        time.sleep(0.4)


class TestLogsPage(Fixture):
    def test_serves_log_tail_with_path(self):
        self.quiet_start()
        t, url = self.serve()
        resp = urllib.request.urlopen(url + "/logs/chatty", timeout=5)
        self.assertIn("text/plain", resp.headers["Content-Type"])
        body = resp.read().decode()
        self.assertIn("hello-from-the-log", body)
        self.assertIn(self.state.name, body)     # names the log's path
        t.join(timeout=6)

    def test_unknown_log_is_404(self):
        t, url = self.serve()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(url + "/logs/nope", timeout=5)
        self.assertEqual(cm.exception.code, 404)
        t.join(timeout=6)

    def test_running_panel_links_logs(self):
        from unpark import render_html
        from test_render_text import derived
        out = render_html(self.w, derived(), token="tok")
        self.assertIn("/logs/", out)              # JS builds the link
        self.assertIn("state directory", dict(__import__("unpark")._DOCS)
                      ["The HTML dashboard"])     # manual names location

    def test_tooltip_points_at_the_logs_link(self):
        from unpark import render_html
        from test_render_text import derived
        out = render_html(self.w, derived(), token="tok")
        m = [ln for ln in out.splitlines() if 'title="click' in ln]
        self.assertTrue(m)
        self.assertIn("log", m[0])

    def test_ps_prints_log_path(self):
        self.quiet_start()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            main(["-C", str(self.root), "ps"])
        self.assertIn(".log", out.getvalue())


if __name__ == "__main__":
    unittest.main()
