"""Offline feature-browser gate tests. No AWS sessions or browser runs."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import feature_browser as browser


EVENT = "probe-feature-browser-unit"
BASE = "a" * 40
ENV = {"BMA_ARTIFACT_BUCKET": "private", "BMA_EVIDENCE_BUCKET": "evidence",
       "BMA_PREVIEW_ORIGIN": "https://example.invalid", "AWS_REGION": "us-east-1",
       "BMA_BROWSER_ID": "browser", "BMA_MODEL": "model", "BMA_RUNTIME_ARN": "runtime",
       "BMA_SESSION_ROLE_ARN": "role"}


def plan_fixture():
    return {"schemaVersion": 1, "title": "Workload", "problem": "Capacity", "approach": "Show totals",
            "assumptions": [], "questions": [], "nonGoals": [],
            "requirements": [{"id": f"AC-{n}", "behavior": "Visible behavior", "verification": "UI"}
                             for n in range(1, 8)],
            "tasks": [{"title": "Implement", "detail": "Workload view",
                       "acceptanceIds": ["AC-1"], "paths": ["src/main.js"]}],
            "flow": [{"title": "Reassign", "detail": "Balance"}],
            "screens": [{"kind": kind, "title": kind, "caption": "Proposal", "html": "<p>Proposal</p>",
                         "css": "p{color:black}", "width": 800, "height": 600, "acceptanceIds": ["AC-1"]}
                        for kind in ("balanced", "mobile", "overloaded", "validation", "wireframe")]}


def candidate_fixture(patch_bytes=b"verified patch", plan=None, prd=b"Feature PRD"):
    plan = plan or plan_fixture()
    patch_sha, acceptance_sha = browser.digest(patch_bytes), "b" * 64
    return {"schemaVersion": 1, "passed": True, "attempt": 0, "baseCommit": BASE,
            "patchSha256": patch_sha, "acceptanceSha256": acceptance_sha, "contractSha256": "c" * 64,
            "planSha256": browser.digest(browser.canonical_json(plan)), "prdSha256": browser.digest(prd),
            "implementationSessionId": "implementation", "implementationTurnId": "implement-turn",
            "reviewSessionId": "reviewer", "reviewTurnId": "review-turn",
            "exportManifest": {"eventId": EVENT + "-implementation", "baseCommit": BASE},
            "checks": {"passed": True, "baseCommit": BASE, "patchSha256": patch_sha,
                       "acceptanceSha256": acceptance_sha, "errors": []},
            "review": {"schemaVersion": 1, "verdict": "approve", "patchSha256": patch_sha,
                       "acceptanceSha256": acceptance_sha, "findings": [], "summary": "Approved"}}


def descriptor_fixture():
    event = EVENT + "-browser-" + browser.digest(b"verified patch")[:12]
    return {"eventId": event, "baseCommit": BASE, "phase": "after", "sourceSha256": "d" * 64,
            "url": f"https://example.invalid/previews/{event}/after/index.html",
            "purpose": "feature", "taskKind": "feature"}


def action(kind, selector="", value="", **kwargs):
    return {"action": kind, "selector": selector, "value": value, "target": "", **kwargs}


def assertion(selector, expected):
    return {"action": "assert_text", "selector": selector, "expected": expected,
            "actual": expected, "passed": True}


def actions_fixture():
    return [action("click", "#reset"), action("click", browser.WORKLOAD),
            assertion(browser.LOADS["Maya"], "12 / 8 points"),
            assertion(browser.LOADS["Erez"], "4 / 8 points"),
            action("select", browser.OWNER, "Erez"),
            assertion(browser.LOADS["Maya"], "8 / 8 points"),
            assertion(browser.LOADS["Erez"], "8 / 8 points"),
            assertion("#announcement", "Give mobile some love reassigned to Erez."),
            action("click", browser.BOARD), assertion(browser.AVATAR, "EW"),
            action("reload"), action("click", browser.WORKLOAD),
            assertion(browser.LOADS["Maya"], "8 / 8 points"),
            assertion(browser.LOADS["Erez"], "8 / 8 points")]


def manifest_fixture():
    descriptor = descriptor_fixture()
    manifest = {key: descriptor[key] for key in ("eventId", "phase", "sourceSha256")}
    manifest.update({"browserSessionId": "agentcore-session", "outcome": "passed",
                     "previewUrl": descriptor["url"], "captureSha256": "e" * 64,
                     "screenshotSha256": "f" * 64, "summary": "Reassigned and verified reload",
                     "frameCount": 2, "duration": 20,
                     "actions": [{"action": "open"}, *actions_fixture(), {"action": "finish"}]})
    for seconds, row in enumerate(manifest["actions"]):
        row["seconds"] = seconds
    return manifest


def mcp(name, arguments, value, turn="turn"):
    return {"id": f"item-{name}", "type": "mcp_call", "name": name,
            "turn_id": turn, "server_label": "agentcore_browser", "status": "completed",
            "arguments": json.dumps(arguments),
            "output": {"isError": False, "content": [{"type": "text", "text": json.dumps(value)}]}}


def items_fixture(manifest=None):
    manifest = manifest or manifest_fixture()
    descriptor = descriptor_fixture()
    items = [mcp("browser_open", {"event": descriptor["eventId"], "phase": "after"},
                 {"url": descriptor["url"], "browserSessionId": manifest["browserSessionId"]})]
    for row in manifest["actions"][1:-1]:
        if row["action"] == "assert_text":
            items.append(mcp("browser_assert_text", {key: row[key] for key in ("selector", "expected")}, row))
        else:
            items.append(mcp("browser_action", {key: row[key] for key in ("action", "selector", "value", "target")},
                             {"url": descriptor["url"], "snapshot": "Observed UI"}))
    items.append(mcp("browser_finish", {"outcome": "passed", "summary": manifest["summary"]}, manifest))
    items.append({"id": "final", "type": "message", "role": "assistant", "phase": "final_answer",
                  "status": "completed", "turn_id": "turn", "content": []})
    return items


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.planning = self.root / "planning"
        self.planning.mkdir()
        self.patch = b"verified patch"
        self.plan = plan_fixture()
        self.candidate = candidate_fixture()
        self.provenance = {key: self.candidate[key] for key in ("baseCommit", "planSha256", "prdSha256")}
        self.provenance["eventId"] = "probe-design-unit"
        self.path = self.root / "candidate.json"
        self.write_inputs()
        self.canonical = patch.object(browser, "canonical_patch", return_value=self.patch).start()
        self.addCleanup(patch.stopall)

    def write_inputs(self):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(self.candidate))
        self.path.with_name("verified.patch").write_bytes(self.patch)
        (self.planning / "plan.json").write_text(json.dumps(self.plan))
        (self.planning / "provenance.json").write_text(json.dumps(self.provenance))
        (self.planning / "prd.md").write_bytes(b"Feature PRD")

    def validate(self):
        return browser.validate_inputs(EVENT, self.path, self.planning, self.root)

    def test_both_candidate_layouts_revalidate_the_exact_patch_and_preserve_identity(self):
        for path in (self.path, self.root / "candidate-2" / "result.json"):
            self.path = path
            self.write_inputs()
            candidate, patch_bytes, _, _, identity = self.validate()
            self.assertEqual(patch_bytes, self.patch)
            self.assertEqual(identity["candidateSha256"], browser.digest(browser.canonical_json(candidate)))
            self.assertEqual(identity["acceptanceSha256"], candidate["acceptanceSha256"])
            self.canonical.assert_called_with(self.root, BASE, self.patch)

    def test_stale_or_unapproved_candidate_is_rejected_before_preview(self):
        changes = [("passed", False), ("passed", 1), ("checks.passed", 1),
                   ("review.verdict", "request_changes"), ("review.patchSha256", "f" * 64),
                   ("checks.patchSha256", "f" * 64), ("checks.baseCommit", "f" * 40),
                   ("review.baseCommit", "f" * 40), ("checks.acceptanceSha256", "f" * 64),
                   ("review.acceptanceSha256", "f" * 64), ("acceptanceSha256", "bad"),
                   ("baseCommit", "short"), ("exportManifest.eventId", "probe-other-implementation"),
                   ("exportManifest.baseCommit", "f" * 40), ("eventId", "probe-other"),
                   ("review.findings", [{"severity": "major"}]), ("checks.errors", ["failed"]),
                   ("implementationSessionId", ""), ("planSha256", "f" * 64)]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.candidate = candidate_fixture()
                target = self.candidate
                parts = key.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
                self.write_inputs()
                with self.assertRaises(ValueError):
                    self.validate()

    def test_changed_patch_planning_and_noncanonical_patch_are_rejected(self):
        self.path.with_name("verified.patch").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "different digest"):
            self.validate()
        self.write_inputs()
        (self.planning / "prd.md").write_bytes(b"changed PRD")
        with self.assertRaisesRegex(ValueError, "prdSha256"):
            self.validate()
        self.write_inputs()
        self.provenance["baseCommit"] = "f" * 40
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "baseCommit"):
            self.validate()
        self.provenance["baseCommit"] = BASE
        self.plan["questions"] = ["Unresolved product question"]
        self.candidate["planSha256"] = self.provenance["planSha256"] = browser.digest(
            browser.canonical_json(self.plan))
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.validate()
        self.plan["questions"] = []
        self.candidate = candidate_fixture()
        self.provenance["planSha256"] = self.candidate["planSha256"]
        self.write_inputs()
        self.canonical.return_value = b"another canonical patch"
        with self.assertRaisesRegex(ValueError, "not canonical"):
            self.validate()

    def test_duplicate_json_and_symlinked_patch_are_rejected(self):
        self.path.write_text('{"passed":true,"passed":false}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.validate()
        self.write_inputs()
        self.path.with_name("verified.patch").unlink()
        self.path.with_name("verified.patch").symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.validate()

    def test_real_canonical_boundary_accepts_src_and_rejects_other_paths(self):
        from patch_boundary import canonical_patch
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "main.js").write_text("const value = 1;\n")
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}

        def git(*args):
            return subprocess.check_output(["git", "-C", str(repo), *args], env=env,
                                           stderr=subprocess.DEVNULL)

        git("init", "-q")
        git("add", "src/main.js")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
        base = git("rev-parse", "HEAD").decode().strip()
        (repo / "src" / "main.js").write_text("const value = 2;\n")
        candidate_patch = git("diff", "--no-ext-diff", "--no-renames")
        self.assertEqual(canonical_patch(repo, base, candidate_patch), candidate_patch)
        bad = b"diff --git a/other b/other\nnew file mode 100644\n--- /dev/null\n+++ b/other\n@@ -0,0 +1 @@\n+x\n"
        with self.assertRaisesRegex(ValueError, "src/"):
            canonical_patch(repo, base, bad)


class FeatureActionTests(unittest.TestCase):
    def test_required_feature_scenario_and_equivalent_quotes(self):
        actions = actions_fixture()
        for row in actions:
            row["selector"] = row.get("selector", "").replace('"', "'")
        self.assertIn("reload-persistence", browser.validate_feature_actions(actions))
        # Announcement can be observed before the numeric assertions.
        actions = actions_fixture()
        actions.insert(5, actions.pop(7))
        browser.validate_feature_actions(actions)

    def test_every_required_action_and_assertion_is_mandatory(self):
        for index in range(len(actions_fixture())):
            with self.subTest(index=index):
                actions = actions_fixture()
                del actions[index]
                with self.assertRaises(ValueError):
                    browser.validate_feature_actions(actions)

    def test_generic_pass_wrong_owner_and_fake_text_never_count(self):
        mutations = [
            (4, {"action": "click"}), (4, {"value": "Maya"}),
            (4, {"selector": "select"}), (2, {"selector": "body"}),
            (4, {"selector": 'select[data-owner-for="mobile"]'}),
            (4, {"selector": '#board select[data-owner-for="mobile"]'}),
            (2, {"actual": "12 / 8", "expected": "12 / 8"}),
            (2, {"actual": "999", "passed": True}),
            (7, {"actual": "Saved", "expected": "Saved"}),
            (9, {"actual": "MK", "expected": "MK"}), (10, {"action": "press"}),
            (13, {"expected": "4 / 8 points", "actual": "4 / 8 points"}),
        ]
        for index, fields in mutations:
            with self.subTest(index=index, fields=fields):
                actions = actions_fixture()
                actions[index].update(fields)
                with self.assertRaises(ValueError):
                    browser.validate_feature_actions(actions)
        with self.assertRaises(ValueError):
            browser.validate_feature_actions([action("click", "body"), assertion("body", "Passed")])

    def test_cannot_rebalance_after_reload_or_hide_failed_optional_assertion(self):
        actions = actions_fixture()
        actions.insert(12, action("select", browser.OWNER, "Erez"))
        with self.assertRaises(ValueError):
            browser.validate_feature_actions(actions)
        actions = actions_fixture() + [{**assertion("#search", "value"), "passed": False}]
        with self.assertRaises(ValueError):
            browser.validate_feature_actions(actions)


class DurableEvidenceTests(unittest.TestCase):
    def validate(self, items=None, manifest=None):
        manifest = manifest or manifest_fixture()
        return browser.validate_feature_calls(items or items_fixture(manifest), "turn",
                                               descriptor_fixture(), manifest)

    def test_correlated_manifest_passes_and_other_turns_do_not_count(self):
        self.validate()
        items = items_fixture()
        items[5]["turn_id"] = "other"
        with self.assertRaises(ValueError):
            self.validate(items)

    def test_each_durable_action_is_required_even_if_manifest_claims_it(self):
        original = items_fixture()
        for index in range(len(original) - 1):
            with self.subTest(index=index):
                items = copy.deepcopy(original)
                del items[index]
                with self.assertRaises(ValueError):
                    self.validate(items)

    def test_failed_spoofed_foreign_or_command_calls_are_rejected(self):
        mutations = [("status", "failed"), ("server_label", "other"),
                     ("arguments", json.dumps({"action": "click", "selector": browser.OWNER})),
                     ("output", {"isError": True, "content": []})]
        for key, value in mutations:
            with self.subTest(key=key):
                items = items_fixture()
                items[5][key] = value
                with self.assertRaises(ValueError):
                    self.validate(items)
        for kind in ("command_execution", "file_change", "function_call", "local_shell_call"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "non-browser"):
                self.validate(items_fixture() + [{"type": kind, "turn_id": "turn"}])

    def test_generic_passing_trace_and_changed_finish_are_rejected(self):
        manifest = manifest_fixture()
        manifest["actions"] = [manifest["actions"][0], action("click", "#reset"),
                               assertion("body", "Passed"), manifest["actions"][-1]]
        with self.assertRaises(ValueError):
            self.validate(manifest=manifest)
        items = items_fixture()
        value = json.loads(items[-2]["output"]["content"][0]["text"])
        value["screenshotSha256"] = "0" * 64
        items[-2]["output"]["content"][0]["text"] = json.dumps(value)
        with self.assertRaisesRegex(ValueError, "exact captured manifest"):
            self.validate(items)

    def test_argument_output_mismatch_or_second_recording_is_rejected(self):
        items = items_fixture()
        items[3]["arguments"] = json.dumps({"selector": "body", "expected": "12 / 8 points"})
        with self.assertRaisesRegex(ValueError, "durable arguments"):
            self.validate(items)
        items = items_fixture()
        items.insert(-1, copy.deepcopy(items[0]))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.validate(items)


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.writes = []

    def error(self, code):
        return ClientError({"Error": {"Code": code, "Message": code}}, "Fixture")

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise self.error("NoSuchKey")
        body, args = self.objects[Bucket, Key]
        return {"Body": io.BytesIO(body), "ETag": '"' + browser.digest(body) + '"',
                "ContentLength": len(body), **args}

    def head_object(self, Bucket, Key):
        result = self.get_object(Bucket, Key)
        result.pop("Body").close()
        return result

    def put_object(self, Bucket, Key, Body, **kwargs):
        prior = self.objects.get((Bucket, Key))
        if kwargs.get("IfNoneMatch") == "*" and prior:
            raise self.error("PreconditionFailed")
        if "IfMatch" in kwargs and (not prior or kwargs["IfMatch"] != '"' + browser.digest(prior[0]) + '"'):
            raise self.error("PreconditionFailed")
        body = Body if isinstance(Body, bytes) else Body.encode()
        attributes = {key: value for key, value in kwargs.items() if not key.startswith("If")}
        self.objects[Bucket, Key] = (body, attributes)
        self.writes.append((Bucket, Key, copy.deepcopy(kwargs), body))
        return {"ETag": '"' + browser.digest(body) + '"'}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.s3 = FakeS3()
        self.client = Mock()
        self.candidate = candidate_fixture()
        self.identity = {field: self.candidate[field] for field in browser.IDENTITIES}
        self.identity.update({"eventId": EVENT, "candidateSha256": "1" * 64,
                              "planningProvenanceSha256": "2" * 64})
        self.design = {"url": "https://example.invalid/design/index.html"}
        self.key = f"state/features/{EVENT}/browser.json"
        self.client.create.return_value = {"id": "verification"}
        self.client.get_session.return_value = {"status": "idle", "environment": {"runtime_arn": "runtime"}}
        self.client.items.return_value = items_fixture()
        self.client.run_turn.return_value = {"turnId": "turn"}
        self.preview = patch.object(browser, "preview", side_effect=self.build_preview).start()
        self.publish = patch.object(browser, "publish_feature_evidence", return_value={
            "passed": True, "browserEvidence": {"watchUrl": "https://example.invalid/watch"}}).start()
        self.addCleanup(patch.stopall)

    def build_preview(self, s3, assignment, phase, patch_bytes):
        self.assertEqual(phase, "after")
        self.assertEqual(patch_bytes, b"verified patch")
        self.assertEqual(assignment["baseCommit"], BASE)
        self.assertEqual(assignment["eventId"], descriptor_fixture()["eventId"])
        self.assertEqual(self.state()["status"], "preparing")
        value = descriptor_fixture()
        s3.put_object(Bucket="private", Key=f"sources/{assignment['eventId']}/browser-after.json",
                      Body=browser.canonical_json(value), ContentType="application/json")
        return value

    def state(self):
        return browser.json_object(self.s3.objects["private", self.key][0])

    def run_browser(self):
        return browser.run_browser(self.s3, self.client, self.candidate, b"verified patch",
                                   self.identity, self.design, self.directory)

    def test_session_identity_is_saved_before_submission_and_replay_does_not_resubmit(self):
        def submit(session, prompt, transcript):
            self.assertEqual(self.state()["sessionId"], "verification")
            self.assertEqual(self.state()["status"], "submitting")
            descriptor = self.state()["descriptor"]
            self.assertEqual(descriptor["purpose"], "feature")
            self.assertEqual(descriptor["taskKind"], "feature")
            self.assertIn("not support keyboard press", prompt)
            return {"turnId": "turn"}
        self.client.run_turn.side_effect = submit
        first = self.run_browser()
        self.assertEqual(self.run_browser(), first)
        self.client.create.assert_called_once()
        self.client.run_turn.assert_called_once()
        self.preview.assert_called_once()
        tools = self.client.create.call_args.args[0]["agent"]["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["server_label"], "agentcore_browser")
        self.assertEqual(self.state()["status"], "published")
        self.assertTrue((self.directory / "result.json").exists())
        assignment = self.s3.objects["private", f"sources/{descriptor_fixture()['eventId']}/feature-assignment.json"]
        self.assertEqual(browser.json_object(assignment[0])["patchSha256"], self.identity["patchSha256"])

    def test_uncertain_submission_is_reconciled_without_a_new_turn_or_recording(self):
        self.client.run_turn.side_effect = TimeoutError("submission response lost")
        with self.assertRaises(TimeoutError):
            self.run_browser()
        self.assertEqual(self.state()["status"], "submitting")
        self.run_browser()
        self.client.run_turn.assert_called_once()
        self.client.create.assert_called_once()
        self.preview.assert_called_once()
        self.assertEqual(self.state()["outcome"]["completionSource"], "durable_items")

    def test_uncertain_creation_never_creates_again(self):
        self.client.create.side_effect = TimeoutError("create response lost")
        with self.assertRaises(TimeoutError):
            self.run_browser()
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            self.run_browser()
        self.client.create.assert_called_once()
        self.client.run_turn.assert_not_called()
        self.preview.assert_called_once()

    def test_failed_running_or_ambiguous_turn_cannot_trigger_another_submission(self):
        self.client.run_turn.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.run_browser()
        for session, items in [
            ({"status": "running", "environment": {"runtime_arn": "runtime"}}, items_fixture()),
            ({"status": "idle", "error": "failed", "environment": {"runtime_arn": "runtime"}}, items_fixture()),
            ({"status": "idle", "environment": {"runtime_arn": "other"}}, items_fixture()),
            ({"status": "idle", "environment": {"runtime_arn": "runtime"}}, []),
            ({"status": "idle", "environment": {"runtime_arn": "runtime"}},
             items_fixture() + [{**items_fixture()[-1], "turn_id": "second"}]),
        ]:
            with self.subTest(session=session, item_count=len(items)):
                self.client.get_session.return_value, self.client.items.return_value = session, items
                with self.assertRaises(RuntimeError):
                    self.run_browser()
        self.client.run_turn.assert_called_once()
        self.client.create.assert_called_once()

    def test_identity_drift_is_rejected_and_publication_failure_can_only_recollect(self):
        self.publish.side_effect = RuntimeError("upload interrupted")
        with self.assertRaises(RuntimeError):
            self.run_browser()
        self.assertEqual(self.state()["status"], "completed")
        self.identity["acceptanceSha256"] = "9" * 64
        with self.assertRaisesRegex(ValueError, "different approved"):
            self.run_browser()
        self.identity["acceptanceSha256"] = self.candidate["acceptanceSha256"]
        self.publish.side_effect = None
        self.run_browser()
        self.client.run_turn.assert_called_once()
        self.preview.assert_called_once()
        self.assertEqual(self.publish.call_count, 2)

    def test_orphan_recording_and_uncertain_preview_are_never_overwritten(self):
        key = f"repairs/{descriptor_fixture()['eventId']}/browser/after/live.json"
        self.s3.put_object(Bucket="private", Key=key, Body=b"{}")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.run_browser()
        self.preview.assert_not_called()
        self.client.create.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            self.run_browser()

    def test_conditional_claim_and_state_updates_cannot_overwrite_other_controller(self):
        etag = browser.save_state(self.s3, "private", self.key, {"status": "preparing"})
        with self.assertRaises(ClientError):
            browser.save_state(self.s3, "private", self.key, {"status": "other"})
        browser.save_state(self.s3, "private", self.key, {"status": "creating"}, etag)
        with self.assertRaises(ClientError):
            browser.save_state(self.s3, "private", self.key, {"status": "submitting"}, etag)
        self.client.create.assert_not_called()


class PublicationTests(unittest.TestCase):
    @patch.dict(os.environ, ENV)
    def test_only_after_mp4_png_report_and_watch_page_are_published_immutably(self):
        s3 = FakeS3()
        candidate = candidate_fixture()
        identity = {key: candidate[key] for key in browser.IDENTITIES}
        identity.update({"eventId": EVENT, "candidateSha256": "1" * 64,
                         "planningProvenanceSha256": "2" * 64})
        state = {"descriptor": descriptor_fixture(), "identity": identity,
                 "origin": ENV["BMA_PREVIEW_ORIGIN"], "sessionId": "verification",
                 "outcome": {"turnId": "turn"}, "design": {"url": "https://example.invalid/design"}}
        manifest = manifest_fixture()
        manifest_key = f"repairs/{descriptor_fixture()['eventId']}/browser/after/manifest.json"
        s3.put_object(Bucket="private", Key=manifest_key, Body=browser.canonical_json(manifest))

        def collect(client, descriptor, destination):
            self.assertEqual(descriptor["phase"], "after")
            destination.mkdir()
            (destination / "capture.tar").write_bytes(b"raw stays private")
            (destination.parent / "after.mp4").write_bytes(b"mp4 fixture")
            (destination.parent / "after.png").write_bytes(b"png fixture")
            return manifest

        with tempfile.TemporaryDirectory() as temporary, patch.object(browser, "collect_phase", side_effect=collect) as collector:
            result = browser.publish_feature_evidence(s3, state, candidate, items_fixture(), Path(temporary))
            collector.assert_called_once()
            prefix = f"evidence/{EVENT}/{candidate['patchSha256']}/feature"
            self.assertEqual({key for bucket, key in s3.objects if bucket == "evidence"},
                             {f"{prefix}/{name}" for name in ("after.mp4", "after.png", "report.json", "index.html")})
            report = browser.json_object(s3.objects["evidence", f"{prefix}/report.json"][0])
            self.assertEqual(report["acceptanceSha256"], candidate["acceptanceSha256"])
            self.assertEqual(report["candidateSha256"], identity["candidateSha256"])
            self.assertEqual(report["approvedCandidate"]["reviewSessionId"], "reviewer")
            self.assertEqual(report["phases"], {"after": manifest})
            self.assertEqual(report["privateCapture"]["bucket"], "private")
            self.assertIn("not exercised", report["keyboardCoverage"])
            self.assertEqual(result["browserEvidence"]["watchUrl"],
                             ENV["BMA_PREVIEW_ORIGIN"] + "/" + prefix + "/index.html")
            for bucket, key, args, _ in s3.writes:
                if bucket == "evidence":
                    self.assertEqual(args["IfNoneMatch"], "*")
                    self.assertIn("immutable", args["CacheControl"])
                    self.assertEqual(args["Metadata"]["patch-sha256"], candidate["patchSha256"])
            # Re-collecting the same capture after an upload interruption is safe.
            browser.publish_feature_evidence(s3, state, candidate, items_fixture(), Path(temporary))
            self.assertEqual([path.name for path in Path(temporary).iterdir()], ["report.json"])

    def test_immutable_publication_checks_bytes_and_metadata_not_just_etag(self):
        s3 = FakeS3()
        browser.freeze(s3, "evidence", "report", b"same", "application/json", {"identity": "one"}, True)
        browser.freeze(s3, "evidence", "report", b"same", "application/json", {"identity": "one"}, True)
        for data, metadata in [(b"different", {"identity": "one"}), (b"same", {"identity": "two"})]:
            with self.assertRaisesRegex(ValueError, "Immutable"):
                browser.freeze(s3, "evidence", "report", data, "application/json", metadata, True)

    @patch.dict(os.environ, ENV)
    def test_manifest_changing_during_collection_blocks_all_publication(self):
        s3 = FakeS3()
        manifest = manifest_fixture()
        descriptor = descriptor_fixture()
        s3.put_object(Bucket="private", Key=f"repairs/{descriptor['eventId']}/browser/after/manifest.json",
                      Body=browser.canonical_json(manifest))
        candidate = candidate_fixture()
        identity = {**candidate, "eventId": EVENT, "candidateSha256": "1" * 64}
        state = {"descriptor": descriptor, "identity": identity, "outcome": {"turnId": "turn"},
                 "origin": ENV["BMA_PREVIEW_ORIGIN"]}
        with tempfile.TemporaryDirectory() as temporary, patch.object(browser, "collect_phase",
                return_value={**manifest, "captureSha256": "0" * 64}):
            with self.assertRaisesRegex(ValueError, "changed during collection"):
                browser.publish_feature_evidence(s3, state, candidate, items_fixture(), Path(temporary))
        self.assertFalse(any(bucket == "evidence" for bucket, _ in s3.objects))


class AcceptancePublicationTests(unittest.TestCase):
    @patch.dict(os.environ, ENV)
    def test_frozen_bundle_is_public_and_tampering_blocks_publication(self):
        candidate = candidate_fixture()
        spec, contract = b"frozen spec", b"frozen UI contract"
        candidate.update(acceptanceSha256=browser.digest(spec),
                         contractSha256=browser.digest(contract))
        manifest = {key: candidate[key] for key in
                    ("baseCommit", "acceptanceSha256", "contractSha256", "planSha256", "prdSha256")}
        manifest["eventId"] = EVENT
        s3 = FakeS3()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "manifest.json").write_bytes(browser.canonical_json(manifest))
            (directory / "workload.acceptance.spec.js").write_bytes(spec)
            (directory / "ui-contract.md").write_bytes(contract)
            key = browser.publish_acceptance_bundle(s3, EVENT, candidate, directory)
            value = browser.json_object(s3.objects["evidence", key][0])
            self.assertEqual(value, {"manifest": manifest, "spec": spec.decode(),
                                     "contract": contract.decode()})
            browser.publish_acceptance_bundle(s3, EVENT, candidate, directory)
            (directory / "workload.acceptance.spec.js").write_bytes(b"weakened test")
            writes = len(s3.writes)
            with self.assertRaisesRegex(ValueError, "differs"):
                browser.publish_acceptance_bundle(s3, EVENT, candidate, directory)
            self.assertEqual(len(s3.writes), writes)


if __name__ == "__main__":
    unittest.main()
