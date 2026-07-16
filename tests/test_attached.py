import io
import json
import os
import queue
import re
import tempfile
import threading
import time
import traceback
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import (
    _DOCS,
    list_running,
    parse_welcome,
    render_html,
    serve_dashboard,
    start_recipe,
    stop_recipe,
)

from test_render_text import derived

BG = """\
---
name: fixture
---

## Recipes

### sleepy

- background: true

```sh
python -c "import time; time.sleep(30)"
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
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            stop_recipe(self.root, None)
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.state.cleanup()

    def quiet(self, fn, *a, **kw):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return fn(*a, **kw)


class TestAttachedStart(Fixture):
    def test_attached_start_is_tracked_and_marked(self):
        rc = self.quiet(start_recipe, self.root, self.w, "sleepy",
                        attached=True)
        self.assertEqual(rc, 0)
        running = list_running(self.root)
        self.assertEqual(len(running), 1)
        self.assertTrue(running[0]["attached"])

    def test_attached_stop_actually_kills(self):
        self.quiet(start_recipe, self.root, self.w, "sleepy", attached=True)
        pid = list_running(self.root)[0]["pid"]
        rc = self.quiet(stop_recipe, self.root, "sleepy")
        self.assertEqual(rc, 0)
        time.sleep(0.3)
        from unpark import pid_alive
        self.assertFalse(pid_alive(pid))
        self.assertEqual(list_running(self.root), [])

    def test_api_start_accepts_attached_flag(self):
        urls = queue.Queue()
        errors = []

        def serve():
            try:
                serve_dashboard(self.root, self.w, timeout=15.0, grace=1.0,
                                on_bound=urls.put)
            except BaseException:
                errors.append(traceback.format_exc())
                urls.put("")

        t = threading.Thread(target=serve, daemon=True)
        with redirect_stdout(io.StringIO()):
            t.start()
            url = urls.get(timeout=5).rstrip("/")
            if errors:
                self.fail(errors[0])
            page = urllib.request.urlopen(url + "/", timeout=5).read().decode()
            tok = re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)
            req = urllib.request.Request(
                url + "/api/start/sleepy?attached=1", method="POST",
                headers={"X-Unpark-Token": tok}, data=b"")
            body = json.loads(urllib.request.urlopen(req, timeout=8).read())
            self.assertTrue(body["ok"])
            self.assertTrue(list_running(self.root)[0]["attached"])
            t.join(timeout=6)


class TestUiAndDocs(Fixture):
    def test_start_buttons_pass_shift_state(self):
        out = render_html(self.w, derived(), token="tok")
        self.assertIn("shiftKey", out)

    def test_running_panel_marks_attached(self):
        self.quiet(start_recipe, self.root, self.w, "sleepy", attached=True)
        from unpark import render_text, _derived
        out = render_text(self.w, _derived(self.root, self.w), color=False)
        self.assertIn("attached", out)

    def test_manual_documents_both_variants_and_skill_trigger(self):
        docs = dict(_DOCS)
        dash = docs["The HTML dashboard"]
        self.assertIn("shift", dash.lower())
        self.assertIn("attached", dash)
        self.assertIn("log", dash)
        fresh = docs["Keeping it fresh (LLM upkeep)"]
        self.assertIn("When does it fire", fresh)


if __name__ == "__main__":
    unittest.main()
