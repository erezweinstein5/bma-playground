"""Offline exact-revision gate tests; HTTP and browser execution are stubbed."""
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import feature_delivery as gate


BASE, HEAD = "a" * 40, "b" * 40
REPO, ORIGIN, LIVE = "demo/playground", "https://evidence.example.com", "https://app.example.com"


def fixture(base=BASE, head=HEAD, candidate=b"patch"):
    cases = [{"title": f"case {n} [AC-{n % 7 + 1}]", "acceptanceIds": [f"AC-{n % 7 + 1}"],
              "purpose": "Verify an observable behavior"} for n in range(10)]
    spec = "import { test, expect } from '@playwright/test';\n" + "\n".join(
        f"test({json.dumps(row['title'])}, async ({{page}}) => {{\n"
        "  await page.goto('/');\n  await expect(page.locator('#board')).toBeVisible();\n});"
        for row in cases)
    contract = "The selected view is visually displayed; other views are hidden.\n"
    hashes = {"patchSha256": gate.digest(candidate), "acceptanceSha256": gate.digest(spec.encode()),
              "contractSha256": gate.digest(contract.encode()), "planSha256": "3" * 64, "prdSha256": "4" * 64}
    metadata = {"schemaVersion": 2, "kind": "feature", "featureKind": "team-workload-v1",
                "issue": 7, "session": "sess_implementation", "event": "issue-feature-123",
                "base": base, "publicationSha256": "5" * 64, **hashes}
    manifest = {"schemaVersion": 1, "eventId": metadata["event"], "baseCommit": base,
                "status": "authored-not-executed", "cases": cases,
                "sessionId": "sess_acceptance", "turnId": "turn_acceptance",
                **{key: value for key, value in hashes.items() if key != "patchSha256"}}
    bundle = {"manifest": manifest, "spec": spec, "contract": contract}
    report = {"schemaVersion": 1, "purpose": "feature", "taskKind": "feature", "passed": True,
              "checksPassed": True, "reviewVerdict": "approve", "baseCommit": base, "eventId": metadata["event"],
              **hashes, "candidateSha256": "6" * 64, "planningProvenanceSha256": "7" * 64,
              "approvedCandidate": {"implementationSessionId": metadata["session"],
                                    "implementationTurnId": "turn_implementation",
                                    "reviewSessionId": "sess_review", "reviewTurnId": "turn_review"},
              "bmaSessionId": "sess_browser", "verificationTurnId": "turn_browser",
              "preview": {"eventId": metadata["event"] + "-browser-" + hashes["patchSha256"][:12],
                          "parentEventId": metadata["event"], "baseCommit": base,
                          "taskKind": "feature", "phase": "after", **hashes}}
    pr = {"number": 9, "user": {"login": gate.BOT, "type": "Bot"}, "state": "open", "draft": False,
          "body": "<!-- bma-playground:" + json.dumps(metadata) + " -->",
          "head": {"sha": head, "ref": "codex/issue-7-issue-feature-123", "repo": {"full_name": REPO}},
          "base": {"sha": base, "ref": "main", "repo": {"full_name": REPO}}}
    event = {"action": "synchronize", "repository": {"full_name": REPO}, "pull_request": pr}
    return metadata, event, report, bundle


def deployment_fixture(metadata, event, revision=HEAD):
    pr = copy.deepcopy(event["pull_request"])
    pr.update(state="closed", merged=True, merged_at="2026-10-18T09:00:00Z", merge_commit_sha=revision)
    return {**metadata, "pr": pr["number"], "repository": REPO,
            "deploySha": revision, "pullRequest": pr}


def passed_checks(metadata, bundle, mode="ci"):
    cases = bundle["manifest"]["cases"]
    result = {"schemaVersion": 1, "status": "passed", "passed": True, "errors": [],
              "infrastructureFailure": False, "baseCommit": metadata["base"],
              "patchSha256": metadata["patchSha256"], "acceptanceSha256": metadata["acceptanceSha256"],
              "actualAcceptanceSha256": metadata["acceptanceSha256"],
              "executedFeatureCases": len(cases), "executedProjectCases": len(cases),
              "coveredAcceptanceIds": [f"AC-{n}" for n in range(1, 8)],
              "featureCases": [{"title": row["title"], "project": "desktop", "passed": True,
                                "executed": True, "errors": [], "status": "expected",
                                "expectedStatus": "passed", "outcomes": ["passed"]} for row in cases],
              "commands": [{"name": name, "status": "passed", "exitCode": 0, "timedOut": False}
                           for name in sorted(gate.CI_COMMANDS if mode == "ci" else gate.LIVE_COMMANDS)]}
    if mode == "live":
        result["liveOrigin"] = LIVE
    return result


