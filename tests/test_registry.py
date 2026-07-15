import io
import json
import os
import queue
import re
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import (
    main,
    parse_welcome,
    registry_add,
    registry_file,
    registry_load,
    registry_remove,
    render_html,
    serve_dashboard,
)

from test_parser import SAMPLE
from test_render_text import derived


class RegistryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name) / "proj"
        self.root.mkdir()
        (self.root / "WELCOME.md").write_text(SAMPLE)

    def tearDown(self):
        for var in ("UNPARK_CONFIG_DIR", "UNPARK_STATE_DIR"):
            os.environ.pop(var, None)
        self.tmp.cleanup()
        self.cfg.cleanup()
        self.state.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(list(args))
        return rc, out.getvalue() + err.getvalue()


class TestRegistryStore(RegistryFixture):
    def test_roundtrip_add_remove(self):
        self.assertEqual(registry_load(), [])
        registry_add(self.root)
        self.assertEqual(registry_load(), [self.root.resolve()])
        registry_add(self.root)  # idempotent
        self.assertEqual(len(registry_load()), 1)
        registry_remove(self.root)
        self.assertEqual(registry_load(), [])

    def test_config_file_lives_in_config_dir(self):
        registry_add(self.root)
        self.assertTrue(str(registry_file()).startswith(self.cfg.name))

    def test_remove_missing_is_harmless(self):
        registry_remove(self.root)
        self.assertEqual(registry_load(), [])


class TestRegisterCli(RegistryFixture):
    def test_register_and_unregister(self):
        rc, out = self.cli("-C", str(self.root), "register")
        self.assertEqual(rc, 0)
        self.assertIn("registered", out)
        self.assertEqual(registry_load(), [self.root.resolve()])

        rc, out = self.cli("-C", str(self.root), "unregister")
        self.assertEqual(rc, 0)
        self.assertEqual(registry_load(), [])

    def test_briefing_shows_registration_state(self):
        rc, out = self.cli("-C", str(self.root))
        self.assertIn("not in your portfolio", out)
        registry_add(self.root)
        rc, out = self.cli("-C", str(self.root))
        self.assertNotIn("not in your portfolio", out)

    def test_portfolio_includes_registered_projects_from_anywhere(self):
        registry_add(self.root)
        with tempfile.TemporaryDirectory() as elsewhere:
            rc, out = self.cli("-C", elsewhere)
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out)   # the registered project's name

    def test_stale_registry_entry_is_skipped(self):
        gone = Path(self.tmp.name) / "gone"
        gone.mkdir()
        (gone / "WELCOME.md").write_text(SAMPLE)
        registry_add(gone)
        (gone / "WELCOME.md").unlink()
        gone.rmdir()
        registry_add(self.root)
        with tempfile.TemporaryDirectory() as elsewhere:
            rc, out = self.cli("-C", elsewhere)
        self.assertEqual(rc, 0)
        self.assertIn("photo-globe", out)


class TestDashboardSetupSection(RegistryFixture):
    def test_dashboard_offers_buttons_not_cli(self):
        w = parse_welcome(SAMPLE)
        out = render_html(w, derived(), token="tok")
        self.assertIn('data-setup="skill-local"', out)
        self.assertIn('data-setup="skill-global"', out)
        self.assertIn('data-setup="register"', out)
        self.assertIn('id="su-reg"', out)

    def test_register_api_roundtrip(self):
        urls = queue.Queue()
        t = threading.Thread(target=lambda: serve_dashboard(
            self.root, parse_welcome(SAMPLE), timeout=15.0, grace=1.0,
            on_bound=urls.put), daemon=True)
        with redirect_stdout(io.StringIO()):
            t.start()
            url = urls.get(timeout=5).rstrip("/")
            page = urllib.request.urlopen(url + "/", timeout=5).read().decode()
            tok = re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)
            hdr = {"X-Unpark-Token": tok}

            def post(path):
                req = urllib.request.Request(url + path, method="POST",
                                             headers=hdr, data=b"")
                return json.loads(urllib.request.urlopen(req, timeout=5).read())

            def status():
                req = urllib.request.Request(url + "/api/status", headers=hdr)
                return json.loads(urllib.request.urlopen(req, timeout=5).read())

            self.assertFalse(status()["registered"])
            self.assertTrue(post("/api/register")["ok"])
            self.assertTrue(status()["registered"])
            self.assertEqual(registry_load(), [self.root.resolve()])
            self.assertTrue(post("/api/unregister")["ok"])
            self.assertFalse(status()["registered"])
            t.join(timeout=6)


if __name__ == "__main__":
    unittest.main()
