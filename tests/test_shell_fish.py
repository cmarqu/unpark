import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import _FISH_MARKER_START, _fish_version, main, shell_config_path
from test_parser import SAMPLE

FISH = shutil.which("fish")
GIT = shutil.which("git")


class ShellFixture(unittest.TestCase):
    def setUp(self):
        # base: the "~/projects" directory that holds the test projects;
        # it must not itself be a project, or the upward walk leaks
        self.base = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home.name
        self.root = Path(self.base.name) / "proj-a"
        self.root.mkdir()
        (self.root / "WELCOME.md").write_text(SAMPLE)
        (self.root / "web").mkdir()

    def tearDown(self):
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        self.base.cleanup()
        self.home.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def make_project(self, name, tagline="A different project"):
        d = Path(self.base.name) / name
        d.mkdir(exist_ok=True)
        (d / "WELCOME.md").write_text(SAMPLE.replace(
            "name: photo-globe\ntagline: Photos on a spinning 3D globe",
            f"name: {name}\ntagline: {tagline}"))
        return d

    def make_git_repo(self, name):
        d = Path(self.base.name) / name
        d.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        return d


class TestShellFishPrint(ShellFixture):
    def test_prints_script_without_installing(self):
        rc, out, _ = self.cli("shell", "fish")
        self.assertEqual(rc, 0)
        self.assertIn(_FISH_MARKER_START, out)
        self.assertIn("__unpark_dir_change_hook", out)
        self.assertIn("functions -c fish_prompt", out)
        self.assertIn("uvx unpark cd-hook", out)
        self.assertIn("__unpark_hook_error_shown", out)
        self.assertFalse((Path(self.home.name) / ".config" / "fish")
                         .exists())

    def test_printed_script_is_stable_and_versioned(self):
        _, out1, _ = self.cli("shell", "fish")
        _, out2, _ = self.cli("shell", "fish")
        self.assertEqual(out1, out2)
        self.assertIn(f"unpark-shell-version: {_fish_version()}", out1)

    def test_unknown_target_rejected(self):
        with self.assertRaises(SystemExit):
            self.cli("shell", "bash")


class TestShellFishInstall(ShellFixture):
    def hook_file(self):
        return Path(self.home.name) / ".config" / "fish" / "conf.d" \
            / "unpark.fish"

    def test_install_writes_conf_d(self):
        rc, out, _ = self.cli("shell", "fish", "--install")
        self.assertEqual(rc, 0)
        f = self.hook_file()
        self.assertTrue(f.exists())
        text = f.read_text()
        self.assertIn(_FISH_MARKER_START, text)
        self.assertIn(f"unpark-shell-version: {_fish_version()}", text)
        self.assertIn(str(f), out)

    def test_install_is_idempotent(self):
        self.cli("shell", "fish", "--install")
        first = self.hook_file().read_text()
        self.cli("shell", "fish", "--install")
        self.assertEqual(first, self.hook_file().read_text())
        self.assertEqual(first.count(_FISH_MARKER_START), 1)

    def test_preserves_user_lines_and_uninstall_keeps_them(self):
        f = self.hook_file()
        f.parent.mkdir(parents=True)
        f.write_text("# my own conf.d lines\nabbr -a g git\n\n")
        rc, _, _ = self.cli("shell", "fish", "--install")
        self.assertEqual(rc, 0)
        text = f.read_text()
        self.assertIn("abbr -a g git", text)
        self.assertIn(_FISH_MARKER_START, text)
        rc, _, _ = self.cli("shell", "fish", "--uninstall")
        self.assertEqual(rc, 0)
        text = f.read_text()
        self.assertIn("abbr -a g git", text)
        self.assertNotIn(_FISH_MARKER_START, text)

    def test_uninstall_removes_file_when_only_block(self):
        self.cli("shell", "fish", "--install")
        rc, _, _ = self.cli("shell", "fish", "--uninstall")
        self.assertEqual(rc, 0)
        self.assertFalse(self.hook_file().exists())

    def test_uninstall_without_install_is_quiet_success(self):
        rc, out, _ = self.cli("shell", "fish", "--uninstall")
        self.assertEqual(rc, 0)
        self.assertIn("no unpark shell hook installed", out)

    def test_install_upgrades_stale_block(self):
        self.cli("shell", "fish", "--install")
        f = self.hook_file()
        f.write_text(f.read_text().replace(_fish_version(), "deadbeef"))
        rc, _, _ = self.cli("shell", "fish", "--install")
        self.assertEqual(rc, 0)
        text = f.read_text()
        self.assertIn(_fish_version(), text)
        self.assertNotIn("deadbeef", text)

    def test_xdg_config_home_honored(self):
        with tempfile.TemporaryDirectory() as xdg:
            os.environ["XDG_CONFIG_HOME"] = xdg
            try:
                rc, _, _ = self.cli("shell", "fish", "--install")
                self.assertEqual(rc, 0)
                self.assertTrue((Path(xdg) / "fish" / "conf.d" /
                                 "unpark.fish").exists())
            finally:
                del os.environ["XDG_CONFIG_HOME"]


