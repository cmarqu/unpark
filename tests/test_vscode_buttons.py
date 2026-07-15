import io
import json
import os
import queue
import re
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

import unpark as W
from unpark import (
    gather_registered,
    parse_welcome,
    registry_add,
    render_html,
    render_portfolio_html,
    serve_dashboard,
)

from test_parser import SAMPLE
from test_render_text import derived


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["UNPARK_CONFIG_DIR"] = self.cfg.name
        os.environ["UNPARK_STATE_DIR"] = self.state.name
        self.root = Path(self.tmp.name) / "proj"
        self.root.mkdir()
        (self.root / "WELCOME.md").write_text(SAMPLE)
        registry_add(self.root)
        self.w = parse_welcome(SAMPLE)

    def tearDown(self):
        os.environ.pop("UNPARK_CONFIG_DIR", None)
        os.environ.pop("UNPARK_STATE_DIR", None)
        self.tmp.cleanup()
        self.cfg.cleanup()
        self.state.cleanup()


class TestButtonsRender(Fixture):
    def test_dashboard_setup_has_vscode_row(self):
        out = render_html(self.w, derived(), token="tok")
        self.assertIn('data-setup="code"', out)
        self.assertIn('id="su-code"', out)

    def test_projects_cards_have_vscode_buttons(self):
        page = render_portfolio_html(gather_registered(), self.root,
                                     token="tok", title="x")
        self.assertIn("openCode", page)
        self.assertIn('data-code="%s"' % self.root.resolve(), page)


class TestCodeApi(Fixture):
    def serve(self):
        urls = queue.Queue()
        t = threading.Thread(target=lambda: serve_dashboard(
            self.root, self.w, timeout=15.0, grace=1.0,
            on_bound=urls.put), daemon=True)
        with redirect_stdout(io.StringIO()):
            t.start()
        return t, urls.get(timeout=5).rstrip("/")

    def test_status_reports_code_target_and_api_opens(self):
        (self.root / "p.code-workspace").write_text("{}")
        calls = []
        real = W.open_vscode
        W.open_vscode = lambda p: (calls.append(Path(p)) or
                                   {"kind": "workspace", "target": "x"})
        try:
            t, url = self.serve()
            page = urllib.request.urlopen(url + "/", timeout=5).read().decode()
            tok = re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)
            hdr = {"X-Unpark-Token": tok}

            req = urllib.request.Request(url + "/api/status", headers=hdr)
            status = json.loads(urllib.request.urlopen(req, timeout=5).read())
            self.assertEqual(status["code_kind"], "workspace")
            self.assertIn("p.code-workspace", status["code_target"])

            req = urllib.request.Request(url + "/api/code", method="POST",
                                         headers=hdr, data=b"")
            body = json.loads(urllib.request.urlopen(req, timeout=5).read())
            self.assertTrue(body["ok"])
            self.assertEqual(calls, [self.root.resolve()])

            # for a registered sibling project via ?p=
            req = urllib.request.Request(
                url + "/api/code?p=" + str(self.root.resolve()),
                method="POST", headers=hdr, data=b"")
            body = json.loads(urllib.request.urlopen(req, timeout=5).read())
            self.assertTrue(body["ok"])
            t.join(timeout=6)
        finally:
            W.open_vscode = real


if __name__ == "__main__":
    unittest.main()
