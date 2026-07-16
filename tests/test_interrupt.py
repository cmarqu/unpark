import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import unpark as W

from test_parser import SAMPLE

class TestCtrlC(unittest.TestCase):
    def test_keyboard_interrupt_returns_130_quietly(self):
        real = W._main

        def boom(argv=None):
            raise KeyboardInterrupt()

        W._main = boom
        try:
            rc = W.main([])
        finally:
            W._main = real
        self.assertEqual(rc, 130)

    def test_sigint_during_serve_exits_cleanly(self):
        if os.name == "nt":
            self.skipTest("Windows cannot deliver SIGINT to this child")
        with tempfile.TemporaryDirectory() as d, \
             tempfile.TemporaryDirectory() as state:
            (Path(d) / "WELCOME.md").write_text(SAMPLE)
            env = dict(os.environ, UNPARK_NO_BROWSER="1",
                       UNPARK_STATE_DIR=state)
            p = subprocess.Popen(
                [sys.executable, "-m", "unpark", "-C", d, "html",
                 "--no-open"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env)
            try:
                # wait for the server to come up, then Ctrl+C it
                deadline = time.monotonic() + 10
                line = ""
                while time.monotonic() < deadline and "http" not in line:
                    line = p.stdout.readline()
                self.assertIn("http", line)
                p.send_signal(signal.SIGINT)
                out, err = p.communicate(timeout=10)
            finally:
                if p.poll() is None:
                    p.kill()
            self.assertEqual(p.returncode, 130)
            self.assertNotIn("Traceback", err)
            self.assertIn("interrupted", err)


if __name__ == "__main__":
    unittest.main()
