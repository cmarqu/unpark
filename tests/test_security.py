import unittest

from unpark import _safe_host_header


class TestLocalServerHostValidation(unittest.TestCase):
    def test_accepts_loopback_hosts(self):
        self.assertTrue(_safe_host_header("127.0.0.1:43120"))
        self.assertTrue(_safe_host_header("localhost:43120"))
        self.assertTrue(_safe_host_header("[::1]:43120"))

    def test_rejects_rebinding_host(self):
        self.assertFalse(_safe_host_header("attacker.example:43120"))


if __name__ == "__main__":
    unittest.main()
