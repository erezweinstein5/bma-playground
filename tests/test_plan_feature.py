"""Offline planning handoff and immutable design publication tests."""
import copy
from contextlib import chdir, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import plan_feature
from design_board import MAX_PLAN, save_board, validate_plan
from test_design_board import example_plan, preferences_plan, provenance, search_plan
from test_feature_intake import MemoryS3


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
        self.client, self.s3 = Mock(), MemoryS3()
        self.client.create.return_value = {"id": "session"}
        self.client.run_turn.return_value = {"turnId": "turn", "completionSource": "durable_items"}
        self.client.get_session.return_value = {"status": "idle"}
        self.aws = Mock()
        self.aws.client.return_value = self.s3
        # Each fixture supplies its own event/run identity. In Actions, inherited
        # GITHUB_EVENT_PATH and GITHUB_RUN_ID would otherwise turn an offline
        # probe into recovery of the CI run itself.
        fixture_env = {key: value for key, value in os.environ.items()
                       if not key.startswith("GITHUB_")}
        self.env = patch.dict(os.environ, {**fixture_env, "GITHUB_RUN_ATTEMPT": "1"},
                              clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_planner(self, reconcile=False, publish_plan=False, source=None,
                    requested_base=None, items=None):
        source = source or self.root / "prd.md"
        source.write_text(self.prd)
        self.client.items.return_value = items if items is not None else [
            {"type": "command_execution", "turn_id": "turn", "status": "completed",
             "exit_code": 0, "output": json.dumps({
                 "repository": f"/mnt/home/workspace/jobs/{self.event}/repo",
                 "base": self.base, "persistenceMarker": "prepared",
             })}, result(self.plan)]
        args = ["plan_feature.py", "--prd", str(source), "--base", requested_base or self.base,
                "--event", self.event]
        if reconcile:
            args.append("--reconcile")
        if publish_plan:
            args.append("--publish")
        self.stdout = io.StringIO()
        with chdir(self.root), redirect_stdout(self.stdout), patch.dict(os.environ, {
            "AWS_REGION": "us-east-1", "BMA_ARTIFACT_BUCKET": "test-artifacts",
            "BMA_EVIDENCE_BUCKET": "test-evidence", "BMA_PREVIEW_ORIGIN": "https://example.invalid",
            "BMA_MODEL": "test-model", "BMA_RUNTIME_ARN": "test-runtime",
            "BMA_SESSION_ROLE_ARN": "test-role",
        }), patch.object(sys, "argv", args), \
                patch.object(plan_feature.boto3, "Session", return_value=self.aws), \
                patch.object(plan_feature, "BmaClient", return_value=self.client), \
                patch.object(plan_feature.subprocess, "check_output", return_value=b"source archive"), \
                patch.object(plan_feature, "publish", wraps=plan_feature.publish) as publish:
            code = plan_feature.main()
            if not publish_plan:
                publish.assert_not_called()
            return code

    def save_claim(self, state):
        self.s3.put(Bucket="test-artifacts", Key=f"state/features/{self.event}/planning.json",
                    Body=json.dumps(state).encode())

    def claiming_state(self, **extra):
        return {"schemaVersion": 2, "planSchemaVersion": 2, "eventId": self.event,
                "baseCommit": self.base, "prdSha256": hashlib.sha256(self.prd.encode()).hexdigest(),
                "sourceArchiveSha256": "b" * 64, "status": "claiming", **extra}

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
        configuration = self.client.create.call_args.args[0]
        self.assertEqual(configuration["environment"]["capability_directories"],
                         ["/opt/bma/plugins/bma-design/skills"])
        self.assertIn("$bma-design", configuration["agent"]["instructions"])
        self.assertNotIn("bma-review", json.dumps(configuration))
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
        self.s3.put(Bucket="test-artifacts", Key=f"state/features/{self.event}/planning.json",
                    Body=(self.output / "planning-state.json").read_bytes())
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
        self.save_claim(state)
        with self.assertRaisesRegex(ValueError, "explicit PRD"):
            self.run_planner(reconcile=True)
        self.assertFalse((self.output / "design").exists())

    def test_duplicate_after_main_advances_skips_without_downstream_files_or_writes(self):
        self.run_planner()
        shutil.rmtree(self.output)
        self.client.reset_mock()
        self.s3.put_object.reset_mock()
        output, summary = self.root / "github-output", self.root / "summary"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}):
            self.assertEqual(self.run_planner(requested_base="c" * 40), 0)
        self.assertIn("status=skipped\nready=false\nbase_commit=" + self.base, output.read_text())
        self.assertIn("rerun the original GitHub run", summary.read_text())
        self.assertFalse((self.output / "planning-state.json").exists())
        self.assertFalse((self.output / "design").exists())
        self.client.create.assert_not_called()
        self.client.run_turn.assert_not_called()
        self.s3.put_object.assert_not_called()

    def test_duplicate_race_obeys_conditional_claim_and_validates_the_winner(self):
        def winner(**kwargs):
            self.save_claim(self.claiming_state(baseCommit="c" * 40))
            return self.s3.put(**kwargs)
        self.s3.put_object.side_effect = winner
        self.assertEqual(self.run_planner(), 0)
        self.assertIn("Duplicate feature trigger skipped", self.stdout.getvalue())
        self.assertEqual(self.s3.put_object.call_count, 1)
        self.client.create.assert_not_called()
        self.client.run_turn.assert_not_called()
        self.assertFalse((self.output / "planning-state.json").exists())

    def test_conflict_without_a_readable_winner_fails_without_retrying_creation(self):
        self.s3.put_object.side_effect = ClientError(
            {"Error": {"Code": "ConditionalRequestConflict"}}, "PutObject")
        with self.assertRaisesRegex(RuntimeError, "without a readable winner"):
            self.run_planner()
        self.client.create.assert_not_called()
        self.assertEqual(self.s3.put_object.call_count, 1)

    def test_mismatched_prd_event_or_schema_is_not_treated_as_a_duplicate(self):
        for fields in ({"prdSha256": "f" * 64}, {"eventId": "issue-feature-other"},
                       {"schemaVersion": 1}, {"planSchemaVersion": True}):
            self.save_claim(self.claiming_state(**fields))
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.run_planner()
        self.client.create.assert_not_called()
        self.client.run_turn.assert_not_called()
        self.s3.put_object.assert_not_called()

    def test_completed_published_retry_preserves_terminal_provenance_and_exact_design_bytes(self):
        self.client.run_turn.return_value = {
            "turnId": "turn", "terminalEvent": "agent.session.turn.completed"}
        self.run_planner(publish_plan=True)
        original = {p.relative_to(self.output): p.read_bytes()
                    for p in self.output.rglob("*") if p.is_file()}
        shutil.rmtree(self.output)
        self.client.reset_mock()
        self.s3.put_object.reset_mock()
        with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}):
            self.assertEqual(self.run_planner(publish_plan=True), 0)
        for path, raw in original.items():
            if path.parts[0] == "design" or str(path) == "planning-state.json":
                self.assertEqual((self.output / path).read_bytes(), raw)
        provenance = json.loads((self.output / "design/provenance.json").read_bytes())
        self.assertNotIn("completionSource", provenance)
        self.assertEqual(provenance["terminalEvent"], "agent.session.turn.completed")
        self.client.create.assert_not_called()
        self.client.run_turn.assert_not_called()
        self.s3.put_object.assert_not_called()

    def test_explicit_rerun_with_no_durable_claim_never_starts_a_session(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}), \
                self.assertRaisesRegex(RuntimeError, "No durable planning claim"):
            self.run_planner()
        self.s3.put_object.assert_not_called()
        self.client.create.assert_not_called()
        self.client.run_turn.assert_not_called()

    def test_unknown_session_creation_is_durable_and_cannot_be_replayed(self):
        self.client.create.side_effect = TimeoutError("response lost")
        with self.assertRaises(TimeoutError):
            self.run_planner()
        state = json.loads(self.s3.objects[(
            "test-artifacts", f"state/features/{self.event}/planning.json")][0])
        self.assertEqual(state["status"], "creating_session")
        self.client.reset_mock()
        self.s3.put_object.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "Session creation was uncertain"):
            self.run_planner(reconcile=True)
        self.client.create.assert_not_called()
        self.client.run_turn.assert_not_called()
        self.s3.put_object.assert_not_called()

    def test_reconcile_refuses_failed_busy_incomplete_or_ambiguous_sessions(self):
        state = self.claiming_state(status="submitting", sessionId="session")
        self.save_claim(state)
        for session in ({"status": "running"}, {"status": "idle", "error": {"message": "failed"}}):
            self.client.get_session.return_value = session
            with self.subTest(session=session), self.assertRaisesRegex(RuntimeError, "not idle"):
                self.run_planner(reconcile=True)
        self.client.get_session.return_value = {"status": "idle"}
        for extra in (
            {"type": "message", "turn_id": "another", "status": "completed"},
            {"type": "command_execution", "turn_id": "turn", "status": "failed"},
            {"type": "command_execution", "turn_id": "turn", "status": "incomplete"},
            {"type": "agent.session.turn.cancelled", "turn_id": "turn"},
        ):
            with self.subTest(extra=extra), self.assertRaisesRegex(RuntimeError, "unambiguous"):
                self.run_planner(reconcile=True, items=[result(self.plan), extra])
        self.client.create.assert_not_called()
        self.client.run_turn.assert_not_called()
        self.s3.put_object.assert_not_called()

    def test_reconcile_refuses_changed_turn_and_changed_pinned_plan(self):
        self.run_planner()
        state = json.loads((self.output / "planning-state.json").read_bytes())
        for fields, message in (({"turnId": "different"}, "pinned turn"),
                                ({"planSha256": "f" * 64}, "pinned hash")):
            self.save_claim({**state, **fields})
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, message):
                self.run_planner(reconcile=True)

    def test_old_yaml_command_restores_adjacent_assignment_and_base_after_main_advances(self):
        self.event = "issue-feature-123"
        self.output = self.root / "custom-assignment-location"
        self.output.mkdir()
        event = {"action": "opened", "sender": {"login": "operator", "type": "User"},
                 "repository": {"full_name": "owner/repo"},
                 "issue": {"id": 123, "number": 21, "state": "open", "title": "Task search",
                           "body": self.prd, "labels": [{"name": "agent-feature"}]}}
        self.prd = f'# {event["issue"]["title"]}\n\n{self.prd}\n'
        assignment = {"schemaVersion": 2, "kind": "prd-feature-v1", "repository": "owner/repo",
                      "issueNumber": 21, "eventId": self.event, "actor": "operator",
                      "baseCommit": self.base, "title": "Task search", "prd": self.prd,
                      "prdSha256": hashlib.sha256(self.prd.encode()).hexdigest(), "status": "prepared"}
        assignment_path = self.output / "assignment.json"
        assignment_path.write_text(json.dumps(assignment))
        source = self.output / "prd.md"
        self.run_planner(source=source, publish_plan=True)
        state = json.loads((self.output / "planning-state.json").read_bytes())
        # Issue 21 predates embedding the assignment in the claim.
        # Its original event and PRD digest remain authoritative.
        self.assertEqual(state["assignment"], assignment)
        original_design = {p.name: p.read_bytes() for p in (self.output / "design").iterdir()}
        shutil.rmtree(self.output / "design")
        assignment_path.write_text(json.dumps({**assignment, "baseCommit": "c" * 40}))
        event_path = self.root / "event.json"
        event_path.write_text(json.dumps(event))
        self.client.reset_mock()
        self.s3.put_object.reset_mock()
        with patch.dict(os.environ, {
            "GITHUB_RUN_ATTEMPT": "2", "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_EVENT_NAME": "issues", "DEMO_OPERATORS": "",
            "GITHUB_RUN_ID": "original-run", "SOURCE_COMMIT": "c" * 40,
        }), patch.object(plan_feature, "ensure_pinned_source") as fetch:
            # This is the original YAML invocation: no new flag or helper step.
            self.assertEqual(self.run_planner(source=source, requested_base="c" * 40,
                                              publish_plan=True), 0)
        fetch.assert_called_once_with(self.base)
        self.assertEqual(json.loads(assignment_path.read_bytes()), assignment)
        self.assertEqual(json.loads((self.output / "planning-state.json").read_bytes()), state)
        # Evidence recovery does not rebase the candidate or bypass freshness:
        # after main advances, publication still requires a new assignment.
        from publish_feature_pr import current_policy
        github = Mock()
        github.request.return_value = {"object": {"sha": "c" * 40}}
        with self.assertRaisesRegex(ValueError, "Main changed; start a new feature assignment"):
            current_policy(github, {"identity": assignment}, "Repair acceptance")
        for name, raw in original_design.items():
            self.assertEqual((self.output / "design" / name).read_bytes(), raw)
        self.client.create.assert_not_called()
        self.client.run_turn.assert_not_called()
        self.s3.put_object.assert_not_called()


class PinnedSourceRecoveryTests(unittest.TestCase):
    def test_fetches_only_the_restored_full_sha_when_missing_from_shallow_checkout(self):
        with patch.object(plan_feature.subprocess, "run") as run:
            run.side_effect = [subprocess.CompletedProcess([], 1),
                               subprocess.CompletedProcess([], 0),
                               subprocess.CompletedProcess([], 0)]
            plan_feature.ensure_pinned_source("a" * 40)
        self.assertEqual(run.call_args_list[1].args[0],
                         ["git", "fetch", "--no-tags", "--depth=1", "origin", "a" * 40])
        self.assertEqual(run.call_args_list[2].args[0],
                         ["git", "cat-file", "-e", "a" * 40 + "^{commit}"])

    def test_available_commit_does_not_fetch(self):
        with patch.object(plan_feature.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0)) as run:
            plan_feature.ensure_pinned_source("a" * 40)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
