import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import (
    main,
    parse_welcome,
    set_front_matter_key,
    vscode_target,
)

from test_parser import SAMPLE


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(SAMPLE)
        (self.root / "web").mkdir()

    def tearDown(self):
        os.environ.pop("UNPARK_CONFIG_DIR", None)
        self.tmp.cleanup()
        self.cfg.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue() + err.getvalue()


class TestFrontMatterEdit(Fixture):
    def test_insert_and_replace_key_preserving_body(self):
        f = self.root / "WELCOME.md"
        set_front_matter_key(f, "workspace", "a.code-workspace")
        w = parse_welcome(f.read_text())
        self.assertEqual(w.meta["workspace"], "a.code-workspace")
        self.assertEqual(w.meta["name"], "photo-globe")     # rest intact
        self.assertIn("Serves the globe", f.read_text())    # body intact

        set_front_matter_key(f, "workspace", "b.code-workspace")
        w = parse_welcome(f.read_text())
        self.assertEqual(w.meta["workspace"], "b.code-workspace")
        self.assertEqual(f.read_text().count("workspace:"), 1)


class TestWorkspaceCommand(Fixture):
    def test_status_shows_folder_before_and_workspace_after(self):
        rc, out = self.cli("-C", str(self.root), "workspace")
        self.assertEqual(rc, 0)
        self.assertIn("folder", out)

        rc, out = self.cli("-C", str(self.root), "workspace", "init")
        self.assertEqual(rc, 0)
        # VS Code only migrates chat/UI state on an in-window conversion;
        # an externally created file starts with empty workspace storage —
        # init must warn about this
        self.assertIn("Save Workspace As", out)
        self.assertIn("chat", out)
        ws = self.root / "photo-globe.code-workspace"  # named after project
        self.assertTrue(ws.exists())
        data = json.loads(ws.read_text())
        self.assertEqual(data["folders"], [{"path": "."}])

        rc, out = self.cli("-C", str(self.root), "workspace")
        self.assertIn("workspace", out)
        self.assertIn(ws.name, out)

    def test_init_refuses_when_workspace_already_exists(self):
        (self.root / "existing.code-workspace").write_text("{}")
        rc, out = self.cli("-C", str(self.root), "workspace", "init")
        self.assertEqual(rc, 1)
        self.assertIn("existing.code-workspace", out)

    def test_set_records_external_workspace_in_front_matter(self):
        outside = Path(self.tmp.name) / "elsewhere"
        outside.mkdir()
        ws = outside / "combo.code-workspace"
        ws.write_text("{}")
        rc, out = self.cli("-C", str(self.root), "workspace", "set",
                           str(ws))
        self.assertEqual(rc, 0)
        w = parse_welcome((self.root / "WELCOME.md").read_text())
        self.assertIn("combo.code-workspace", w.meta["workspace"])
        self.assertEqual(vscode_target(self.root, w), ws.resolve())

    def test_set_rejects_missing_file(self):
        rc, out = self.cli("-C", str(self.root), "workspace", "set",
                           "gone.code-workspace")
        self.assertEqual(rc, 1)

    def test_registration_survives_migration_untouched(self):
        self.cli("-C", str(self.root), "register")
        from unpark import registry_load
        before = registry_load()
        self.cli("-C", str(self.root), "workspace", "init")
        self.assertEqual(registry_load(), before)


class TestDashboardMigration(Fixture):
    def test_button_and_api(self):
        import queue
        import re
        import threading
        import urllib.request
        from unpark import parse_welcome as pw, render_html, serve_dashboard
        from test_render_text import derived

        page = render_html(pw(SAMPLE), derived(), token="tok")
        self.assertIn('data-setup="ws-init"', page)

        os.environ["UNPARK_STATE_DIR"] = tempfile.mkdtemp()
        urls = queue.Queue()
        t = threading.Thread(target=lambda: serve_dashboard(
            self.root, pw(SAMPLE), timeout=15.0, grace=1.0,
            on_bound=urls.put), daemon=True)
        with redirect_stdout(io.StringIO()):
            t.start()
        url = urls.get(timeout=5).rstrip("/")
        html = urllib.request.urlopen(url + "/", timeout=5).read().decode()
        tok = re.search(r'TOKEN\s*=\s*"([^"]+)"', html).group(1)
        req = urllib.request.Request(url + "/api/workspace/init",
                                     method="POST",
                                     headers={"X-Unpark-Token": tok},
                                     data=b"")
        body = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertTrue(body["ok"])
        self.assertTrue((self.root / "photo-globe.code-workspace").exists())
        t.join(timeout=6)


if __name__ == "__main__":
    unittest.main()
