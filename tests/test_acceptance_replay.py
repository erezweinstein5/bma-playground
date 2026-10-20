"""The negative browser controls must not turn runner errors into green evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import replay_acceptance_recovery as replay


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture = Path(__file__).resolve().parent / "fixtures/acceptance-recovery/reviewed-bundle.json"
        bundle = json.loads(fixture.read_text())
        failed = bundle["manifest"]["recovery"]["checks"]
        self.results = {key: copy.deepcopy(failed) for key in (
            "original-failure", "corrected-suite", "feature-still-present")}
        passed = self.results["corrected-suite"]
        passed.update(passed=True, status="passed", errors=[])
        for command in passed["commands"]:
            command.update(status="passed", exitCode=0, timedOut=False)
        for row in passed["featureCases"]:
            row.update(passed=True, outcomes=["passed"], errors=[], observations=[])
        for name, result in self.results.items():
            report = self.root / f"{name}.json"
            report.write_text('{"errors":[]}')
            result["featureReport"] = str(report)
        self.output = self.root / "output"

    def run_replay(self):
        with patch.object(sys, "argv", ["replay", "--output", str(self.output)]), \
             patch.object(replay, "baseline_patch", return_value=b"baseline comment"), \
             patch.object(replay, "check_candidate", side_effect=lambda *args, **kwargs:
                          copy.deepcopy(self.results[args[4].name])):
            return replay.main()

    def test_reproduction_pass_and_negative_control_produce_a_current_green_summary(self):
        self.assertEqual(self.run_replay(), 0)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertTrue(summary["passed"])
        self.assertEqual(len(summary["scenarios"]), 3)
        self.assertFalse(summary["scenarios"]["original-failure"]["passed"])
        self.assertTrue(summary["scenarios"]["corrected-suite"]["passed"])
        self.assertFalse(summary["scenarios"]["feature-still-present"]["passed"])

    def test_expected_failure_cannot_be_a_browser_process_timeout(self):
        for scenario in ("original-failure", "feature-still-present"):
            with self.subTest(scenario=scenario):
                row = next(row for row in self.results[scenario]["commands"] if row["name"] == "feature-browser")
                previous = copy.deepcopy(row)
                row.update(status="timeout", timedOut=True, exitCode=-9)
                with self.assertRaisesRegex(ValueError, "normal assertions"):
                    self.run_replay()
                self.assertFalse(json.loads((self.output / "summary.json").read_text())["passed"])
                row.update(previous)

    def test_expected_failure_cannot_hide_a_global_browser_error(self):
        Path(self.results["original-failure"]["featureReport"]).write_text(
            '{"errors":[{"message":"browser disconnected"}]}')
        with self.assertRaisesRegex(ValueError, "normal assertions"):
            self.run_replay()

    def test_failed_rerun_replaces_prior_green_summary(self):
        self.run_replay()
        self.results["corrected-suite"]["passed"] = False
        with self.assertRaisesRegex(ValueError, "did not pass every case"):
            self.run_replay()
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertFalse(summary["passed"])
        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["scenarios"]["corrected-suite"]["passed"])
        self.assertIn("did not pass every case", summary["error"])


if __name__ == "__main__":
    unittest.main()