class IdentityTests(unittest.TestCase):
    def test_feature_context_pins_every_identity(self):
        metadata, event, _, _ = fixture()
        context = gate.ci_context(event, "pull_request", REPO)
        self.assertEqual(context["metadata"], metadata)
        self.assertEqual(context["revision"], HEAD)
        self.assertEqual(context["baseCommit"], BASE)

    def test_nonfeature_events_and_repair_pr_skip(self):
        self.assertIsNone(gate.ci_context({"ref": "refs/heads/main"}, "push", REPO))
        self.assertIsNone(gate.ci_context({"pull_request": {"head": {"ref": "ordinary-feature"}}},
                                         "pull_request", REPO))
        _, event, _, _ = fixture()
        event["pull_request"]["head"]["ref"] = "codex/issue-7-issue-123"
        event["pull_request"]["body"] = '<!-- bma-playground:{"issue":7,"session":"sess_old"} -->'
        self.assertIsNone(gate.ci_context(event, "pull_request", REPO))

    def test_recognizable_malformed_or_spoofed_feature_fails(self):
        mutations = [
            lambda pr: pr.update(body="Missing marker"),
            lambda pr: pr.update(body='<!-- bma-playground:{"kind":"feature" -->'),
            lambda pr: pr.update(body=pr["body"] + pr["body"]),
            lambda pr: pr["user"].update(login="bma-playground"),
            lambda pr: pr["user"].update(type="User"),
            lambda pr: pr["head"].update(ref="codex/issue-8-issue-feature-123"),
            lambda pr: pr["head"].update(sha="HEAD"),
            lambda pr: pr["head"]["repo"].update(full_name="attacker/fork"),
            lambda pr: pr["base"].update(sha="c" * 40),
            lambda pr: pr["base"].update(ref="develop"),
            lambda pr: pr.update(draft=True),
            lambda pr: pr.update(state="closed"),
            lambda pr: pr.update(number=True),
        ]
        for change in mutations:
            with self.subTest(change=change):
                _, event, _, _ = fixture()
                change(event["pull_request"])
                with self.assertRaises(ValueError):
                    gate.ci_context(event, "pull_request", REPO)

    def test_invalid_marker_fields_duplicate_json_and_wrong_event_fail(self):
        for name, value in (("schemaVersion", True), ("schemaVersion", 1), ("kind", "repair"),
                            ("featureKind", "arbitrary"), ("issue", True), ("issue", 0),
                            ("event", "probe-feature-123"), ("event", "issue-feature-01"),
                            ("session", "sess_/bad"), ("base", "main"),
                            ("patchSha256", "a" * 40), ("repository", "elsewhere/repo")):
            with self.subTest(name=name, value=value):
                metadata, event, _, _ = fixture()
                metadata[name] = value
                event["pull_request"]["body"] = "<!-- bma-playground:" + json.dumps(metadata) + " -->"
                with self.assertRaises(ValueError):
                    gate.ci_context(event, "pull_request", REPO)
        _, event, _, _ = fixture()
        event["pull_request"]["body"] = event["pull_request"]["body"].replace(
            '"kind": "feature"', '"kind":"feature","kind":"feature"')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            gate.ci_context(event, "pull_request", REPO)
        _, event, _, _ = fixture()
        for event_name, repository in (("pull_request_target", REPO), ("push", REPO),
                                       ("pull_request", "wrong/repo")):
            with self.assertRaises(ValueError):
                gate.ci_context(event, event_name, repository)

    def test_live_requires_merged_bot_snapshot_at_exact_deploy_sha(self):
        metadata, event, _, _ = fixture()
        deployment = deployment_fixture(metadata, event)
        context = gate.live_context(deployment, REPO, HEAD)
        self.assertEqual(context["revision"], HEAD)
        for change in (
            lambda value: value.pop("pullRequest"),
            lambda value: value.update(deploySha="c" * 40),
            lambda value: value.update(repository="wrong/repo"),
            lambda value: value.update(patchSha256="0" * 64),
            lambda value: value["pullRequest"].update(merge_commit_sha="c" * 40),
            lambda value: value["pullRequest"].update(merged_at=None),
            lambda value: value["pullRequest"].update(merged=False),
            lambda value: value["pullRequest"]["user"].update(login="operator"),
        ):
            invalid = copy.deepcopy(deployment)
            change(invalid)
            with self.assertRaises(ValueError):
                gate.live_context(invalid, REPO, HEAD)
        self.assertIsNone(gate.live_context(None, REPO, HEAD))
        self.assertIsNone(gate.live_context({"issue": 1, "session": "sess_repair"}, REPO, HEAD))


