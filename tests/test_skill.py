import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unpark import main

from test_parser import SAMPLE


class SkillFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home.name
        self.root = Path(self.tmp.name)
        (self.root / "WELCOME.md").write_text(SAMPLE)
        (self.root / "web").mkdir()

    def tearDown(self):
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        self.tmp.cleanup()
        self.home.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["-C", str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()


class TestSkillPrint(SkillFixture):
    def test_prints_canonical_instructions(self):
        rc, out, _ = self.cli("skill")
        self.assertEqual(rc, 0)
        self.assertIn("updated:", out)          # bump the stamp
        self.assertIn("State of things", out)   # what to rewrite
        self.assertIn("unpark check", out)     # how to validate
        self.assertNotIn("Claude", out)         # agent-agnostic wording

    def test_demands_scannable_writing(self):
        rc, out, _ = self.cli("skill")
        # the formatting contract: no walls of text
        self.assertIn("Formatting", out)
        self.assertIn("blank line", out)
        self.assertIn("bullet", out)
        self.assertIn("wall of text", out)

    def test_covers_first_time_setup(self):
        rc, out, _ = self.cli("skill")
        self.assertIn("first time", out.lower())
        self.assertIn("unpark init", out)
        self.assertIn("Set up unpark", out)    # the trigger phrase

    def test_skill_description_triggers_on_setup_requests(self):
        self.cli("skill", "--install")
        skill = (self.root / ".claude" / "skills" / "unpark-upkeep"
                 / "SKILL.md").read_text()
        head = skill.split("---")[1]            # front-matter only
        self.assertIn("set up", head.lower())


class TestSkillInstall(SkillFixture):
    def test_installs_agents_md_and_claude_skill(self):
        rc, out, _ = self.cli("skill", "--install")
        self.assertEqual(rc, 0)

        agents = (self.root / "AGENTS.md").read_text()
        self.assertIn("unpark:upkeep:start", agents)
        self.assertIn("State of things", agents)

        skill = (self.root / ".claude" / "skills" / "unpark-upkeep"
                 / "SKILL.md").read_text()
        self.assertTrue(skill.startswith("---\n"))
        self.assertIn("name: unpark-upkeep", skill)
        self.assertIn("description:", skill)
        self.assertIn("State of things", skill)

    def test_install_preserves_existing_agents_md(self):
        (self.root / "AGENTS.md").write_text(
            "# My project\n\nHand-written agent notes.\n")
        self.cli("skill", "--install")
        agents = (self.root / "AGENTS.md").read_text()
        self.assertIn("Hand-written agent notes.", agents)
        self.assertIn("unpark:upkeep:start", agents)

    def test_installs_carry_a_version_stamp(self):
        from unpark import _skill_version
        self.cli("skill", "--install")
        v = _skill_version()
        self.assertIn(v, (self.root / "AGENTS.md").read_text())
        self.assertIn(v, (self.root / ".claude" / "skills"
                          / "unpark-upkeep" / "SKILL.md").read_text())

    def test_check_warns_on_outdated_installed_copies(self):
        self.cli("skill", "--install")
        from unpark import _skill_version
        agents = self.root / "AGENTS.md"
        agents.write_text(agents.read_text().replace(
            _skill_version(), "deadbeef"))
        rc, out, err = self.cli("check")
        self.assertEqual(rc, 0)               # warning, not error
        self.assertIn("outdated", out + err)
        self.assertIn("skill --install", out + err)

    def test_check_quiet_when_copies_current(self):
        self.cli("skill", "--install")
        rc, out, _ = self.cli("check")
        self.assertNotIn("outdated", out)

    def test_install_is_idempotent_and_upgradable(self):
        self.cli("skill", "--install")
        first = (self.root / "AGENTS.md").read_text()
        self.cli("skill", "--install")
        second = (self.root / "AGENTS.md").read_text()
        self.assertEqual(first, second)
        self.assertEqual(second.count("unpark:upkeep:start"), 1)


class TestGlobalSkillTargets(SkillFixture):
    def test_can_select_one_global_target(self):
        rc, out, _ = self.cli("skill", "--install", "--global",
                              "--target", "codex")
        self.assertEqual(rc, 0)
        codex = Path(self.home.name) / ".codex" / "AGENTS.md"
        self.assertTrue(codex.exists())
        self.assertIn("unpark:upkeep:start", codex.read_text())
        self.assertFalse((Path(self.home.name) / ".claude").exists())
        self.assertIn("for codex", out)

    def test_all_installs_every_file_based_global_target(self):
        rc, _, _ = self.cli("skill", "--install", "--global",
                            "--target", "all")
        self.assertEqual(rc, 0)
        home = Path(self.home.name)
        self.assertTrue((home / ".claude" / "skills" / "unpark-upkeep"
                         / "SKILL.md").exists())
        self.assertTrue((home / ".codex" / "AGENTS.md").exists())
        self.assertTrue((home / ".copilot" / "instructions" /
                         "unpark.instructions.md").exists())


if __name__ == "__main__":
    unittest.main()
