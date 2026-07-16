import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import code_command, main, parse_welcome, vscode_target

from test_parser import SAMPLE


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(SAMPLE)
        self.w = parse_welcome(SAMPLE)

    def tearDown(self):
        os.environ.pop("UNPARK_CONFIG_DIR", None)
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.cfg.cleanup()
        self.state.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue() + err.getvalue()


class TestVscodeTarget(Fixture):
    def test_folder_based_project(self):
        self.assertEqual(vscode_target(self.root, self.w),
                         self.root.resolve())

    def test_workspace_file_in_root_is_detected(self):
        ws = self.root / "photo.code-workspace"
        ws.write_text("{}")
        self.assertEqual(vscode_target(self.root, self.w), ws.resolve())

    def test_workspace_file_in_dot_vscode(self):
        (self.root / ".vscode").mkdir()
        ws = self.root / ".vscode" / "photo.code-workspace"
        ws.write_text("{}")
        self.assertEqual(vscode_target(self.root, self.w), ws.resolve())

    def test_front_matter_workspace_wins(self):
        (self.root / "other.code-workspace").write_text("{}")
        (self.root / "docs").mkdir()
        ws = self.root / "docs" / "real.code-workspace"
        ws.write_text("{}")
        w = parse_welcome(SAMPLE.replace(
            "updated: 2026-06-02",
            "updated: 2026-06-02\nworkspace: docs/real.code-workspace"))
        self.assertEqual(vscode_target(self.root, w), ws.resolve())

    def test_code_command(self):
        ws = self.root / "p.code-workspace"
        ws.write_text("{}")
        self.assertEqual(code_command(self.root, self.w),
                         ["code", str(ws.resolve())])


class TestProjectsCommand(Fixture):
    def test_lists_registered_projects_from_anywhere(self):
        self.cli("-C", str(self.root), "register")
        with tempfile.TemporaryDirectory() as elsewhere:
            rc, out = self.cli("-C", elsewhere, "projects")
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out)
        self.assertIn("registered", out)          # says what this list is
        self.assertIn(self.cfg.name, out)         # and where it lives

    def test_empty_registry_says_so(self):
        rc, out = self.cli("projects")
        self.assertEqual(rc, 0)
        self.assertIn("no projects registered", out)
        self.assertIn("unpark register", out)


class TestHelpNamesProject(Fixture):
    def test_help_inside_project_names_it(self):
        old = os.getcwd()
        os.chdir(self.root)
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["help"])
        finally:
            os.chdir(old)
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out.getvalue())

    def test_help_outside_project_says_none(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                out = io.StringIO()
                with redirect_stdout(out):
                    rc = main(["help"])
            finally:
                os.chdir(old)
        self.assertIn("no project", out.getvalue())


class TestManualExplainsManagedProcesses(unittest.TestCase):
    def test_section_covers_lifecycle(self):
        from unpark import _DOCS
        body = dict(_DOCS)["Managed processes"]
        for needle in ("run", "start", "detached", "survive",
                       "state directory", "log", "stop", "heartbeat"):
            self.assertIn(needle, body)


if __name__ == "__main__":
    unittest.main()