class TestCdHook(ShellFixture):
    def test_briefs_on_project_entry(self):
        rc, out, _ = self.cli("cd-hook", str(self.root))
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out)

    def test_briefing_from_subdirectory_names_the_root(self):
        rc, out, _ = self.cli("cd-hook", str(self.root / "web"))
        self.assertEqual(rc, 0)
        self.assertIn("project: photo-globe", out)
        self.assertIn("photo-globe", out)

    def test_silent_within_same_project(self):
        rc, out, _ = self.cli("cd-hook", str(self.root / "web"),
                              "--from", str(self.root))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_briefs_on_project_switch(self):
        other = self.make_project("other", "the other project")
        rc, out, _ = self.cli("cd-hook", str(other),
                              "--from", str(self.root))
        self.assertEqual(rc, 0)
        self.assertIn("the other project", out)
        self.assertNotIn("photo-globe", out)

    def test_silent_outside_any_project(self):
        plain = Path(self.base.name) / "plain"
        plain.mkdir()
        rc, out, err = self.cli("cd-hook", str(plain),
                                "--from", str(self.root))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")
        self.assertEqual(err.strip(), "")

    @unittest.skipUnless(GIT, "git not installed")
    def test_git_repo_without_briefing_offers_init(self):
        repo = self.make_git_repo("bare-repo")
        rc, out, err = self.cli("cd-hook", str(repo),
                                "--from", str(self.root))
        self.assertEqual(rc, 0)
        self.assertIn("unpark init", err)
        self.assertEqual(out.strip(), "")
        self.assertFalse((repo / "WELCOME.md").exists())

    @unittest.skipUnless(GIT, "git not installed")
    def test_silent_within_same_git_repo(self):
        repo = self.make_git_repo("bare-repo")
        (repo / "sub").mkdir()
        rc, out, err = self.cli("cd-hook", str(repo / "sub"),
                                "--from", str(repo))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")
        self.assertEqual(err.strip(), "")  # one hint per repo entry

    @unittest.skipUnless(GIT, "git not installed")
    def test_auto_init_creates_template(self):
        repo = self.make_git_repo("bare-repo")
        os.environ["UNPARK_CD_AUTO_INIT"] = "1"
        try:
            rc, out, _ = self.cli("cd-hook", str(repo),
                                  "--from", str(self.root))
        finally:
            del os.environ["UNPARK_CD_AUTO_INIT"]
        self.assertEqual(rc, 0)
        self.assertTrue((repo / "WELCOME.md").exists())
        self.assertIn("created", out)

    @unittest.skipUnless(GIT, "git not installed")
    def test_hint_only_once_per_repo_entry(self):
        repo = self.make_git_repo("bare-repo")
        first = self.cli("cd-hook", str(repo), "--from", str(self.root))
        self.assertIn("unpark init", first[2])
        # moving deeper into the same repo: no second hint
        (repo / "sub").mkdir()
        second = self.cli("cd-hook", str(repo / "sub"),
                          "--from", str(repo))
        self.assertEqual(second[2].strip(), "")


