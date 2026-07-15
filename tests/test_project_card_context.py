import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import (
    _UPKEEP_TEXT,
    gather_registered,
    main,
    registry_add,
    render_portfolio_html,
)

from test_parser import SAMPLE


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.folder_proj = Path(self.tmp.name) / "folderish"
        self.folder_proj.mkdir()
        (self.folder_proj / "web").mkdir()
        (self.folder_proj / "WELCOME.md").write_text(
            SAMPLE.replace("name: photo-globe", "name: folderish"))
        self.ws_proj = Path(self.tmp.name) / "wspaced"
        self.ws_proj.mkdir()
        (self.ws_proj / "web").mkdir()
        (self.ws_proj / "WELCOME.md").write_text(
            SAMPLE.replace("name: photo-globe", "name: wspaced"))
        (self.ws_proj / "main.code-workspace").write_text("{}")
        registry_add(self.folder_proj)
        registry_add(self.ws_proj)

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


class TestCardContext(Fixture):
    def page(self):
        return render_portfolio_html(gather_registered(), self.tmp.name,
                                     token="tok", title="x")

    def test_cards_show_full_project_path(self):
        page = self.page()
        self.assertIn(str(self.folder_proj.resolve()), page)
        self.assertIn(str(self.ws_proj.resolve()), page)

    def test_cards_state_folder_vs_workspace(self):
        page = self.page()
        self.assertIn("opens as <b>folder</b>", page)
        self.assertIn("opens as <b>workspace</b>", page)
        self.assertIn("main.code-workspace", page)

    def test_cmd_hint_uses_full_path_not_bare_name(self):
        page = self.page()
        self.assertIn(f"unpark -C {self.folder_proj.resolve()}", page)


class TestWorkspaceChecks(Fixture):
    def test_check_fails_on_dangling_workspace_key(self):
        (self.ws_proj / "WELCOME.md").write_text(SAMPLE.replace(
            "updated: 2026-06-02",
            "updated: 2026-06-02\nworkspace: ../elsewhere/gone.code-workspace"))
        rc, out = self.cli("-C", str(self.ws_proj), "check")
        self.assertEqual(rc, 1)
        self.assertIn("gone.code-workspace", out)

    def test_check_warns_on_multiple_workspace_files(self):
        (self.ws_proj / "another.code-workspace").write_text("{}")
        rc, out = self.cli("-C", str(self.ws_proj), "check")
        self.assertEqual(rc, 0)      # ambiguity is a warning, not an error
        self.assertIn("warning", out)
        self.assertIn("code-workspace", out)

    def test_skill_advises_in_repo_workspace_files(self):
        self.assertIn("repo root", _UPKEEP_TEXT)
        self.assertIn(".code-workspace", _UPKEEP_TEXT)


if __name__ == "__main__":
    unittest.main()
