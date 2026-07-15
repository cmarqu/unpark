import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from unpark import commits_since, git_info


def run(cwd, *args, date=None):
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    env.setdefault("GIT_AUTHOR_NAME", "t")
    env.setdefault("GIT_AUTHOR_EMAIL", "t@t")
    env.setdefault("GIT_COMMITTER_NAME", "t")
    env.setdefault("GIT_COMMITTER_EMAIL", "t@t")
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   capture_output=True)


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        run(self.root, "init", "-q", "-b", "main")
        (self.root / "a.txt").write_text("one\n")
        run(self.root, "add", ".")
        run(self.root, "commit", "-qm", "first", date="2026-05-01T10:00:00")
        (self.root / "a.txt").write_text("two\n")
        run(self.root, "add", ".")
        run(self.root, "commit", "-qm", "fix marker jitter",
            date="2026-07-03T10:00:00")

    def tearDown(self):
        self.tmp.cleanup()


class TestGitInfo(GitFixture):
    def test_reports_branch_and_last_commit(self):
        info = git_info(self.root)
        self.assertEqual(info["branch"], "main")
        self.assertEqual(info["last_subject"], "fix marker jitter")
        self.assertEqual(info["last_date"], "2026-07-03")

    def test_counts_dirty_files(self):
        (self.root / "b.txt").write_text("untracked\n")
        (self.root / "a.txt").write_text("modified\n")
        info = git_info(self.root)
        self.assertEqual(info["dirty"], 2)

    def test_returns_none_outside_git(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(git_info(Path(d)))


class TestStaleness(GitFixture):
    def test_commits_since_updated_date(self):
        count, latest = commits_since(self.root, "2026-06-02")
        self.assertEqual(count, 1)
        self.assertEqual(latest, "fix marker jitter")

    def test_up_to_date_when_no_newer_commits(self):
        count, latest = commits_since(self.root, "2026-07-04")
        self.assertEqual(count, 0)

    def test_bad_date_returns_zero(self):
        count, latest = commits_since(self.root, "not-a-date")
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
