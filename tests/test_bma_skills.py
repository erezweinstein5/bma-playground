"""Ensure session discovery paths resolve to the skills copied into the image."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from bma_skills import SKILLS, skill_configuration


class PackagedSkillTests(unittest.TestCase):
    def test_role_directories_resolve_to_self_contained_plugin_skills(self):
        for name, directory in SKILLS.items():
            with self.subTest(skill=name):
                source = ROOT / "runtime" / directory.removeprefix("/opt/bma/")
                plugin = json.loads((source.parent / ".codex-plugin/plugin.json").read_text())
                self.assertEqual(plugin["name"], name)
                self.assertRegex(plugin["version"], r"^\d+\.\d+\.\d+$")
                self.assertEqual((source.parent / plugin["skills"]).resolve(), source.resolve())
                files = list(source.glob("*/SKILL.md"))
                self.assertEqual(files, [source / name / "SKILL.md"])
                body = files[0].read_text()
                self.assertTrue(body.startswith(f"---\nname: {name}\ndescription: "))
                self.assertIn(f"{directory}/{name}/SKILL.md",
                              skill_configuration(name)["instructions"])


if __name__ == "__main__":
    unittest.main()
