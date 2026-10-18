"""Offline feature-browser gate tests. No AWS sessions or browser runs."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
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


def prd_checks_fixture(plan, patch_sha, acceptance_sha):
    from feature_checks import parse_feature_report
    from test_feature_checks import report_fixture, specs
    ids = sorted(browser.requirement_ids(plan))
    report = report_fixture(count=len(ids), project_names=("desktop",))
    for row, ref in zip(specs(report), ids):
        row["title"] = f"[{ref}] observable behavior"
    commands = ("canonical-patch", "clone", "checkout-base", "verify-base", "apply-candidate",
                "protected-inputs", "npm-ci", "npm-test", "npm-build", "board-browser",
                "repair-browser", "feature-browser")
    return {
        **parse_feature_report(report, expected_titles=[row["title"] for row in specs(report)],
                               required_ids=ids),
        "schemaVersion": 1, "baseCommit": BASE, "patchSha256": patch_sha,
        "acceptanceSha256": acceptance_sha, "actualAcceptanceSha256": acceptance_sha,
        "status": "passed", "infrastructureFailure": False,
        "commands": [{"name": name, "status": "passed", "exitCode": 0, "timedOut": False}
                     for name in commands],
    }


def candidate_fixture(patch_bytes=b"verified patch", plan=None, prd=b"Feature PRD"):
    plan = plan or plan_fixture()
    patch_sha, acceptance_sha = browser.digest(patch_bytes), "b" * 64
    candidate = {"schemaVersion": 1, "passed": True, "attempt": 0, "baseCommit": BASE,
            "patchSha256": patch_sha, "acceptanceSha256": acceptance_sha, "contractSha256": "c" * 64,
            "planSha256": browser.digest(browser.canonical_json(plan)), "prdSha256": browser.digest(prd),
            "implementationSessionId": "implementation", "implementationTurnId": "implement-turn",
            "reviewSessionId": "reviewer", "reviewTurnId": "review-turn",
            "exportManifest": {"eventId": EVENT + "-implementation", "baseCommit": BASE},
            "checks": {"passed": True, "baseCommit": BASE, "patchSha256": patch_sha,
                       "acceptanceSha256": acceptance_sha, "errors": []},
            "review": {"schemaVersion": 1, "verdict": "approve", "patchSha256": patch_sha,
                       "acceptanceSha256": acceptance_sha, "findings": [], "summary": "Approved"}}
    if plan["schemaVersion"] == 2:
        candidate.update(contractSha256=browser.digest(plan["uiContract"].encode()),
                         browserJourneySha256=browser.digest(browser.canonical_json(plan["browserJourney"])),
                         checks=prd_checks_fixture(plan, patch_sha, acceptance_sha))
    return candidate


def prd_provenance_fixture(candidate):
    return {"schemaVersion": 2, "planSchemaVersion": 2, "eventId": EVENT, "status": "planned",
            "sessionId": "sess_planning", "turnId": "turn_planning",
            **{key: candidate[key] for key in ("baseCommit", "planSha256", "prdSha256")}}


def prd_acceptance_fixture(plan, candidate, spec):
    candidate["acceptanceSha256"] = browser.digest(spec)
    candidate["checks"] = prd_checks_fixture(plan, candidate["patchSha256"], candidate["acceptanceSha256"])
    candidate["review"]["acceptanceSha256"] = candidate["acceptanceSha256"]
    return {"schemaVersion": 2, "eventId": EVENT, "status": "authored-not-executed",
            "sessionId": "sess_acceptance", "turnId": "turn_acceptance",
            **{key: candidate[key] for key in
               ("baseCommit", "acceptanceSha256", "contractSha256", "planSha256", "prdSha256")},
            **browser.journey_binding(plan),
            "cases": [{"title": row["title"], "acceptanceIds": row["acceptanceIds"],
                       "purpose": "Verify observable behavior"}
                      for row in candidate["checks"]["featureCases"]]}


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


def journey_step(kind, refs, **fields):
    return {"action": kind, "description": f"Observe {kind}", "acceptanceIds": refs, **fields}


def catalog_journey():
    return [
        journey_step("assert_text", ["AC-2"], selector="#catalog-count", expected="3 products"),
        journey_step("fill", ["AC-2"], selector="#catalog-query", value="tea"),
        journey_step("assert_text", ["AC-2"], selector="#catalog-count", expected="1 product"),
        journey_step("select", ["AC-9"], selector="#catalog-sort", value="price"),
        journey_step("assert_text", ["AC-9"], selector="#catalog-result", expected="Green tea"),
        journey_step("reload", ["AC-2"]),
        journey_step("assert_text", ["AC-2"], selector="#catalog-count", expected="1 product"),
    ]


def editor_journey():
    return [
        journey_step("fill", ["AC-1"], selector="#draft-title", value="Release notes"),
        journey_step("assert_text", ["AC-1"], selector="#draft-state", expected="Unsaved"),
        journey_step("click", ["AC-1"], selector="#save-draft"),
        journey_step("assert_text", ["AC-1"], selector="#draft-state", expected="Saved"),
        journey_step("drag", ["AC-2"], selector="#draft-row", target="#published"),
        journey_step("assert_text", ["AC-2"], selector="#publish-count", expected="1 published"),
        journey_step("fill", ["AC-3"], selector="#draft-title", value=""),
        journey_step("assert_text", ["AC-3"], selector="#draft-state", expected="Untitled"),
    ]


def reload_journey():
    return [
        journey_step("assert_text", ["AC-4"], selector="#sync-status", expected="Saved"),
        journey_step("reload", ["AC-4"]),
        journey_step("assert_text", ["AC-4"], selector="#sync-status", expected="Saved"),
    ]


def prd_plan_fixture(journey=None, extra_ids=()):
    journey = catalog_journey() if journey is None else journey
    ids = list(dict.fromkeys(ref for step in journey for ref in step["acceptanceIds"]))
    ids.extend(extra_ids)
    plan = plan_fixture()
    plan.update(schemaVersion=2, title="PRD feature", problem="Observable product behavior",
                approach="Follow the approved UI contract", browserJourney=journey,
                uiContract="# Observable UI contract\nUse the frozen selectors and exact visible text "
                           "specified by the approved browser journey.\n",
                requirements=[{"id": ref, "behavior": f"Behavior for {ref}", "verification": "UI"}
                              for ref in ids])
    plan["tasks"][0].update(detail="Implement the approved behavior", acceptanceIds=ids)
    plan["screens"] = [{**plan["screens"][0], "kind": "preview", "acceptanceIds": ids}]
    return plan


def journey_manifest(journey):
    manifest = manifest_fixture()
    rows = []
    for step in journey:
        if step["action"] == "assert_text":
            rows.append(assertion(step["selector"], step["expected"]))
        else:
            rows.append(action(step["action"], step.get("selector", ""),
                               step.get("value", ""), target=step.get("target", "")))
    manifest.update(summary="Observed the frozen PRD journey",
                    actions=[{"action": "open"}, *rows, {"action": "finish"}])
    for seconds, row in enumerate(manifest["actions"]):
        row["seconds"] = seconds
    return manifest


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

    def test_prd_plan_binds_its_own_contract_criteria_and_journey(self):
        self.plan = prd_plan_fixture()
        self.candidate = candidate_fixture(plan=self.plan)
        self.candidate["contractSha256"] = browser.digest(self.plan["uiContract"].encode())
        self.provenance = prd_provenance_fixture(self.candidate)
        self.write_inputs()
        identity = self.validate()[-1]
        self.assertEqual(identity["acceptanceIds"], ["AC-2", "AC-9"])
        self.assertEqual(identity["browserJourney"], catalog_journey())
        self.assertEqual(identity["browserJourneySha256"], browser.digest(
            browser.canonical_json(catalog_journey())))
        for field in ("contractSha256", "browserJourneySha256"):
            with self.subTest(field=field):
                original = copy.deepcopy(self.candidate)
                self.candidate[field] = "f" * 64
                self.write_inputs()
                with self.assertRaisesRegex(ValueError, "frozen plan"):
                    self.validate()
                self.candidate = original

    def test_primary_browser_journey_can_leave_keyboard_criteria_to_acceptance_checks(self):
        self.plan = prd_plan_fixture(extra_ids=["AC-12"])
        self.plan["requirements"][-1].update(behavior="Keyboard saves the form",
                                            verification="Independent Playwright keyboard test")
        self.candidate = candidate_fixture(plan=self.plan)
        self.provenance = prd_provenance_fixture(self.candidate)
        self.write_inputs()
        identity = self.validate()[-1]
        self.assertEqual(identity["acceptanceIds"], ["AC-12", "AC-2", "AC-9"])
        self.assertEqual(identity["browserJourney"], catalog_journey())
        manifest = journey_manifest(catalog_journey())
        self.assertEqual(browser.validate_feature_calls(items_fixture(manifest), "turn",
                         descriptor_fixture(), manifest, journey=identity["browserJourney"]),
                         ["AC-2", "AC-9"])

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


class GenericPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.planning, self.acceptance = self.root / "planning", self.root / "acceptance"
        self.planning.mkdir()
        self.acceptance.mkdir()
        self.path = self.root / "candidate.json"
        self.argv = ["--event", EVENT, "--candidate", str(self.path),
                     "--planning", str(self.planning), "--acceptance", str(self.acceptance)]
        self.patch = b"verified patch"
        self.spec = b"frozen acceptance spec"
        canonical = patch.object(browser, "canonical_patch", return_value=self.patch)
        canonical.start()
        self.addCleanup(canonical.stop)
        self.reset_inputs()

    def reset_inputs(self):
        self.plan = prd_plan_fixture(extra_ids=["AC-12"])
        self.candidate = candidate_fixture(plan=self.plan)
        self.manifest = prd_acceptance_fixture(self.plan, self.candidate, self.spec)
        self.provenance = prd_provenance_fixture(self.candidate)
        self.write_inputs()

    def write_inputs(self):
        self.path.write_bytes(browser.canonical_json(self.candidate))
        self.path.with_name("verified.patch").write_bytes(self.patch)
        (self.planning / "plan.json").write_bytes(browser.canonical_json(self.plan))
        (self.planning / "provenance.json").write_bytes(browser.canonical_json(self.provenance))
        (self.planning / "prd.md").write_bytes(b"Feature PRD")
        (self.acceptance / "manifest.json").write_bytes(browser.canonical_json(self.manifest))
        (self.acceptance / "workload.acceptance.spec.js").write_bytes(self.spec)
        (self.acceptance / "ui-contract.md").write_text(self.plan["uiContract"])

    def assert_rejected_before_cloud(self):
        with patch.object(browser.boto3, "Session") as aws, \
                patch.object(browser, "run_browser") as run:
            with self.assertRaises(ValueError):
                browser.main(self.argv)
            aws.assert_not_called()
            run.assert_not_called()

    def test_complete_generic_report_passes_both_preflight_gates(self):
        candidate, _, plan, _, identity = browser.validate_inputs(
            EVENT, self.path, self.planning, self.root)
        with patch.object(browser, "validate_check_binding", wraps=browser.validate_check_binding) as check:
            manifest, spec, contract = browser.validate_acceptance_bundle(
                EVENT, candidate, self.acceptance, plan=plan)
            check.assert_called_once_with(candidate["checks"], BASE, candidate["patchSha256"],
                                          manifest, required_ids=["AC-12", "AC-2", "AC-9"])
        self.assertEqual(spec, self.spec)
        self.assertEqual(contract, self.plan["uiContract"].encode())
        self.assertEqual(identity["acceptanceIds"], ["AC-12", "AC-2", "AC-9"])
        self.assertEqual(candidate["checks"]["executedFeatureCases"], 3)

    def test_foreign_unready_or_invalid_planning_provenance_stops_before_cloud(self):
        changes = [("eventId", "probe-other-feature"), ("status", "questions"),
                   ("status", "submitting"), ("status", None),
                   ("schemaVersion", 1), ("schemaVersion", True),
                   ("planSchemaVersion", 1), ("planSchemaVersion", None),
                   ("planSchemaVersion", True)]
        changes += [(field, value) for field in ("sessionId", "turnId")
                    for value in (None, "", " ", 1, "wrong-stage-id", "sess_bad\n", "turn_" + "a" * 121)]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                self.reset_inputs()
                self.provenance[field] = value
                self.write_inputs()
                self.assert_rejected_before_cloud()
        for field in ("sessionId", "turnId", "schemaVersion", "planSchemaVersion", "status", "eventId"):
            with self.subTest(missing=field):
                self.reset_inputs()
                self.provenance.pop(field)
                self.write_inputs()
                self.assert_rejected_before_cloud()

    def test_unresolved_plan_or_changed_explicit_prd_ids_stops_before_cloud(self):
        for questions in (["Which behavior should be shown?"], None):
            with self.subTest(questions=questions):
                self.reset_inputs()
                self.plan["questions"] = questions
                checksum = browser.digest(browser.canonical_json(self.plan))
                self.candidate["planSha256"] = self.provenance["planSha256"] = checksum
                self.write_inputs()
                self.assert_rejected_before_cloud()
        self.reset_inputs()
        prd = b"AC-99: This is a different explicit requirement."
        checksum = browser.digest(prd)
        self.candidate["prdSha256"] = self.provenance["prdSha256"] = checksum
        self.write_inputs()
        (self.planning / "prd.md").write_bytes(prd)
        self.assert_rejected_before_cloud()

    def test_empty_partial_or_fabricated_success_stops_before_cloud(self):
        changes = [
            {"commands": [], "featureCases": [], "coveredAcceptanceIds": [],
             "executedFeatureCases": 0, "executedProjectCases": 0},
            {"commands": []}, {"featureCases": []}, {"coveredAcceptanceIds": []},
            {"coveredAcceptanceIds": ["AC-2", "AC-9"]},
            {"executedFeatureCases": 0}, {"executedProjectCases": 0},
            {"actualAcceptanceSha256": "f" * 64}, {"status": "failed"},
            {"infrastructureFailure": True}, {"passed": False},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.reset_inputs()
                self.candidate["checks"].update(change)
                self.write_inputs()
                self.assert_rejected_before_cloud()
        for field in ("commands", "featureCases", "coveredAcceptanceIds",
                      "executedFeatureCases", "executedProjectCases", "actualAcceptanceSha256"):
            with self.subTest(missing=field):
                self.reset_inputs()
                self.candidate["checks"].pop(field)
                self.write_inputs()
                self.assert_rejected_before_cloud()
        for field, value in (("title", "[AC-2] Another feature"), ("executed", False),
                             ("outcomes", ["passed", "passed"]), ("passed", False),
                             ("acceptanceIds", ["AC-99"]), ("project", "mobile")):
            with self.subTest(case_field=field, value=value):
                self.reset_inputs()
                self.candidate["checks"]["featureCases"][0][field] = value
                self.write_inputs()
                self.assert_rejected_before_cloud()
        for field, value in (("exitCode", 1), ("exitCode", False), ("timedOut", True),
                             ("status", "failed"), ("name", "fabricated-command")):
            with self.subTest(command_field=field, value=value):
                self.reset_inputs()
                self.candidate["checks"]["commands"][0][field] = value
                self.write_inputs()
                self.assert_rejected_before_cloud()

    def test_malformed_or_unknown_criterion_review_stops_before_cloud(self):
        for change in ({"summary": None}, {"schemaVersion": True},
                       {"findings": [{"severity": "minor", "path": "src/main.js", "line": 1,
                                      "acceptanceIds": ["AC-99"], "title": "Unknown criterion",
                                      "evidence": "Observed", "requestedChange": "Correct it"}]}):
            with self.subTest(change=change):
                self.reset_inputs()
                self.candidate["review"].update(change)
                self.write_inputs()
                self.assert_rejected_before_cloud()


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


class PrdJourneyTests(unittest.TestCase):
    def validate(self, journey, manifest=None, items=None):
        manifest = journey_manifest(journey) if manifest is None else manifest
        items = items_fixture(manifest) if items is None else items
        return browser.validate_feature_calls(items, "turn", descriptor_fixture(), manifest,
                                              journey=journey)

    def test_unrelated_variable_criteria_journeys_use_exact_native_actions(self):
        for journey, ids in ((catalog_journey(), ["AC-2", "AC-9"]),
                             (editor_journey(), ["AC-1", "AC-2", "AC-3"]),
                             (reload_journey(), ["AC-4"])):
            with self.subTest(ids=ids):
                self.assertEqual(self.validate(journey), ids)
                assignment = {**descriptor_fixture(), "patchSha256": "b" * 64}
                prompt = browser.verification_prompt(assignment, journey=journey)
                self.assertIn(browser.canonical_json(journey).decode(), prompt)
                self.assertIn("do not support keyboard press", prompt)
                for workload_text in ("Workload", "Maya", "Erez", "mobile", "#reset"):
                    self.assertNotIn(workload_text, prompt)

    def test_wrong_feature_evidence_cannot_satisfy_another_frozen_journey(self):
        for expected, manifest in ((catalog_journey(), manifest_fixture()),
                                   (catalog_journey(), journey_manifest(editor_journey())),
                                   (editor_journey(), journey_manifest(catalog_journey()))):
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError):
                    self.validate(expected, manifest=manifest)
        with self.assertRaises(ValueError):
            browser.validate_feature_calls(items_fixture(journey_manifest(catalog_journey())),
                                           "turn", descriptor_fixture(),
                                           journey_manifest(catalog_journey()))

    def test_every_step_order_selector_value_target_and_exact_assertion_are_frozen(self):
        journey = editor_journey()
        mutations = [(0, {"value": "Other title"}), (0, {"action": "click"}),
                     (2, {"selector": "button"}), (4, {"target": "#trash"}),
                     (4, {"selector": "body"}), (6, {"value": "not empty"}),
                     (7, {"expected": "Other", "actual": "Other"}),
                     (7, {"actual": "fabricated", "passed": True}),
                     (7, {"passed": False}), (2, {"action": "press"})]
        for index, fields in mutations:
            with self.subTest(index=index, fields=fields):
                manifest = journey_manifest(journey)
                manifest["actions"][index + 1].update(fields)
                with self.assertRaises(ValueError):
                    self.validate(journey, manifest=manifest)
        for index in range(len(journey)):
            with self.subTest(missing=index):
                manifest = journey_manifest(journey)
                del manifest["actions"][index + 1]
                with self.assertRaises(ValueError):
                    self.validate(journey, manifest=manifest)
        manifest = journey_manifest(journey)
        manifest["actions"][2], manifest["actions"][4] = manifest["actions"][4], manifest["actions"][2]
        with self.assertRaises(ValueError):
            self.validate(journey, manifest=manifest)
        for row in (action("click", "#save-draft"), assertion("#draft-state", "Untitled"),
                    {**assertion("#draft-state", "Untitled"), "passed": False}):
            manifest = journey_manifest(journey)
            manifest["actions"].insert(-1, row)
            with self.assertRaises(ValueError):
                self.validate(journey, manifest=manifest)

    def test_every_durable_call_and_original_failure_gate_remain_required(self):
        journey = catalog_journey()
        manifest = journey_manifest(journey)
        original = items_fixture(manifest)
        for index in range(len(original) - 1):
            with self.subTest(missing=index):
                items = copy.deepcopy(original)
                del items[index]
                with self.assertRaises(ValueError):
                    self.validate(journey, manifest=manifest, items=items)
        mutations = [("status", "failed"), ("status", "incomplete"),
                     ("server_label", "foreign"), ("turn_id", "other"),
                     ("output", {"isError": True, "content": []}),
                     ("arguments", json.dumps({"action": "fill", "selector": "#other", "value": "tea"}))]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                items = copy.deepcopy(original)
                items[2][field] = value
                with self.assertRaises(ValueError):
                    self.validate(journey, manifest=manifest, items=items)
        for kind in ("command_execution", "file_change", "function_call", "local_shell_call"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "non-browser"):
                self.validate(journey, items=original + [{"type": kind, "turn_id": "turn"}])
        for failed in (mcp("browser_snapshot", {}, {"url": descriptor_fixture()["url"]}),
                       mcp("browser_screenshot", {}, {})):
            failed["output"]["isError"] = True
            with self.assertRaises(ValueError):
                self.validate(journey, items=original[:-2] + [failed] + original[-2:])
        items = copy.deepcopy(original)
        items.insert(-1, copy.deepcopy(items[0]))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.validate(journey, items=items)

    def test_assertion_arguments_and_finish_must_match_the_durable_output(self):
        journey = catalog_journey()
        items = items_fixture(journey_manifest(journey))
        items[1]["arguments"] = json.dumps({"selector": "#other", "expected": "3 products"})
        with self.assertRaisesRegex(ValueError, "durable arguments"):
            self.validate(journey, items=items)
        items = items_fixture(journey_manifest(journey))
        value = json.loads(items[-2]["output"]["content"][0]["text"])
        value["captureSha256"] = "0" * 64
        items[-2]["output"]["content"][0]["text"] = json.dumps(value)
        with self.assertRaises(ValueError):
            self.validate(journey, items=items)

    def test_malformed_journeys_and_partial_identity_do_not_fall_back_to_legacy(self):
        for invalid in (None, [], [{"action": "press", "description": "Keyboard",
                                    "acceptanceIds": ["AC-1"]}],
                        [{**catalog_journey()[0], "acceptanceIds": ["bad"]}, *catalog_journey()[1:]]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    browser.validate_frozen_journey(invalid)
        binding = browser.journey_binding(prd_plan_fixture())
        for field in ("featureKind", *browser.JOURNEY_FIELDS):
            invalid = {key: value for key, value in binding.items() if key != field}
            with self.subTest(missing=field), self.assertRaises(ValueError):
                browser.identity_journey(invalid)
        binding["browserJourneySha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest"):
            browser.identity_journey(binding)


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

    def download_file(self, Bucket, Key, Filename):
        with self.get_object(Bucket, Key)["Body"] as body:
            Path(Filename).write_bytes(body.read())

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

    def test_prd_journey_is_frozen_in_one_session_and_drift_cannot_restart_it(self):
        plan = prd_plan_fixture()
        binding = browser.journey_binding(plan)
        self.candidate = candidate_fixture(plan=plan)
        self.identity.update({field: self.candidate[field] for field in browser.IDENTITIES}, **binding)
        self.client.items.return_value = items_fixture(journey_manifest(catalog_journey()))
        self.publish.return_value.update(schemaVersion=2, **binding)
        first = self.run_browser()
        self.assertEqual(self.run_browser(), first)
        self.client.create.assert_called_once()
        self.client.run_turn.assert_called_once()
        self.preview.assert_called_once()
        self.assertEqual(self.state()["identity"]["browserJourney"], catalog_journey())
        self.assertEqual(self.state()["descriptor"]["browserJourneySha256"], binding["browserJourneySha256"])
        configuration = self.client.create.call_args.args[0]
        self.assertNotIn("Workload", configuration["agent"]["instructions"])
        self.assertIn(browser.canonical_json(catalog_journey()).decode(),
                      self.client.run_turn.call_args.args[1])
        changed = copy.deepcopy(binding["browserJourney"])
        changed[0]["expected"] = "4 products"
        self.identity.update(browserJourney=changed,
                             browserJourneySha256=browser.digest(browser.canonical_json(changed)))
        self.candidate["browserJourneySha256"] = self.identity["browserJourneySha256"]
        with self.assertRaisesRegex(ValueError, "different approved"):
            self.run_browser()
        self.client.create.assert_called_once()
        self.client.run_turn.assert_called_once()

    def test_prd_identity_tampering_fails_before_a_session_or_preview(self):
        plan = prd_plan_fixture()
        self.candidate = candidate_fixture(plan=plan)
        self.identity.update(browser.journey_binding(plan))
        self.identity["browserJourneySha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest"):
            self.run_browser()
        self.preview.assert_not_called()
        self.client.create.assert_not_called()
        self.assertEqual(self.s3.writes, [])


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

    @patch.dict(os.environ, ENV)
    def test_prd_report_result_and_metadata_carry_the_exact_frozen_journey(self):
        plan = prd_plan_fixture(extra_ids=["AC-12"])
        candidate = candidate_fixture(plan=plan)
        binding = browser.journey_binding(plan)
        identity = {key: candidate[key] for key in browser.IDENTITIES}
        identity.update(eventId=EVENT, candidateSha256="1" * 64,
                        planningProvenanceSha256="2" * 64, **binding)
        state = {"descriptor": descriptor_fixture(), "identity": identity,
                 "origin": ENV["BMA_PREVIEW_ORIGIN"], "sessionId": "verification",
                 "outcome": {"turnId": "turn"}, "design": {"url": "https://example.invalid/design"}}
        manifest = journey_manifest(catalog_journey())
        s3 = FakeS3()
        s3.put_object(Bucket="private",
                      Key=f"repairs/{descriptor_fixture()['eventId']}/browser/after/manifest.json",
                      Body=browser.canonical_json(manifest))

        def collect(client, descriptor, destination, journey):
            self.assertEqual(journey, catalog_journey())
            destination.mkdir()
            (destination.parent / "after.mp4").write_bytes(b"mp4")
            (destination.parent / "after.png").write_bytes(b"png")
            return manifest

        with tempfile.TemporaryDirectory() as temporary, patch.object(
                browser, "collect_feature_phase", side_effect=collect) as collector:
            result = browser.publish_feature_evidence(s3, state, candidate,
                                                      items_fixture(manifest), Path(temporary))
            collector.assert_called_once()
            report = browser.json_object((Path(temporary) / "report.json").read_bytes())
            for value in (report, result):
                self.assertEqual(value["schemaVersion"], 2)
                for field in ("featureKind", *browser.JOURNEY_FIELDS):
                    self.assertEqual(value[field], binding[field])
            self.assertEqual(report["verifiedObservations"], ["AC-2", "AC-9"])
            page = next(data.decode() for bucket, key, _, data in s3.writes if key.endswith("index.html"))
            self.assertNotIn("Workload", page)
            for bucket, key, attrs, data in s3.writes:
                if bucket == "evidence":
                    self.assertEqual(attrs["Metadata"]["browser-journey-sha256"],
                                     binding["browserJourneySha256"])
            writes = len(s3.writes)
            # Even if identity hashes stay fixed, evidence for Workload cannot be published.
            wrong = manifest_fixture()
            s3.put_object(Bucket="private",
                          Key=f"repairs/{descriptor_fixture()['eventId']}/browser/after/manifest.json",
                          Body=browser.canonical_json(wrong))
            with self.assertRaises(ValueError):
                browser.publish_feature_evidence(s3, state, candidate, items_fixture(wrong), Path(temporary))
            self.assertEqual(len(s3.writes), writes + 1)


class PrdCaptureTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.s3 = FakeS3()
        self.manifest = journey_manifest(reload_journey())
        self.prefix = f"repairs/{descriptor_fixture()['eventId']}/browser/after/"
        png, jpeg = io.BytesIO(), io.BytesIO()
        image = browser.Image.new("RGB", (16, 16), color="white")
        image.save(png, format="PNG")
        image.save(jpeg, format="JPEG")
        index = {"duration": 2, "frames": [{"name": f"frame-{number:06d}.jpg", "seconds": number}
                                         for number in range(2)]}
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for name, data in (("frames.json", browser.canonical_json(index)),
                               ("frame-000000.jpg", jpeg.getvalue()), ("frame-000001.jpg", jpeg.getvalue())):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        self.manifest.update(captureSha256=browser.digest(archive.getvalue()),
                             screenshotSha256=browser.digest(png.getvalue()))
        self.s3.put_object(Bucket="private", Key=self.prefix + "capture.tar", Body=archive.getvalue())
        self.s3.put_object(Bucket="private", Key=self.prefix + "result.png", Body=png.getvalue())

    def collect(self):
        self.s3.put_object(Bucket="private", Key=self.prefix + "manifest.json",
                          Body=browser.canonical_json(self.manifest))
        with tempfile.TemporaryDirectory() as temporary, patch.object(browser, "encode_video") as encoder:
            result = browser.collect_feature_phase(self.s3, descriptor_fixture(),
                                                    Path(temporary) / "after", reload_journey())
            encoder.assert_called_once()
            self.assertTrue((Path(temporary) / "after.png").exists())
            return result

    def test_reload_only_capture_keeps_real_archive_and_screenshot_validation(self):
        self.assertEqual(self.collect(), self.manifest)

    def test_changed_digests_and_frame_count_are_rejected(self):
        for field, value in (("captureSha256", "0" * 64), ("screenshotSha256", "0" * 64),
                             ("frameCount", 3)):
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = value
                with self.assertRaises(ValueError):
                    self.collect()
                self.manifest[field] = original

    def test_oversized_archive_and_unsafe_members_are_rejected(self):
        with patch.object(self.s3, "head_object", return_value={"ContentLength": browser.MAX_CAPTURE + 1}):
            with self.assertRaisesRegex(ValueError, "Oversized"):
                self.collect()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            tar.addfile(tarfile.TarInfo("../outside"))
        self.s3.put_object(Bucket="private", Key=self.prefix + "capture.tar", Body=archive.getvalue())
        self.manifest["captureSha256"] = browser.digest(archive.getvalue())
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.collect()


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

    @patch.dict(os.environ, ENV)
    def test_prd_bundle_requires_matching_plan_ids_journey_digest_and_schema(self):
        plan = prd_plan_fixture(extra_ids=["AC-12"])
        candidate = candidate_fixture(plan=plan)
        spec, contract = b"frozen acceptance spec", plan["uiContract"].encode()
        manifest = prd_acceptance_fixture(plan, candidate, spec)
        s3 = FakeS3()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "manifest.json"
            path.write_bytes(browser.canonical_json(manifest))
            (directory / "workload.acceptance.spec.js").write_bytes(spec)
            (directory / "ui-contract.md").write_bytes(contract)
            key = browser.publish_acceptance_bundle(s3, EVENT, candidate, directory, plan=plan)
            self.assertEqual(browser.json_object(s3.objects["evidence", key][0])["manifest"], manifest)
            writes = len(s3.writes)
            for invalid_plan in (None, plan_fixture(), prd_plan_fixture(editor_journey())):
                with self.subTest(plan=invalid_plan), self.assertRaises(ValueError):
                    browser.publish_acceptance_bundle(s3, EVENT, candidate, directory, plan=invalid_plan)
            mutations = [
                {"schemaVersion": 1}, {"schemaVersion": True}, {"schemaVersion": 3},
                {"acceptanceIds": ["AC-2"]}, {"acceptanceIds": ["AC-2", "AC-2"]},
                {"acceptanceIds": ["AC-2", "AC-3"]}, {"acceptanceIds": ["AC-9", "AC-2"]},
                {"browserJourneySha256": "0" * 64},
                {"browserJourney": editor_journey()},
                {"browserJourney": editor_journey(),
                 "browserJourneySha256": browser.digest(browser.canonical_json(editor_journey()))},
            ]
            for fields in mutations:
                path.write_bytes(browser.canonical_json({**manifest, **fields}))
                with self.subTest(fields=fields), self.assertRaises(ValueError):
                    browser.publish_acceptance_bundle(s3, EVENT, candidate, directory, plan=plan)
            for field in browser.JOURNEY_FIELDS:
                path.write_bytes(browser.canonical_json({key: value for key, value in manifest.items()
                                                        if key != field}))
                with self.subTest(missing=field), self.assertRaises(ValueError):
                    browser.publish_acceptance_bundle(s3, EVENT, candidate, directory, plan=plan)
            path.write_bytes(browser.canonical_json(manifest))
            missing_candidate_hash = {key: value for key, value in candidate.items()
                                      if key != "browserJourneySha256"}
            with self.assertRaisesRegex(ValueError, "browser journey"):
                browser.publish_acceptance_bundle(s3, EVENT, missing_candidate_hash, directory, plan=plan)
            # Matching manifest/candidate contract hashes cannot replace the plan's UI contract.
            other_contract = b"An unrelated UI contract with a different feature and selectors."
            (directory / "ui-contract.md").write_bytes(other_contract)
            candidate["contractSha256"] = manifest["contractSha256"] = browser.digest(other_contract)
            path.write_bytes(browser.canonical_json(manifest))
            with self.assertRaisesRegex(ValueError, "UI contract"):
                browser.publish_acceptance_bundle(s3, EVENT, candidate, directory, plan=plan)
            self.assertEqual(len(s3.writes), writes)


if __name__ == "__main__":
    unittest.main()