class BundleTests(unittest.TestCase):
    def setUp(self):
        _, event, self.report, self.bundle = fixture()
        self.context = gate.ci_context(event, "pull_request", REPO)

    def test_frozen_bundle_returns_exact_titles(self):
        self.assertEqual(gate.validate_bundle(self.context, self.report, self.bundle),
                         [row["title"] for row in self.bundle["manifest"]["cases"]])

    def test_mutated_spec_contract_manifest_and_case_ids_fail(self):
        for change in (
            lambda bundle: bundle.update(spec=bundle["spec"] + "\n"),
            lambda bundle: bundle.update(contract=bundle["contract"] + "\n"),
            lambda bundle: bundle.update(extra="not allowed"),
            lambda bundle: bundle["manifest"].update(schemaVersion=True),
            lambda bundle: bundle["manifest"].update(eventId="issue-feature-999"),
            lambda bundle: bundle["manifest"].update(baseCommit="c" * 40),
            lambda bundle: bundle["manifest"].update(status="passed"),
            lambda bundle: bundle["manifest"].update(acceptanceSha256="0" * 64),
            lambda bundle: bundle["manifest"].update(contractSha256="0" * 64),
            lambda bundle: bundle["manifest"].update(planSha256="0" * 64),
            lambda bundle: bundle["manifest"].update(prdSha256="0" * 64),
            lambda bundle: bundle["manifest"].update(cases=[None] * 10),
            lambda bundle: bundle["manifest"]["cases"][0].update(title="Wrong [AC-2]"),
        ):
            with self.subTest(change=change):
                bundle = copy.deepcopy(self.bundle)
                change(bundle)
                with self.assertRaises(ValueError):
                    gate.validate_bundle(self.context, self.report, bundle)

    def test_hash_consistent_forbidden_spec_still_fails_validation(self):
        spec = self.bundle["spec"].replace("test(", "test.skip(", 1)
        self.bundle["spec"] = spec
        for value in (self.bundle["manifest"], self.report, self.report["preview"], self.context["metadata"]):
            value["acceptanceSha256"] = gate.digest(spec.encode())
        with self.assertRaisesRegex(ValueError, "forbidden"):
            gate.validate_bundle(self.context, self.report, self.bundle)

    def test_optional_canonical_manifest_digest_is_checked_not_ignored(self):
        checksum = gate.digest(gate.canonical_json(self.bundle["manifest"]))
        self.report["acceptanceManifestSha256"] = checksum
        self.context["metadata"]["acceptanceManifestSha256"] = checksum
        gate.validate_bundle(self.context, self.report, self.bundle)
        self.bundle["manifest"]["cases"][0]["purpose"] = "Altered frozen manifest"
        with self.assertRaisesRegex(ValueError, "manifest hash"):
            gate.validate_bundle(self.context, self.report, self.bundle)

    def test_report_pass_flags_hashes_and_stage_reuse_fail_closed(self):
        changes = [
            lambda report: report.update(passed=False),
            lambda report: report.update(checksPassed="true"),
            lambda report: report.update(reviewVerdict="request_changes"),
            lambda report: report.update(baseCommit="c" * 40),
            lambda report: report.update(eventId="issue-feature-99"),
            lambda report: report.update(repository="wrong/repo"),
            lambda report: report.update(candidateSha256="not a hash"),
            lambda report: report.update(bmaSessionId="sess_review"),
            lambda report: report.update(verificationTurnId="turn_acceptance"),
            lambda report: report["approvedCandidate"].update(implementationSessionId="sess_other"),
            lambda report: report["preview"].update(parentEventId="issue-feature-99"),
        ]
        changes += [lambda report, field=field: report.update({field: "0" * 64})
                    for field in gate.HASH_FIELDS]
        for change in changes:
            with self.subTest(change=change):
                report = copy.deepcopy(self.report)
                change(report)
                with self.assertRaises(ValueError):
                    gate.validate_bundle(self.context, report, self.bundle)


