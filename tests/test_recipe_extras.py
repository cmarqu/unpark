import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import main, parse_welcome, pick_recipe, run_recipe

EXTRAS = """\
---
name: extras
---

## Recipes

### echoargs

```sh
python -c "from pathlib import Path; Path('out.txt').write_text('start\\n')"
python -c "import pathlib,sys; pathlib.Path('out.txt').open('a').write(' '.join(sys.argv[1:]) + '\\n')" hello
```

### enviro

- env: GREETING=hallo PORT=99

```sh
python -c "import os,pathlib; pathlib.Path('env.txt').write_text(os.environ['GREETING'] + ':' + os.environ['PORT'] + '\\n')"
```

### dotty

- dotenv: .env

```sh
python -c "import os,pathlib; pathlib.Path('secret.txt').write_text(os.environ['SECRET'] + '\\n')"
```

### demo

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
        (self.root / "WELCOME.md").write_text(EXTRAS)
        self.w = parse_welcome(EXTRAS)

    def tearDown(self):
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.state.cleanup()

    def quiet(self, fn, *a, **kw):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return fn(*a, **kw)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue() + err.getvalue()


class TestArgPassthrough(Fixture):
    def test_extra_args_append_to_last_step_quoted(self):
        rc = self.quiet(run_recipe, self.root, self.w, "echoargs",
                        extra_args=["world", "two words"])
        self.assertEqual(rc, 0)
        self.assertEqual((self.root / "out.txt").read_text(),
                         "start\nhello world two words\n")

    def test_cli_passthrough_after_double_dash(self):
        rc, _ = self.cli("-C", str(self.root), "run", "echoargs",
                         "--", "via-cli")
        self.assertEqual(rc, 0)
        self.assertIn("via-cli", (self.root / "out.txt").read_text())


class TestEnvAndDotenv(Fixture):
    def test_env_metadata_reaches_the_step(self):
        rc = self.quiet(run_recipe, self.root, self.w, "enviro")
        self.assertEqual(rc, 0)
        self.assertEqual((self.root / "env.txt").read_text(), "hallo:99\n")

    def test_dotenv_file_is_loaded(self):
        (self.root / ".env").write_text(
            "# comment\nexport SECRET=s3cret\nOTHER='quoted'\n")
        rc = self.quiet(run_recipe, self.root, self.w, "dotty")
        self.assertEqual(rc, 0)
        self.assertEqual((self.root / "secret.txt").read_text(), "s3cret\n")

    def test_missing_dotenv_warns_in_check(self):
        rc, out = self.cli("-C", str(self.root), "check")
        self.assertEqual(rc, 0)
        self.assertIn(".env", out)     # dotty references a missing .env


class TestPicker(Fixture):
    def test_pick_by_number_and_name(self):
        outbuf = io.StringIO()
        with redirect_stdout(outbuf):
            name = pick_recipe(self.w, "run", ask=lambda prompt: "1")
        self.assertEqual(name, "echoargs")
        self.assertIn("demo", outbuf.getvalue())   # list was shown
        with redirect_stdout(io.StringIO()):
            name = pick_recipe(self.w, "run", ask=lambda prompt: "demo")
        self.assertEqual(name, "demo")

    def test_run_without_recipe_non_tty_lists_and_errors(self):
        rc, out = self.cli("-C", str(self.root), "run")
        self.assertEqual(rc, 2)
        self.assertIn("echoargs", out)
        self.assertIn("demo", out)


if __name__ == "__main__":
    unittest.main()