@unittest.skipUnless(FISH, "fish not installed")
class TestFishHookEndToEnd(ShellFixture):
    def fish_run(self, script, uvx_shim=None):
        """Run a fish one-liner with the installed hook. The hook calls
        `uvx unpark ...`; by default a shim maps that onto the local
        package, uvx_shim overrides it (e.g. to make it fail)."""
        wrapper = Path(self.base.name) / "bin"
        wrapper.mkdir(exist_ok=True)
        shim = wrapper / "uvx"
        if uvx_shim is None:
            uvx_shim = ("#!/bin/sh\n"
                        "shift  # drop the tool name\n"
                        "exec python3 -m unpark \"$@\"\n")
        shim.write_text(uvx_shim)
        os.chmod(shim, 0o755)
        env = dict(os.environ)
        env["PATH"] = f"{wrapper}{os.pathsep}{env.get('PATH', '')}"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent
                                / "src")
        return subprocess.run([FISH, "-c", script], capture_output=True,
                              text=True, env=env, timeout=60)

    def test_prompt_wrapped_exactly_once(self):
        rc, _, _ = self.cli("shell", "fish", "--install")
        self.assertEqual(rc, 0)
        conf = shell_config_path("fish")
        p = self.fish_run(f"""
            source {conf}
            source {conf}
            string match -q "*__unpark_dir_change_hook*" (functions fish_prompt)
            and echo wrapped-ok
            string match -q "*__unpark_dir_change_hook*" (functions __unpark_orig_fish_prompt)
            or echo orig-clean
        """)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("wrapped-ok", p.stdout)
        self.assertIn("orig-clean", p.stdout)  # no double-wrap

    def test_hook_briefs_on_project_change_only(self):
        rc, _, _ = self.cli("shell", "fish", "--install")
        self.assertEqual(rc, 0)
        conf = shell_config_path("fish")
        other = self.make_project("other", "the other project")
        p = self.fish_run(f"""
            source {conf}
            cd {self.root}
            __unpark_dir_change_hook
            cd {self.root / "web"}
            __unpark_dir_change_hook
            cd {other}
            __unpark_dir_change_hook
        """)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("the other project", p.stdout)  # switch → briefing
        self.assertNotIn("photo-globe", p.stdout)     # entry+same project:
        # silent (last_dir seeded on first call, then same root)

    def test_failing_uv_reported_once_per_session(self):
        rc, _, _ = self.cli("shell", "fish", "--install")
        self.assertEqual(rc, 0)
        conf = shell_config_path("fish")
        for n in (1, 2, 3):
            (Path(self.base.name) / f"d{n}").mkdir(exist_ok=True)
        p = self.fish_run(f"""
            source {conf}
            cd {Path(self.base.name) / 'd1'}
            __unpark_dir_change_hook
            cd {Path(self.base.name) / 'd2'}
            __unpark_dir_change_hook
            cd {Path(self.base.name) / 'd3'}
            __unpark_dir_change_hook
        """, uvx_shim=("#!/bin/sh\n"
                       "echo 'uvx: error: mock failure' >&2\n"
                       "exit 1\n"))
        self.assertEqual(p.returncode, 0, p.stderr)
        # three failing directory changes, one report per session
        self.assertEqual(p.stderr.count("unpark cd-hook failed"), 1)
        # ...with the file the hook was called from and the error
        self.assertIn(str(conf), p.stderr)
        self.assertIn("mock failure", p.stderr)
        # nothing leaked into the prompt
        self.assertEqual(p.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
