import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from feature_stages import FeatureStages, digest, final_json, validate_acceptance
from feature_cycle import validate_review


def final(turn="turn", value=None):
    return {"turn_id": turn, "type": "message", "role": "assistant", "phase": "final_answer",
            "status": "completed", "content": [{"type": "output_text", "text": json.dumps(value or {"ok": True})}]}


class StageTests(unittest.TestCase):
    def test_final_json_excludes_other_turns_and_incomplete_messages(self):
        self.assertEqual(final_json([final("old"), final("new")], "new"), {"ok": True})
        with self.assertRaises(ValueError):
            final_json([{**final(), "status": "in_progress"}], "turn")
        with self.assertRaises(ValueError):
            final_json([final(), final()], "turn")

    def test_acceptance_rejects_skip_network_and_process_escapes(self):
        cases = [{"title": f"[AC-{n % 7 + 1}] case {n}", "acceptanceIds": [f"AC-{n % 7 + 1}"],
                  "purpose": "Check a visible invariant"} for n in range(10)]
        spec = "import { test, expect } from '@playwright/test';\n" + "// bounded fixture\n" * 80
        value = {"schemaVersion": 1, "spec": spec, "cases": cases}
        validate_acceptance(value)
        for code in ("test.skip('x')", "test.only('x')", "fetch('https://invalid')",
                     "process.env.SECRET", "test.describe.configure({ retries: 2 })"):
            with self.assertRaises(ValueError):
                validate_acceptance({**value, "spec": spec + code})

    def test_review_cannot_approve_stale_candidate_or_blocking_findings(self):
        review = {"schemaVersion": 1, "patchSha256": "patch", "acceptanceSha256": "tests",
                  "summary": "Reviewed", "verdict": "approve", "findings": []}
        validate_review(review, "patch", "tests")
        with self.assertRaises(ValueError):
            validate_review(review, "different", "tests")
        finding = {"severity": "major", "path": "src/board.js", "line": 2,
                   "acceptanceIds": ["AC-2"], "title": "Done included", "evidence": "Actual line",
                   "requestedChange": "Exclude Done"}
        with self.assertRaises(ValueError):
            validate_review({**review, "findings": [finding]}, "patch", "tests")
        validate_review({**review, "verdict": "request_changes", "findings": [finding]}, "patch", "tests")

    def test_recorded_implementation_can_be_reconciled_after_later_corrections(self):
        with tempfile.TemporaryDirectory() as temporary:
            stages = FeatureStages.__new__(FeatureStages)
            stages.event, stages.base, stages.directory = "probe-test", "a" * 40, Path(temporary)
            stages.bucket = "bucket"
            prompt, instructions = "task", "instruction"
            identity = {"baseCommit": stages.base, "promptSha256": digest(prompt.encode()),
                        "instructionsSha256": digest(instructions.encode()), "sourceArchiveSha256": None}
            state = {"identity": identity, "sessionId": "session", "outcome": {"turnId": "first"}}
            stages.read = Mock(return_value=json.dumps(state).encode())
            stages.save = Mock()
            stages.client = Mock()
            stages.client.get_session.return_value = {"status": "idle"}
            stages.client.items.return_value = [final("first"), final("correction")]
            result, _ = stages.run("implementation", instructions, prompt)
            self.assertEqual(result["outcome"]["turnId"], "first")
            stages.client.create.assert_not_called()
            stages.client.run_turn.assert_not_called()

    def test_ambiguous_unrecorded_stage_does_not_submit_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            stages = FeatureStages.__new__(FeatureStages)
            stages.event, stages.base, stages.directory = "probe-test", "a" * 40, Path(temporary)
            stages.bucket = "bucket"
            identity = {"baseCommit": stages.base, "promptSha256": digest(b"task"),
                        "instructionsSha256": digest(b"instruction"), "sourceArchiveSha256": None}
            stages.read = Mock(return_value=json.dumps({"identity": identity, "sessionId": "session"}).encode())
            stages.client = Mock()
            stages.client.get_session.return_value = {"status": "idle"}
            stages.client.items.return_value = [final("one"), final("two")]
            with self.assertRaises(RuntimeError):
                stages.run("implementation", "instruction", "task")
            stages.client.run_turn.assert_not_called()

    def test_fifth_correction_is_rejected_before_any_service_call(self):
        stages = FeatureStages.__new__(FeatureStages)
        with self.assertRaises(ValueError):
            stages.continue_implementation(5, "session", "task", {})


if __name__ == "__main__":
    unittest.main()
