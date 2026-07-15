import io
import unittest

from unpark.pager import display


class TtyBuffer(io.StringIO):
    def isatty(self):
        return True


class TestPagerPolicy(unittest.TestCase):
    def test_non_tty_prints_directly(self):
        stream = io.StringIO()
        self.assertFalse(display("one\ntwo", stream=stream,
                                 terminal_lines=1))
        self.assertEqual(stream.getvalue(), "one\ntwo\n")

    def test_short_tty_prints_directly(self):
        stream = TtyBuffer()
        self.assertFalse(display("one\ntwo", stream=stream,
                                 terminal_lines=2))
        self.assertEqual(stream.getvalue(), "one\ntwo\n")

    def test_no_pager_environment_wins(self):
        stream = TtyBuffer()
        self.assertFalse(display("one\ntwo", stream=stream,
                                 environ={"NO_PAGER": "1"},
                                 terminal_lines=1))
        self.assertEqual(stream.getvalue(), "one\ntwo\n")

    def test_overflow_uses_pager_and_less_defaults(self):
        calls = []

        def run(*args, **kwargs):
            calls.append((args, kwargs))

        self.assertTrue(display("one\ntwo", stream=TtyBuffer(),
                                environ={"PAGER": "more"},
                                terminal_lines=1, run=run))
        self.assertEqual(calls[0][0][0], ["more"])
        self.assertEqual(calls[0][1]["input"], "one\ntwo\n")
        self.assertEqual(calls[0][1]["env"]["LESS"], "FRX")

    def test_missing_pager_falls_back_to_direct_output(self):
        stream = TtyBuffer()

        def run(*args, **kwargs):
            raise OSError("not installed")

        self.assertFalse(display("one\ntwo", stream=stream,
                                 terminal_lines=1, run=run))
        self.assertEqual(stream.getvalue(), "one\ntwo\n")


if __name__ == "__main__":
    unittest.main()