class FetchTests(unittest.TestCase):
    def response(self, raw=b'{"passed":true}', *, status=200, headers=None, chunks=None):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status_code = status
        response.headers = headers or {"Content-Type": "application/json"}
        response.iter_content.return_value = chunks if chunks is not None else [raw]
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value = response
        return session, response

    def test_get_has_no_credentials_env_proxy_redirects_or_artifact_url(self):
        session, _ = self.response()
        with patch.object(gate.requests, "Session", return_value=session):
            self.assertEqual(gate.fetch_json(ORIGIN, "/evidence/event/report.json"), {"passed": True})
        self.assertFalse(session.trust_env)
        args, kwargs = session.get.call_args
        self.assertEqual(args, (ORIGIN + "/evidence/event/report.json",))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["timeout"], (10, 15))
        self.assertNotIn("Authorization", kwargs["headers"])

    def test_redirect_error_oversize_malformed_json_and_type_fail(self):
        variants = [
            {"status": 302, "headers": {"Location": "https://attacker.example.com"}},
            {"status": 404},
            {"headers": {"Content-Type": "text/html"}},
            {"headers": {"Content-Type": "application/json", "Content-Length": "99999"}},
            {"headers": {"Content-Type": "application/json", "Content-Length": "-1"}},
            {"chunks": [b"x" * 50, b"x" * 51]},
            {"raw": b'{"a":1,"a":2}'}, {"raw": b'{"a":NaN}'},
            {"raw": b"[]"}, {"raw": b"\xff"},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                session, _ = self.response(**variant)
                with patch.object(gate.requests, "Session", return_value=session):
                    with self.assertRaises(ValueError):
                        gate.fetch_json(ORIGIN, "/report.json", limit=100)
                self.assertEqual(session.get.call_count, 1)

    def test_cross_origin_paths_bad_origins_and_traversal_never_connect(self):
        with patch.object(gate.requests, "Session") as session:
            for origin, path in (("http://example.com", "/report.json"),
                                 ("https://user:secret@example.com", "/report.json"),
                                 (ORIGIN, "https://attacker.example.com/report.json"),
                                 (ORIGIN, "//evil/report.json"), (ORIGIN, "/../report.json"),
                                 (ORIGIN, "/report.json?redirect=https://evil"),
                                 (ORIGIN + "/prefix", "/report.json")):
                with self.assertRaises(ValueError):
                    gate.fetch_json(origin, path)
            session.assert_not_called()

    def test_streaming_has_a_total_deadline(self):
        session, _ = self.response()
        with patch.object(gate.requests, "Session", return_value=session), \
             patch.object(gate.time, "monotonic", side_effect=[0, 46]):
            with self.assertRaisesRegex(ValueError, "time bound"):
                gate.fetch_json(ORIGIN, "/report.json")


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "repo"
        self.source.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Delivery tests")
        self.git("config", "user.email", "test@example.invalid")
        (self.source / "src").mkdir()
        (self.source / "src/app.js").write_text("export const feature = false;\n")
        (self.source / "README.md").write_text("base documentation\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        (self.source / "src/app.js").write_text("export const feature = true;\n")
        self.git("commit", "-qam", "feature")
        self.head = self.git("rev-parse", "HEAD").decode().strip()
        candidate = self.git("diff", self.base, self.head)
        self.metadata, self.event, self.report, self.bundle = fixture(self.base, self.head, candidate)
        self.event_path = self.root / "event.json"
        self.deployment_path = self.root / "deployment.json"
        self.env = {"GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "pull_request",
                    "BMA_PREVIEW_ORIGIN": ORIGIN, "DEPLOY_SHA": self.head, "APP_URL": LIVE}
        self.output = self.root / "output"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args], stderr=subprocess.PIPE)

    def run_delivery(self, mode="ci", checks=None, revisions=None):
        self.event_path.write_text(json.dumps(self.event))
        if not self.deployment_path.exists():
            self.deployment_path.write_text(json.dumps(deployment_fixture(self.metadata, self.event, self.head)))
        fetched = []
        revisions = list(revisions or [self.head, self.head])

        def fetch(origin, path, **kwargs):
            fetched.append((origin, path))
            if path.endswith("/report.json"):
                return self.report
            if path.endswith("/acceptance.json"):
                return self.bundle
            if path == "/revision.json":
                return {"commit": revisions.pop(0)}
            self.fail("Unexpected remote request")

        with patch.object(gate, "fetch_json", side_effect=fetch), \
             patch.object(gate, "check_candidate", return_value=(
                 checks if checks is not None else passed_checks(self.metadata, self.bundle, mode))) as runner:
            result = gate.delivery(mode=mode, event_path=self.event_path,
                                   deployment_path=self.deployment_path, source=self.source,
                                   output=self.output, env=self.env)
        return result, fetched, runner

    def test_ci_checks_real_committed_patch_and_exact_frozen_titles(self):
        (self.source / "src/app.js").write_text("Uncommitted changes are not the PR head\n")
        before = self.git("status", "--porcelain")
        result, fetched, runner = self.run_delivery()
        self.assertTrue(result["passed"], result["errors"])
        prefix = f'/evidence/issue-feature-123/{self.metadata["patchSha256"]}/feature/'
        self.assertEqual(fetched, [(ORIGIN, prefix + "report.json"), (ORIGIN, prefix + "acceptance.json")])
        args, kwargs = runner.call_args
        self.assertEqual(gate.digest(args[2]), self.metadata["patchSha256"])
        self.assertEqual(kwargs["expected_titles"], [row["title"] for row in self.bundle["manifest"]["cases"]])
        self.assertIsNone(kwargs["live_origin"])
        self.assertEqual(self.git("status", "--porcelain"), before)
        self.assertEqual(json.loads((self.output / "result.json").read_text()), result)

    def test_stale_checkout_and_patch_mismatch_fail_before_http(self):
        self.git("checkout", "--quiet", "--detach", self.base)
        result, fetched, runner = self.run_delivery()
        self.assertFalse(result["passed"])
        self.assertIn("Local HEAD", " ".join(result["errors"]))
        self.assertEqual(fetched, [])
        runner.assert_not_called()
        self.git("checkout", "--quiet", "--detach", self.head)
        self.event["pull_request"]["body"] = self.event["pull_request"]["body"].replace(
            self.metadata["patchSha256"], "0" * 64)
        result, fetched, runner = self.run_delivery()
        self.assertIn("patch hash mismatch", " ".join(result["errors"]))
        self.assertEqual(fetched, [])

    def test_non_src_change_cannot_pass_even_with_a_matching_hash(self):
        (self.source / "README.md").write_text("forbidden change\n")
        self.git("commit", "-qam", "outside scope")
        head = self.git("rev-parse", "HEAD").decode().strip()
        self.metadata, self.event, self.report, self.bundle = fixture(
            self.base, head, self.git("diff", self.base, head))
        result, fetched, runner = self.run_delivery()
        self.assertFalse(result["passed"])
        self.assertIn("src-only-canonical", " ".join(result["errors"]))
        self.assertEqual(fetched, [])
        runner.assert_not_called()

    def test_live_checks_revision_before_and_after_without_local_server(self):
        result, fetched, runner = self.run_delivery("live")
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(fetched[-2:], [(LIVE, "/revision.json")] * 2)
        self.assertEqual(runner.call_args.kwargs["live_origin"], LIVE)
        self.assertEqual(result["revisionBefore"]["commit"], self.head)
        self.assertEqual(result["revisionAfter"]["commit"], self.head)
        self.assertEqual({row["name"] for row in result["checks"]["commands"]}, gate.LIVE_COMMANDS)

    def test_live_revision_stale_before_or_changed_after_fails(self):
        result, _, runner = self.run_delivery("live", revisions=["0" * 40, self.head])
        self.assertFalse(result["passed"])
        self.assertIn("before acceptance", " ".join(result["errors"]))
        runner.assert_not_called()
        result, fetched, runner = self.run_delivery("live", revisions=[self.head, "0" * 40])
        self.assertFalse(result["passed"])
        self.assertIn("changed during acceptance", " ".join(result["errors"]))
        runner.assert_called_once()
        self.assertEqual(fetched[-1], (LIVE, "/revision.json"))

    def test_failed_live_assertions_still_observe_the_final_revision(self):
        checks = passed_checks(self.metadata, self.bundle, "live")
        checks.update(passed=False, status="failed", errors=["Feature assertion failed"])
        result, fetched, _ = self.run_delivery("live", checks=checks)
        self.assertFalse(result["passed"])
        self.assertEqual(fetched[-1], (LIVE, "/revision.json"))
        self.assertEqual(result["revisionAfter"]["commit"], self.head)

    def test_squash_commit_can_differ_from_pr_head_but_not_from_approved_patch(self):
        # Re-create an actual one-commit squash with the same tree and base.
        tree = self.git("rev-parse", "HEAD^{tree}").decode().strip()
        squash = self.git("commit-tree", tree, "-p", self.base, "-m", "Squash feature").decode().strip()
        self.git("checkout", "--quiet", "--detach", squash)
        self.env["DEPLOY_SHA"] = squash
        self.deployment_path.write_text(json.dumps(deployment_fixture(self.metadata, self.event, squash)))
        result, _, _ = self.run_delivery("live", revisions=[squash, squash])
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(result["revision"], squash)
        self.assertEqual(result["headCommit"], self.head)

    def test_missing_extra_retried_or_failed_cases_cannot_use_pass_flag(self):
        for change in (
            lambda value: value["featureCases"].pop(),
            lambda value: value["featureCases"].append(copy.deepcopy(value["featureCases"][0])),
            lambda value: value["featureCases"][0].update(title="different [AC-1]"),
            lambda value: value["featureCases"][0].update(outcomes=["passed", "passed"]),
            lambda value: value["featureCases"][0].update(executed=False),
            lambda value: value["commands"].pop(),
            lambda value: value["commands"][0].update(status="failed"),
            lambda value: value.update(actualAcceptanceSha256="0" * 64),
        ):
            with self.subTest(change=change):
                checks = passed_checks(self.metadata, self.bundle)
                change(checks)
                result, _, _ = self.run_delivery(checks=checks)
                self.assertFalse(result["passed"])

    def test_bad_public_bundle_never_executes_js(self):
        self.bundle["spec"] += "\n"
        result, _, runner = self.run_delivery()
        self.assertFalse(result["passed"])
        runner.assert_not_called()

    def test_default_main_push_and_null_deployment_skip_without_network(self):
        self.event = {"ref": "refs/heads/main"}
        self.env = {"GITHUB_EVENT_NAME": "push"}
        self.deployment_path.write_text("null")
        result, fetched, runner = self.run_delivery()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(fetched, [])
        runner.assert_not_called()
        self.deployment_path.write_text("null")
        result, fetched, runner = self.run_delivery("live")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(fetched, [])

    def test_summary_records_real_checks_and_escapes_errors(self):
        result, _, _ = self.run_delivery()
        result["errors"] = ["<script>untrusted</script>"]
        summary = self.root / "summary.md"
        gate.write_summary(result, summary)
        text = summary.read_text()
        self.assertIn("10 executed / 10 expected", text)
        self.assertIn("feature-browser | passed", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn(str(self.output / "result.json"), text)

    def test_cli_returns_skipped_without_a_preview_origin_and_prints_artifact(self):
        self.event_path.write_text('{"ref":"refs/heads/main"}')
        summary = self.root / "summary.md"
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_STEP_SUMMARY": str(summary)},
                        clear=True), patch.object(gate, "fetch_json") as fetch, redirect_stdout(io.StringIO()) as out:
            code = gate.main(["ci", "--event", str(self.event_path), "--source", str(self.source),
                              "--output", str(self.output)])
        self.assertEqual(code, 0)
        self.assertIn("skipped", out.getvalue())
        self.assertIn(str(self.output / "result.json"), out.getvalue())
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
