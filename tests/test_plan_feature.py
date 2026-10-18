"""Offline planning handoff and immutable design publication tests."""
import copy
from contextlib import chdir, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import plan_feature
from design_board import MAX_PLAN, save_board, validate_plan
from test_design_board import example_plan, preferences_plan, provenance, search_plan


PROSE_PRD = (
    "Help people locate tasks by title. Entering a title substring should show only "
    "matching tasks. Show No matching tasks when there are no results, and restore "
    "all tasks when the query is cleared. Searching must not edit the saved tasks."
)
EXPLICIT_PRD = (
    "Provide a comfortable display preference.\n"
    "AC-4: A user can toggle between a light and dark display.\n"
    "AC-12: Reloading the application keeps the selected display."
)


def result(plan):
    return {"type": "message", "role": "assistant", "phase": "final_answer",
            "status": "completed", "turn_id": "turn",
            "content": [{"type": "output_text", "text": json.dumps(plan)}]}


class PlannerPromptTests(unittest.TestCase):
    def prompt(self, prd=PROSE_PRD):
        return plan_feature.make_prompt("probe-search", "a" * 40, "test-bucket",
                                        "b" * 64, prd, "c" * 64)

    def test_schema_example_is_valid_generic_contract(self):
        validate_plan(copy.deepcopy(plan_feature.SCHEMA))
        self.assertEqual(plan_feature.SCHEMA["schemaVersion"], 2)

    def test_prompt_derives_criteria_without_business_rule_or_frame_hardcoding(self):
        prompt = self.prompt()
        self.assertTrue(prompt.endswith(PROSE_PRD))
        for expected in ("schemaVersion 2", "prose", "exactly once", "never renumber",
                         "1-50", "1-8 relevant", "uiContract", "browserJourney",
                         "assert_text", "pinned preview origin"):
            self.assertIn(expected, prompt)
        for forbidden in ("Workload", "Maya", "Erez", "AC-7", "overloaded", "balanced", "FIVE"):
            self.assertNotIn(forbidden, prompt)
        self.assertIn("--event probe-search", prompt)
        self.assertIn("--base " + "a" * 40, prompt)

    def test_scope_or_browser_gaps_are_material_questions_not_policy_expansion(self):
        prompt = self.prompt("AC-5: Upload an image to a new backend and open the external result.")
        for expected in ("outside src/", "material question", "affected criteria",
                         "dependencies", "backend", "workflows", "infrastructure",
                         "file upload/download", "external navigation",
                         "Do not fabricate", "Do not silently drop"):
            self.assertIn(expected, prompt)
        self.assertIn("No scripts, event handlers, external assets", prompt)
        self.assertIn("Keep total response below 100KB", prompt)

    def test_native_recording_is_a_primary_scenario_while_playwright_covers_every_criterion(self):
        prompt = self.prompt()
        self.assertIn("primary visual scenario", prompt)
        self.assertIn("subset of the plan's acceptance IDs", prompt)
        self.assertIn("Independent Playwright acceptance covers every criterion, including keyboard", prompt)
        self.assertIn("these limitations alone are not material product questions", prompt)
        self.assertIn("absence of any honest supported primary journey", prompt)
        self.assertNotIn("assertions covering every criterion", prompt)

    def test_unrelated_prds_bind_to_their_own_criteria_and_journeys(self):
        for prd, plan in ((PROSE_PRD, search_plan()), (EXPLICIT_PRD, preferences_plan())):
            with self.subTest(prd=prd):
                collected = plan_feature.validate_planning_result([result(plan)], "turn", prd)
                self.assertEqual(collected, plan)
        with self.assertRaisesRegex(ValueError, "explicit PRD"):
            plan_feature.validate_planning_result([result(search_plan())], "turn", EXPLICIT_PRD)

    def test_explicit_ids_cannot_be_renumbered_dropped_or_added(self):
        for prd in (
            EXPLICIT_PRD + "\nAC-25: Restore the default on request.",
            EXPLICIT_PRD.replace("AC-12", "AC-13"),
            "AC-4: A user can toggle between a light and dark display.",
        ):
            with self.subTest(prd=prd), self.assertRaisesRegex(ValueError, "explicit PRD"):
                plan_feature.validate_planning_result([result(preferences_plan())], "turn", prd)

    def test_new_runs_reject_legacy_outputs_but_explicit_legacy_reconcile_works(self):
        plan = example_plan()
        with self.assertRaisesRegex(ValueError, "claimed plan schema"):
            plan_feature.validate_planning_result([result(plan)], "turn", PROSE_PRD)
        self.assertEqual(
            plan_feature.validate_planning_result([result(plan)], "turn", PROSE_PRD, 1), plan)

    def test_oversized_wrong_turn_or_ambiguous_output_cannot_be_collected(self):
        item = result(search_plan())
        for items, turn in (([item], "another"), ([item, copy.deepcopy(item)], "turn")):
            with self.assertRaises(ValueError):
                plan_feature.validate_planning_result(items, turn, PROSE_PRD)
        item["content"][0]["text"] = "x" * (MAX_PLAN + 1)
        with self.assertRaisesRegex(ValueError, "Oversized"):
            plan_feature.validate_planning_result([item], "turn", PROSE_PRD)


class ImmutableDesignPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.metadata = save_board(search_plan(), provenance(), self.root)
        self.env = patch.dict(os.environ, {
            "BMA_PREVIEW_ORIGIN": "https://example.invalid",
            "BMA_EVIDENCE_BUCKET": "test-evidence",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_generic_publication_retains_conditional_immutable_writes(self):
        s3 = Mock()
        url = plan_feature.publish(s3, self.root, self.metadata, PROSE_PRD.encode())
        self.assertIn("/design-" + self.metadata["planSha256"][:16] + "-", url)
        self.assertEqual(s3.put_object.call_count, 4)
        for call in s3.put_object.call_args_list:
            self.assertEqual(call.kwargs["IfNoneMatch"], "*")
            self.assertEqual(call.kwargs["CacheControl"], "public,max-age=31536000,immutable")
            self.assertEqual(call.kwargs["Body"],
                             (self.root / call.kwargs["Key"].rsplit("/", 1)[1]).read_bytes())
        s3.get_object.assert_not_called()

    def test_same_bytes_are_idempotent_but_changed_immutable_bytes_fail_and_close_streams(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                s3, streams = Mock(), []
                s3.put_object.side_effect = ClientError(
                    {"Error": {"Code": "PreconditionFailed"}}, "PutObject")

                def read_existing(**kwargs):
                    body = (self.root / kwargs["Key"].rsplit("/", 1)[1]).read_bytes()
                    stream = io.BytesIO(body + b"different" if changed else body)
                    streams.append(stream)
                    return {"Body": stream}

                s3.get_object.side_effect = read_existing
                if changed:
                    with self.assertRaisesRegex(ValueError, "immutable design artifact differs"):
                        plan_feature.publish(s3, self.root, self.metadata, PROSE_PRD.encode())
                else:
                    plan_feature.publish(s3, self.root, self.metadata, PROSE_PRD.encode())
                    self.assertEqual(len(streams), 4)
                self.assertTrue(streams)
                self.assertTrue(all(stream.closed for stream in streams))

    def test_aws_failures_propagate_without_reading_or_replacing_existing_artifacts(self):
        s3 = Mock()
        s3.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        with self.assertRaises(ClientError):
            plan_feature.publish(s3, self.root, self.metadata, PROSE_PRD.encode())
        s3.get_object.assert_not_called()
        self.assertEqual(s3.put_object.call_count, 1)


class OfflinePlannerRunTests(unittest.TestCase):
    """Exercise checkpoint/status wiring with no AWS or BMA requests."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = search_plan()
        self.prd = PROSE_PRD
        self.base, self.event = "a" * 40, "probe-search"
        self.output = self.root / "artifacts" / self.event
        self.client, self.s3 = Mock(), Mock()
        self.client.create.return_value = {"id": "session"}
        self.client.run_turn.return_value = {"turnId": "turn", "completionSource": "durable_items"}
        self.client.get_session.return_value = {"status": "idle"}
        self.aws = Mock()
        self.aws.client.return_value = self.s3

    def run_planner(self, reconcile=False):
        source = self.root / "prd.md"
        source.write_text(self.prd)
        self.client.items.return_value = [
            {"type": "command_execution", "turn_id": "turn", "status": "completed",
             "exit_code": 0, "output": json.dumps({
                 "repository": f"/mnt/home/workspace/jobs/{self.event}/repo",
                 "base": self.base, "persistenceMarker": "prepared",
             })}, result(self.plan)]
        args = ["plan_feature.py", "--prd", str(source), "--base", self.base, "--event", self.event]
        if reconcile:
            args.append("--reconcile")
        with chdir(self.root), redirect_stdout(io.StringIO()), patch.dict(os.environ, {
            "AWS_REGION": "us-east-1", "BMA_ARTIFACT_BUCKET": "test-artifacts",
            "BMA_MODEL": "test-model", "BMA_RUNTIME_ARN": "test-runtime",
            "BMA_SESSION_ROLE_ARN": "test-role",
        }), patch.object(sys, "argv", args), \
                patch.object(plan_feature.boto3, "Session", return_value=self.aws), \
                patch.object(plan_feature, "BmaClient", return_value=self.client), \
                patch.object(plan_feature.subprocess, "check_output", return_value=b"source archive"), \
                patch.object(plan_feature, "publish") as publish:
            code = plan_feature.main()
            publish.assert_not_called()
            return code

    def test_new_run_pins_generic_schema_and_saves_the_actual_plan(self):
        self.assertEqual(self.run_planner(), 0)
        state = json.loads((self.output / "planning-state.json").read_bytes())
        self.assertEqual(state["schemaVersion"], 2)
        self.assertEqual(state["planSchemaVersion"], 2)
        self.assertEqual(state["status"], "planned")
        self.assertEqual(json.loads((self.output / "design/plan.json").read_bytes()), self.plan)
        claim = json.loads(self.s3.put_object.call_args_list[0].kwargs["Body"])
        self.assertEqual(claim["planSchemaVersion"], 2)
        self.assertEqual(self.s3.put_object.call_args_list[0].kwargs["IfNoneMatch"], "*")
        self.client.create.assert_called_once()
        self.client.run_turn.assert_called_once()

    def test_blocked_scope_saves_questions_with_empty_contract_and_journey(self):
        self.plan["questions"] = ["AC-1 needs a new authenticated backend. "
                                 "How should the source and browser scope be resolved?"]
        self.plan["uiContract"], self.plan["browserJourney"] = "", []
        self.assertEqual(self.run_planner(), 0)
        state = json.loads((self.output / "planning-state.json").read_bytes())
        self.assertEqual(state["status"], "questions")
        self.assertEqual(json.loads((self.output / "design/plan.json").read_bytes()), self.plan)

    def test_legacy_reconcile_collects_without_resubmitting_or_upgrading(self):
        self.plan = example_plan()
        self.output.mkdir(parents=True)
        (self.output / "planning-state.json").write_text(json.dumps({
            "schemaVersion": 1, "eventId": self.event, "baseCommit": self.base,
            "prdSha256": hashlib.sha256(self.prd.encode()).hexdigest(),
            "sessionId": "legacy-session", "turnId": "turn",
        }))
        self.assertEqual(self.run_planner(reconcile=True), 0)
        state = json.loads((self.output / "planning-state.json").read_bytes())
        self.assertEqual(state["schemaVersion"], 1)
        self.assertEqual(state["status"], "planned")
        self.client.create.assert_not_called()
        self.client.run_turn.assert_not_called()

    def test_new_run_cannot_save_downgraded_or_wrong_prd_output(self):
        self.plan = example_plan()
        with self.assertRaisesRegex(ValueError, "claimed plan schema"):
            self.run_planner()
        self.assertFalse((self.output / "design").exists())
        self.plan = search_plan()
        self.prd = EXPLICIT_PRD
        # Reuse the test's fake completed session with its generic schema claim.
        state = json.loads((self.output / "planning-state.json").read_bytes())
        state["prdSha256"] = hashlib.sha256(self.prd.encode()).hexdigest()
        (self.output / "planning-state.json").write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "explicit PRD"):
            self.run_planner(reconcile=True)
        self.assertFalse((self.output / "design").exists())


if __name__ == "__main__":
    unittest.main()
