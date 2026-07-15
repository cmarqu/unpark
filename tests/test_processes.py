import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import (
    list_running,
    parse_welcome,
    pid_alive,
    start_recipe,
    state_dir,
    stop_recipe,
)

BG = """\
---
name: fixture
---

## Recipes

### sleepy

- background: true
- url: http://localhost:9999

```sh
python -c "print('started')"
python -c "import time; time.sleep(30)"
```

### fg-only

```sh
python -c "print('not a background recipe')"
```

### dies

- background: true

```sh
python -c "print('boom: port already in use'); raise SystemExit(7)"
```
"""


class ProcFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ["UNPARK_STATE_DIR"] = self.state.name
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


class TestStateDir(ProcFixture):
    def test_state_dir_respects_env_and_is_per_project(self):
        d1 = state_dir(self.root)
        self.assertTrue(str(d1).startswith(self.state.name))
        with tempfile.TemporaryDirectory() as other:
            self.assertNotEqual(d1, state_dir(Path(other)))


class TestStartStop(ProcFixture):
    def test_start_lists_running_with_live_pid(self):
        rc = self.quiet(start_recipe, self.root, self.w, "sleepy")
        self.assertEqual(rc, 0)
        running = list_running(self.root)
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0]["recipe"], "sleepy")
        self.assertEqual(running[0]["url"], "http://localhost:9999")
        self.assertTrue(pid_alive(running[0]["pid"]))

    def test_double_start_is_refused(self):
        self.quiet(start_recipe, self.root, self.w, "sleepy")
        rc = self.quiet(start_recipe, self.root, self.w, "sleepy")
        self.assertEqual(rc, 1)
        self.assertEqual(len(list_running(self.root)), 1)

    def test_stop_kills_process(self):
        self.quiet(start_recipe, self.root, self.w, "sleepy")
        pid = list_running(self.root)[0]["pid"]
        rc = self.quiet(stop_recipe, self.root, "sleepy")
        self.assertEqual(rc, 0)
        time.sleep(0.3)
        self.assertFalse(pid_alive(pid))
        self.assertEqual(list_running(self.root), [])

    def test_stop_unknown_recipe_errors(self):
        rc = self.quiet(stop_recipe, self.root, "sleepy")
        self.assertEqual(rc, 1)

    def test_output_goes_to_log_file(self):
        self.quiet(start_recipe, self.root, self.w, "sleepy")
        time.sleep(0.4)
        log = Path(list_running(self.root)[0]["log"])
        self.assertIn("started", log.read_text())

    def test_start_reports_immediate_death_with_log_tail(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = start_recipe(self.root, self.w, "dies")
        self.assertEqual(rc, 1)
        combined = out.getvalue() + err.getvalue()
        self.assertIn("boom: port already in use", combined)  # log tail shown
        self.assertEqual(list_running(self.root), [])  # no stale pidfile

    def test_foreground_recipe_can_still_be_started(self):
        # non-background recipes may be started too; they just finish
        rc = self.quiet(start_recipe, self.root, self.w, "fg-only")
        self.assertEqual(rc, 0)
        time.sleep(0.4)
        self.assertEqual(list_running(self.root), [])  # already exited


if __name__ == "__main__":
    unittest.main()
