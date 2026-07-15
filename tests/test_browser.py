import unittest

from unpark import _browser_command, _is_wsl


class TestIsWsl(unittest.TestCase):
    def test_detects_microsoft_kernel(self):
        self.assertTrue(_is_wsl(
            "Linux version 6.6.87.2-microsoft-standard-WSL2 ..."))

    def test_plain_linux_is_not_wsl(self):
        self.assertFalse(_is_wsl("Linux version 6.8.0-generic (gcc ...)"))

    def test_missing_proc_version(self):
        self.assertFalse(_is_wsl(""))


class TestBrowserCommand(unittest.TestCase):
    URL = "http://127.0.0.1:1234/"

    def test_explicit_browser_env_wins_even_on_wsl(self):
        # user configured $BROWSER: stdlib webbrowser honors it → None
        cmd = _browser_command(self.URL, browser_env="firefox",
                               is_wsl=True, have=lambda x: True)
        self.assertIsNone(cmd)

    def test_wsl_prefers_wslview(self):
        cmd = _browser_command(self.URL, browser_env="", is_wsl=True,
                               have=lambda x: x == "wslview")
        self.assertEqual(cmd, ["wslview", self.URL])

    def test_wsl_falls_back_to_windows_start(self):
        # no wslview: hand the URL to Windows via cmd.exe
        cmd = _browser_command(self.URL, browser_env="", is_wsl=True,
                               have=lambda x: x == "cmd.exe")
        self.assertEqual(cmd, ["cmd.exe", "/c", "start", "", self.URL])

    def test_plain_linux_uses_stdlib(self):
        cmd = _browser_command(self.URL, browser_env="", is_wsl=False,
                               have=lambda x: True)
        self.assertIsNone(cmd)


if __name__ == "__main__":
    unittest.main()
