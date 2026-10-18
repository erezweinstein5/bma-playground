import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from feature_stages import FeatureStages, acceptance_prompt, digest, final_json, validate_acceptance
from feature_cycle import validate_check_binding, validate_review


def final(turn="turn", value=None):
    return {"turn_id": turn, "type": "message", "role": "assistant", "phase": "final_answer",
            "status": "completed", "content": [{"type": "output_text", "text": json.dumps(value or {"ok": True})}]}


class StageTests(unittest.TestCase):
    def test_cached_success_cannot_skip_frozen_cases_or_claim_empty_checks(self):
        from feature_checks import parse_feature_report
        from test_feature_checks import report_fixture, specs
        report = report_fixture(count=2, project_names=("desktop",))
        ids = ["AC-4", "AC-12"]
        for row, ref in zip(specs(report), ids):
            row["title"] = f"[{ref}] observable preference"
        titles = [row["title"] for row in specs(report)]
        commands = ["canonical-patch", "clone", "checkout-base", "verify-base",
                    "apply-candidate", "protected-inputs", "npm-ci", "npm-test",
                    "npm-build", "board-browser", "repair-browser", "feature-browser"]
        checks = {**parse_feature_report(report, expected_titles=titles, required_ids=ids),
                  "baseCommit": "base", "patchSha256": "patch", "acceptanceSha256": "spec",
                  "actualAcceptanceSha256": "spec", "status": "passed",
                  "infrastructureFailure": False,
                  "commands": [{"name": name, "status": "passed", "exitCode": 0,
                                "timedOut": False} for name in commands]}
        manifest = {"acceptanceSha256": "spec", "cases": [{"title": title} for title in titles]}
        validate_check_binding(checks, "base", "patch", manifest, ids)
        for change in ({"commands": []}, {"featureCases": []},
                       {"coveredAcceptanceIds": []}, {"executedFeatureCases": 0},
                       {"actualAcceptanceSha256": "different"}, {"patchSha256": "other"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_check_binding({**checks, **change}, "base", "patch", manifest, ids)

    def test_invalid_handoff_stops_before_cloud_stage_construction(self):
        import prepare_feature_acceptance
        import feature_cycle
        from design_board import canonical_json
        from test_design_board import preferences_plan
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            plan, prd = preferences_plan(), "AC-4: Change the display. AC-12: Save it."
            provenance = {"schemaVersion": 2, "planSchemaVersion": 2,
                          "eventId": "probe-original", "baseCommit": "a" * 40,
                          "status": "planned", "planSha256": digest(canonical_json(plan)),
                          "prdSha256": digest(prd.encode())}
            (folder / "plan.json").write_bytes(canonical_json(plan))
            (folder / "provenance.json").write_text(json.dumps(provenance))
            (folder / "prd.md").write_text(prd)
            for module in (prepare_feature_acceptance, feature_cycle):
                with self.subTest(module=module.__name__), patch.object(module, "FeatureStages") as stages:
                    with patch.object(sys, "argv", ["stage", "--planning", str(folder),
                                                   "--event", "probe-foreign"]):
                        with self.assertRaises(ValueError):
                            module.main()
                    stages.assert_not_called()

    def test_generic_acceptance_uses_the_complete_pinned_criteria_not_seven(self):
        from test_design_board import preferences_plan
        plan = preferences_plan()
        prompt = acceptance_prompt("Display preferences", plan, plan["uiContract"])
        self.assertNotIn("Workload", prompt)
        self.assertIn("schemaVersion=2", prompt)
        value = {
            "schemaVersion": 2, "acceptanceIds": ["AC-4", "AC-12"],
            "spec": "import { test, expect } from '@playwright/test';\n"
                    "test('[AC-4] [AC-12] toggle and reload', async ({ page }) => {\n"
                    "  await page.goto('/');\n"
                    "  await expect(page.locator('#theme')).toBeVisible();\n});",
            "cases": [{"title": "[AC-4] [AC-12] toggle and reload",
                       "acceptanceIds": ["AC-4", "AC-12"], "purpose": "Observe display state"}],
        }
        self.assertEqual(validate_acceptance(value, {"AC-4", "AC-12"}), value)
        observer = "\npage.on('request', request => observed.push(request.url()));"
        validate_acceptance({**value, "spec": value["spec"] + observer}, {"AC-4", "AC-12"})
        for forbidden in ("request.get('/api')", "request.post('/api')",
                          "page.request.get('/api')", "route.fulfill({body: 'fake'})"):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                validate_acceptance({**value, "spec": value["spec"] + forbidden},
                                    {"AC-4", "AC-12"})
        for ids in ({"AC-1", "AC-2"}, {"AC-4"}, {"AC-4", "AC-12", "AC-30"}):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_acceptance(value, ids)
        with self.assertRaises(ValueError):
            validate_acceptance({**value, "schemaVersion": 1}, {"AC-4", "AC-12"})
        with self.assertRaises(ValueError):
            validate_acceptance(value)
        with self.assertRaises(ValueError):
            validate_acceptance({**value, "cases": [{**value["cases"][0],
                                 "acceptanceIds": ["AC-4"]}]}, {"AC-4", "AC-12"})

    def test_review_accepts_only_current_prd_criteria(self):
        finding = {"severity": "major", "path": "src/main.js", "line": 2,
                   "acceptanceIds": ["AC-12"], "title": "Preference lost",
                   "evidence": "Reload discards the selected display",
                   "requestedChange": "Persist the selected display"}
        review = {"schemaVersion": 1, "patchSha256": "patch", "acceptanceSha256": "tests",
                  "summary": "Reviewed", "verdict": "request_changes", "findings": [finding]}
        validate_review(review, "patch", "tests", {"AC-4", "AC-12"})
        with self.assertRaises(ValueError):
            validate_review(review, "patch", "tests", {"AC-1", "AC-2"})
        with self.assertRaises(ValueError):
            validate_review(review, "patch", "tests")

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
