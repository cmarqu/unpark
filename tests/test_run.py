import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import parse_welcome, run_recipe

RUNNABLE = """\
---
name: fixture
---

## Recipes

### hello

```sh
echo one >> out.txt
```

```sh
echo two >> out.txt
```

### subdir

- dir: web

```sh
echo here > loc.txt
```

### broken

```sh
echo before >> trail.txt
exit 3
```

```sh
echo after >> trail.txt
```

### publish

- needs: hello

```sh
echo pub >> out.txt
```

### speak

```sh
printf 'MAR%s\\n' KER_OUT
```
"""


class TestRunRecipe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "web").mkdir()
        self.w = parse_welcome(RUNNABLE)

    def tearDown(self):
        self.tmp.cleanup()

    def run_quiet(self, name):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return run_recipe(self.root, self.w, name)

    def test_steps_run_in_order(self):
        rc = self.run_quiet("hello")
        self.assertEqual(rc, 0)
        self.assertEqual((self.root / "out.txt").read_text(), "one\ntwo\n")

    def test_dir_metadata_sets_cwd(self):
        rc = self.run_quiet("subdir")
        self.assertEqual(rc, 0)
        self.assertTrue((self.root / "web" / "loc.txt").exists())

    def test_failing_step_stops_and_propagates_code(self):
        rc = self.run_quiet("broken")
        self.assertEqual(rc, 3)
        self.assertEqual((self.root / "trail.txt").read_text(), "before\n")

    def test_needs_runs_dependency_first(self):
        rc = self.run_quiet("publish")
        self.assertEqual(rc, 0)
        self.assertEqual((self.root / "out.txt").read_text(),
                         "one\ntwo\npub\n")

    def test_unknown_recipe_errors(self):
        rc = self.run_quiet("nope")
        self.assertEqual(rc, 2)

    def test_step_header_precedes_step_output_when_piped(self):
        import subprocess as sp
        import sys
        (self.root / "WELCOME.md").write_text(RUNNABLE)
        p = sp.run([sys.executable, "-m", "unpark", "-C", str(self.root),
                    "run", "speak"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        # MARKER_OUT is printed only by the step itself, never by the header
        self.assertLess(p.stdout.index("[speak] step 1/1"),
                        p.stdout.index("MARKER_OUT"))


if __name__ == "__main__":
    unittest.main()
