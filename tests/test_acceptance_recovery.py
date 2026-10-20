"""Offline recovery contract tests; issue25 evidence is reproduced inline."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import acceptance_recovery as recovery
from design_board import canonical_json
from feature_stages import FeatureStages, validate_acceptance


COMMAND_NAMES = (
    "canonical-patch", "clone", "checkout-base", "verify-base", "apply-candidate",
    "protected-inputs", "npm-ci", "npm-test", "npm-build", "board-browser",
    "repair-browser", "feature-browser",
)
TITLES = (
    "[AC-1] removed theme controls are absent from keyboard navigation",
    "[AC-2] creating a task still works",
    "[AC-3] task changes survive reload",
)
BAD_ASSERTION = "await expect(page.locator(':focus')).not.toHaveText(/Light|Dark/);"
FIXED_ASSERTION = (
    "await expect(page.locator(':focus').filter({ hasText: /Light|Dark/ })).toHaveCount(0);"
)
SPEC = """import { test, expect } from '@playwright/test';
test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Reset demo board', exact: true }).click();
});
test('[AC-1] removed theme controls are absent from keyboard navigation', async ({ page }) => {
  await expect(page.getByRole('button', { name: /^(Light|Dark)$/ })).toHaveCount(0);
  for (let index = 0; index < 45; index++) {
    await page.keyboard.press('Tab');
    await expect(page.locator(':focus')).not.toHaveText(/Light|Dark/);
  }
});
test('[AC-2] creating a task still works', async ({ page }) => {
  await page.getByLabel('Task title', { exact: true }).fill('Keyboard regression');
  await page.getByRole('button', { name: 'Add task', exact: true }).click();
  await expect(page.locator('#board')).toContainText('Keyboard regression');
});
test('[AC-3] task changes survive reload', async ({ page }) => {
  await page.getByLabel('Task title', { exact: true }).fill('Persistent task');
  await page.getByRole('button', { name: 'Add task', exact: true }).click();
  await page.reload();
  await expect(page.locator('#board')).toContainText('Persistent task');
});
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def issue25_fixture():
    """Reduced issue25 shape: 45 Tabs, empty :focus, only feature-browser fails."""
    requirements = [
        {"id": "AC-1", "behavior": "Remove Light and Dark controls, including tab stops"},
        {"id": "AC-2", "behavior": "Preserve task creation"},
        {"id": "AC-3", "behavior": "Preserve task persistence after reload"},
    ]
    prd = "\n".join(f"{row['id']}: {row['behavior']}" for row in requirements)
    contract = (
        "Light and Dark controls are absent. Task title and Add task create a card "
        "in #board. Saved cards survive reload. Reset demo board restores the fixture."
    )
    journey = [{"action": "assert_text", "selector": "#board", "expected": "Deploy preview",
                "description": "Existing board remains available", "acceptanceIds": ["AC-2"]}]
    plan = {"schemaVersion": 2, "requirements": requirements,
            "uiContract": contract, "browserJourney": journey}
    manifest = {
        "schemaVersion": 2, "eventId": "issue-25", "baseCommit": "a" * 40,
        "planSha256": sha(canonical_json(plan)), "prdSha256": sha(prd.encode()),
        "contractSha256": sha(contract.encode()), "acceptanceSha256": sha(SPEC.encode()),
        "sessionId": "sess_original_author", "turnId": "turn_original_author",
        "status": "authored-not-executed", "acceptanceIds": ["AC-1", "AC-2", "AC-3"],
        "browserJourney": journey, "browserJourneySha256": sha(canonical_json(journey)),
        "cases": [{"title": title, "acceptanceIds": [row["id"]], "purpose": row["behavior"]}
                  for title, row in zip(TITLES, requirements)],
    }
    checks = {
        "schemaVersion": 1, "passed": False, "status": "failed",
        "baseCommit": manifest["baseCommit"], "patchSha256": sha(b"unchanged application patch"),
        "acceptanceSha256": manifest["acceptanceSha256"],
        "actualAcceptanceSha256": manifest["acceptanceSha256"],
        "infrastructureFailure": False, "artifacts": "runs/first", "resultFile": "runs/result.json",
        "executedFeatureCases": 3, "executedProjectCases": 3,
        "coveredAcceptanceIds": ["AC-2", "AC-3"],
        "errors": ["desktop: " + TITLES[0] + ": test did not pass cleanly"],
        "commands": [
            {"name": name, "status": "failed" if name == "feature-browser" else "passed",
             "exitCode": 1 if name == "feature-browser" else 0, "timedOut": False,
             "command": ["runner", name, "--port", "4101"], "durationSeconds": 8.5,
             "stdout": f"runs/first/{name}.stdout.log",
             "stderr": f"runs/first/{name}.stderr.log"}
            for name in COMMAND_NAMES
        ],
        # feature_checks.parse_feature_report emits "project", not "projectName".
        "featureCases": [
            {"title": title, "fullTitle": "workload.acceptance.spec.js > " + title,
             "file": "workload.acceptance.spec.js", "line": index + 6, "project": "desktop",
             "status": "unexpected" if index == 0 else "expected", "expectedStatus": "passed",
             "outcomes": ["failed"] if index == 0 else ["passed"],
             "executed": True, "passed": index != 0, "acceptanceIds": [f"AC-{index + 1}"],
             "errors": ["test did not pass cleanly"] if index == 0 else [],
             "observations": [
                 "Error: expect(locator).not.toHaveText(expected) failed\n"
                 "Locator: locator(':focus')\nExpected pattern: not /Light|Dark/\n"
                 "Received: <element(s) not found>\nTimeout: 5000ms\n"
                 "at runs/first/workload.acceptance.spec.js:10"
             ] if index == 0 else []}
            for index, title in enumerate(TITLES)
        ],
    }
    review = {
        "schemaVersion": 1, "verdict": "approve", "patchSha256": checks["patchSha256"],
        "acceptanceSha256": manifest["acceptanceSha256"], "findings": [],
        "summary": "The application removes theme controls and preserves board behavior.",
    }
    return {"prd": prd, "plan": plan, "contract": contract, "manifest": manifest,
            "spec": SPEC, "checks": checks, "review": review,
            "required_ids": set(manifest["acceptanceIds"])}


def recovered_fixture(fixture):
    """Return (revised_manifest, revised_spec) for an issue25_fixture() input."""
    parent, checks = fixture["manifest"], fixture["checks"]
    identity = {
        "baseCommit": parent["baseCommit"], "planSha256": parent["planSha256"],
        "prdSha256": parent["prdSha256"], "acceptanceSha256": parent["acceptanceSha256"],
        "acceptanceManifestSha256": sha(canonical_json(parent)),
        "patchSha256": checks["patchSha256"], "checksSha256": sha(canonical_json(checks)),
    }
    diagnosis = {
        "schemaVersion": 1, **identity, "classification": "test_defect",
        "affectedTitles": [TITLES[0]],
        "evidence": "Tab may leave the document. The negated text assertion still waits "
                    "for :focus to exist, so a conforming application times out on empty focus.",
    }
    proposal = {
        "schemaVersion": 1, **identity,
        "replacements": [{"old": BAD_ASSERTION, "new": FIXED_ASSERTION,
                          "reason": "Count forbidden focused matches; empty focus is valid."}],
    }
    corrected = SPEC.replace(BAD_ASSERTION, FIXED_ASSERTION)
    audit = {
        "schemaVersion": 1, **identity, "correctedAcceptanceSha256": sha(corrected.encode()),
        "verdict": "approve", "preservesAllCriteria": True, "changesOnlyTestDefects": True,
        "rationale": "Only the empty-focus assertion changes; every original case remains.",
        "caseAssessments": [
            {"title": title, "preserved": True,
             "reason": "Forbidden focus remains checked after all 45 Tabs." if index == 0
                       else "The original case and its assertions are byte-for-byte unchanged."}
            for index, title in enumerate(TITLES)
        ],
    }
    record = {
        "schemaVersion": 1, "identity": identity,
        "original": {"manifest": copy.deepcopy(parent), "spec": SPEC},
        "checks": copy.deepcopy(checks), "codeReview": copy.deepcopy(fixture["review"]),
        **{role: {"sessionId": f"sess_{role}", "turnId": f"turn_{role}", "result": result}
           for role, result in (("diagnosis", diagnosis), ("author", proposal), ("review", audit))},
    }
    return {**copy.deepcopy(parent), "acceptanceSha256": sha(corrected.encode()),
            "sessionId": "sess_author", "turnId": "turn_author", "recovery": record}, corrected


def recovery_fixture():
    """Return fresh (original, revised) dictionaries for recovery integration tests.

    original contains manifest, spec, checks, review, prd, plan, contract and
    required_ids, matching recover() keyword arguments. revised contains manifest
    and spec, matching recover()'s successful bundle. Its manifest includes the
    original evidence and all three independent recovery roles. The plan is a
    minimal recovery fixture, not a complete validate_plan() planning handoff.
    """
    original = issue25_fixture()
    manifest, spec = recovered_fixture(original)
    return original, {"manifest": manifest, "spec": spec}


def stage_response(role):
    turn = role["turnId"]
    state = {"sessionId": role["sessionId"], "outcome": {"turnId": turn}}
    items = [{"turn_id": turn, "type": "message", "role": "assistant",
              "phase": "final_answer", "status": "completed",
              "content": [{"type": "output_text", "text": json.dumps(role["result"])}]}]
    return state, items


class AcceptanceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture, revised = recovery_fixture()
        self.manifest, self.corrected = revised["manifest"], revised["spec"]
        self.record = self.manifest["recovery"]
        self.ids = self.fixture["required_ids"]

    def validate(self, manifest=None, spec=None, excluded_sessions=()):
        return recovery.validate_recovery(
            self.manifest if manifest is None else manifest,
            self.corrected if spec is None else spec, self.ids, excluded_sessions)

    def apply(self, proposal, spec=SPEC):
        return recovery.apply_replacements(
            spec, proposal, self.record["identity"], self.fixture["manifest"], self.ids)

    def invoke(self, stages, directory, **overrides):
        inputs = {**self.fixture, "candidate_archive": b"candidate archive",
                  "base_archive": b"baseline archive", "directory": directory,
                  "excluded_sessions": ("sess_implementer", "sess_code_reviewer"), **overrides}
        return recovery.recover(stages, **inputs)

    def test_inline_issue25_failure_is_eligible_and_valid_recovery_preserves_all_cases(self):
        validate_acceptance({**self.fixture["manifest"], "spec": SPEC}, self.ids)
        self.assertIn("index < 45", SPEC)
        self.assertIn("page.keyboard.press('Tab')", SPEC)
        self.assertIn("<element(s) not found>", self.fixture["checks"]["featureCases"][0]["observations"][0])
        self.assertTrue(recovery.eligible(self.fixture["checks"], self.fixture["review"]))
        self.assertEqual(self.apply(self.record["author"]["result"]), self.corrected)
        self.validate()
        self.assertEqual(self.manifest["cases"], self.fixture["manifest"]["cases"])
        self.assertEqual(self.manifest["browserJourney"], self.fixture["manifest"]["browserJourney"])
        self.assertEqual(self.corrected.replace(FIXED_ASSERTION, BAD_ASSERTION), SPEC)

    def test_eligibility_requires_all_other_commands_to_pass_without_timeouts(self):
        for name in COMMAND_NAMES[:-1]:
            for failure in ({"status": "failed", "exitCode": 1},
                            {"status": "passed", "timedOut": True},
                            {"status": "passed", "exitCode": None}):
                with self.subTest(command=name, failure=failure):
                    checks = copy.deepcopy(self.fixture["checks"])
                    next(row for row in checks["commands"] if row["name"] == name).update(failure)
                    self.assertFalse(recovery.eligible(checks, self.fixture["review"]))

    def test_eligibility_rejects_incomplete_commands_infrastructure_and_nonfailures(self):
        mutations = {
            "missing command": lambda c: c["commands"].pop(0),
            "duplicate command": lambda c: c["commands"].__setitem__(0, copy.deepcopy(c["commands"][1])),
            "extra command": lambda c: c["commands"].append(copy.deepcopy(c["commands"][0])),
            "infrastructure failure": lambda c: c.update(infrastructureFailure=True),
            "missing infrastructure classification": lambda c: c.pop("infrastructureFailure"),
            "checks passed": lambda c: c.update(passed=True),
            "feature command passed": lambda c: c["commands"][-1].update(status="passed", exitCode=0),
            "no executed cases": lambda c: c.update(featureCases=[]),
            "no failed cases": lambda c: c["featureCases"][0].update(passed=True),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                checks = copy.deepcopy(self.fixture["checks"])
                mutate(checks)
                self.assertFalse(recovery.eligible(checks, self.fixture["review"]))
        for verdict in ("request_changes", "reject", "uncertain", None):
            with self.subTest(verdict=verdict):
                self.assertFalse(recovery.eligible(
                    self.fixture["checks"], {**self.fixture["review"], "verdict": verdict}))

    def test_feature_runner_timeout_is_not_an_isolated_test_failure(self):
        checks = copy.deepcopy(self.fixture["checks"])
        checks["commands"][-1].update(status="timeout", exitCode=-9, timedOut=True)
        self.assertFalse(recovery.eligible(checks, self.fixture["review"]))

    def test_unchanged_failure_ignores_run_paths_ports_order_and_timing(self):
        previous = {"passed": False, "patchSha256": self.fixture["checks"]["patchSha256"],
                    "acceptanceSha256": self.fixture["manifest"]["acceptanceSha256"],
                    "checks": copy.deepcopy(self.fixture["checks"])}
        current = copy.deepcopy(previous)
        current["checks"].update(artifacts="runs/second", resultFile="elsewhere/result.json",
                                 errors=["Same failure with different stack formatting"])
        for command in current["checks"]["commands"]:
            command.update(stdout="runs/second/stdout.log", stderr="runs/second/stderr.log",
                           command=["runner", command["name"], "--port", "9202"],
                           durationSeconds=61.2)
        current["checks"]["featureCases"][0]["observations"] = [
            "Timeout 5000ms; empty focus at runs/second/workload.acceptance.spec.js:10"]
        current["checks"]["commands"].reverse()
        current["checks"]["featureCases"].reverse()
        self.assertEqual(recovery.failure_evidence(previous["checks"]),
                         recovery.failure_evidence(current["checks"]))
        self.assertTrue(recovery.unchanged_failure(previous, current))

    def test_unchanged_failure_requires_the_same_patch_spec_and_failed_case_outcome(self):
        previous = {"passed": False, "patchSha256": self.fixture["checks"]["patchSha256"],
                    "acceptanceSha256": self.fixture["manifest"]["acceptanceSha256"],
                    "checks": copy.deepcopy(self.fixture["checks"])}
        mutations = {
            "patch": lambda c: c.update(patchSha256=sha(b"different patch")),
            "spec": lambda c: c.update(acceptanceSha256=sha(b"different spec")),
            "success": lambda c: c.update(passed=True),
            "case title": lambda c: c["checks"]["featureCases"][0].update(title=TITLES[1]),
            "case outcome": lambda c: c["checks"]["featureCases"][0].update(outcomes=["timedOut"]),
            "case passed": lambda c: c["checks"]["featureCases"][0].update(passed=True),
            "exit status": lambda c: c["checks"]["commands"][-1].update(exitCode=2),
            "command timeout": lambda c: c["checks"]["commands"][-1].update(timedOut=True),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                current = copy.deepcopy(previous)
                mutate(current)
                self.assertFalse(recovery.unchanged_failure(previous, current))
        self.assertFalse(recovery.unchanged_failure({**previous, "passed": True}, previous))

    def test_failure_identity_distinguishes_actual_browser_project_field(self):
        previous = {"passed": False, "patchSha256": self.fixture["checks"]["patchSha256"],
                    "acceptanceSha256": self.fixture["manifest"]["acceptanceSha256"],
                    "checks": copy.deepcopy(self.fixture["checks"])}
        current = copy.deepcopy(previous)
        current["checks"]["featureCases"][0]["project"] = "mobile"
        self.assertFalse(recovery.unchanged_failure(previous, current))

    def test_replacements_reject_empty_missing_ambiguous_and_unchanged_fragments(self):
        good = self.record["author"]["result"]
        invalid_edits = (
            [], None, [{}] * 6,
            [{"old": "", "new": FIXED_ASSERTION, "reason": "No target"}],
            [{"old": BAD_ASSERTION, "new": " ", "reason": "Delete the assertion"}],
            [{"old": BAD_ASSERTION, "new": FIXED_ASSERTION, "reason": ""}],
            [{"old": "not in the original spec", "new": FIXED_ASSERTION, "reason": "Missing"}],
            [{"old": "await expect(", "new": "await expect.poll(", "reason": "Ambiguous"}],
            [{"old": BAD_ASSERTION, "new": BAD_ASSERTION, "reason": "Unchanged"}],
            [good["replacements"][0], good["replacements"][0]],
            [{"old": BAD_ASSERTION, "new": "x" * 5001, "reason": "Oversized"}],
        )
        for edits in invalid_edits:
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                self.apply({**good, "replacements": edits})

    def test_replacements_cannot_change_full_test_declarations_or_add_skip_mechanisms(self):
        declaration = f"test('{TITLES[0]}', async ({{ page }}) => {{"
        proposals = [
            {"old": declaration, "new": declaration.replace("navigation", "traversal"),
             "reason": "Rename the test"},
            *({"old": BAD_ASSERTION, "new": f"test.{mechanism}();",
               "reason": "Avoid the failure"}
              for mechanism in ("skip", "fixme", "only", "fail")),
        ]
        for edit in proposals:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.apply({**self.record["author"]["result"], "replacements": [edit]})

    def test_replacements_cannot_rename_a_case_by_replacing_only_its_title(self):
        edit = {"old": TITLES[0], "new": TITLES[0].replace("navigation", "traversal"),
                "reason": "Change the executable case identity without replacing test(."}
        with self.assertRaises(ValueError):
            self.apply({**self.record["author"]["result"], "replacements": [edit]})

    def test_replacements_reject_a_stale_author_identity(self):
        for key in self.record["identity"]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.apply({**self.record["author"]["result"], key: "stale"})

    def test_diagnosis_requires_evidence_and_actual_failed_frozen_titles(self):
        good = self.record["diagnosis"]["result"]
        for mutation in (
            {"classification": "test_defect", "affectedTitles": []},
            {"classification": "unknown"}, {"evidence": " "},
            {"affectedTitles": [TITLES[1]]}, {"affectedTitles": ["unknown case"]},
            {"affectedTitles": [TITLES[0], TITLES[0]]}, {"affectedTitles": [None]},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                recovery.validate_diagnosis(
                    {**good, **mutation}, self.record["identity"],
                    self.fixture["manifest"], self.fixture["checks"])

    def test_diagnosis_accepts_bounded_string_or_string_list_evidence(self):
        explanation = self.record["diagnosis"]["result"]["evidence"]
        for label, evidence in (
            ("string", explanation),
            ("one string in a list", [explanation]),
            ("multiple observations", ["Focus left the document.", explanation]),
            ("maximum string length", "x" * 10000),
            ("maximum list and row lengths", ["x" * 10000] * 12),
        ):
            with self.subTest(evidence=label):
                manifest = copy.deepcopy(self.manifest)
                manifest["recovery"]["diagnosis"]["result"]["evidence"] = evidence
                self.validate(manifest)

    def test_diagnosis_rejects_empty_malformed_and_oversized_evidence(self):
        good = self.record["diagnosis"]["result"]
        for label, evidence in (
            ("null", None), ("empty string", ""), ("whitespace", " \n\t"),
            ("empty list", []), ("blank row", [""]),
            ("whitespace row", ["Observation", " \t"]),
            ("null row", ["Observation", None]), ("number row", ["Observation", 1]),
            ("nested list", [["Observation"]]), ("object row", [{"text": "Observation"}]),
            ("object", {"text": "Observation"}), ("boolean", True),
            ("tuple", ("Observation",)), ("too many rows", ["Observation"] * 13),
            ("string too long", "x" * 10001), ("list row too long", ["x" * 10001]),
        ):
            with self.subTest(evidence=label), self.assertRaises(ValueError):
                recovery.validate_diagnosis(
                    {**good, "evidence": evidence}, self.record["identity"],
                    self.fixture["manifest"], self.fixture["checks"])
        missing = {key: value for key, value in good.items() if key != "evidence"}
        with self.assertRaises(ValueError):
            recovery.validate_diagnosis(
                missing, self.record["identity"], self.fixture["manifest"], self.fixture["checks"])

    def test_recovery_accepts_equivalent_flat_nested_and_matching_duplicate_identities(self):
        for representation in ("flat", "nested", "both", "partial matching duplicates"):
            with self.subTest(representation=representation):
                manifest = copy.deepcopy(self.manifest)
                for role in ("diagnosis", "author", "review"):
                    result = manifest["recovery"][role]["result"]
                    keys = [*self.record["identity"]]
                    if role == "review":
                        keys.append("correctedAcceptanceSha256")
                    if representation != "flat":
                        result["identity"] = {key: result.pop(key) for key in keys}
                    if representation == "both":
                        result.update(result["identity"])
                    elif representation == "partial matching duplicates":
                        result["patchSha256"] = result["identity"]["patchSha256"]
                self.validate(manifest)

    def test_recovery_rejects_missing_or_wrong_fields_in_each_identity_representation(self):
        for role in ("diagnosis", "author", "review"):
            keys = [*self.record["identity"]]
            if role == "review":
                keys.append("correctedAcceptanceSha256")
            for key in keys:
                for representation in ("flat", "nested"):
                    for mutation in ("missing", "wrong"):
                        with self.subTest(role=role, key=key, representation=representation,
                                          mutation=mutation):
                            manifest = copy.deepcopy(self.manifest)
                            result = manifest["recovery"][role]["result"]
                            if representation == "nested":
                                result["identity"] = {field: result.pop(field) for field in keys}
                                target = result["identity"]
                            else:
                                target = result
                            if mutation == "missing":
                                target.pop(key)
                            else:
                                target[key] = sha(b"wrong identity")
                            with self.assertRaises(ValueError):
                                self.validate(manifest)

    def test_recovery_rejects_conflicting_duplicate_identity_hashes_in_either_direction(self):
        for role in ("diagnosis", "author", "review"):
            keys = [*self.record["identity"]]
            if role == "review":
                keys.append("correctedAcceptanceSha256")
            for key in keys:
                for conflicting_side in ("flat", "nested"):
                    with self.subTest(role=role, key=key, conflicting_side=conflicting_side):
                        manifest = copy.deepcopy(self.manifest)
                        result = manifest["recovery"][role]["result"]
                        result["identity"] = {field: result[field] for field in keys}
                        target = result if conflicting_side == "flat" else result["identity"]
                        target[key] = sha(b"conflicting duplicate")
                        with self.assertRaises(ValueError):
                            self.validate(manifest)

    def test_complete_flat_identity_cannot_hide_invalid_or_inexact_nested_identity(self):
        identity = {**self.record["identity"],
                    "correctedAcceptanceSha256": sha(self.corrected.encode())}
        for label, nested in (
            ("null", None), ("list", list(identity.items())), ("string", "identity"),
            ("empty", {}),
            ("missing key", {key: value for key, value in identity.items() if key != "patchSha256"}),
            ("extra key", {**identity, "unrecognizedSha256": sha(b"extra")}),
            ("schema inside identity", {**identity, "schemaVersion": 1}),
        ):
            with self.subTest(nested=label):
                manifest = copy.deepcopy(self.manifest)
                manifest["recovery"]["review"]["result"]["identity"] = nested
                with self.assertRaises(ValueError):
                    self.validate(manifest)

    def test_recovery_revalidates_every_identity_hash_and_role_binding(self):
        for key in self.record["identity"]:
            with self.subTest(identity=key):
                manifest = copy.deepcopy(self.manifest)
                manifest["recovery"]["identity"][key] = "stale"
                with self.assertRaises(ValueError):
                    self.validate(manifest)
            for role in ("diagnosis", "author", "review"):
                with self.subTest(role=role, identity=key):
                    manifest = copy.deepcopy(self.manifest)
                    manifest["recovery"][role]["result"][key] = "stale"
                    with self.assertRaises(ValueError):
                        self.validate(manifest)

    def test_recovery_rejects_changed_original_corrected_or_reviewed_spec_bytes(self):
        for target in ("original spec", "original hash", "corrected hash", "review hash"):
            with self.subTest(target=target):
                manifest = copy.deepcopy(self.manifest)
                if target == "original spec":
                    manifest["recovery"]["original"]["spec"] += "\n// unreviewed"
                elif target == "original hash":
                    manifest["recovery"]["original"]["manifest"]["acceptanceSha256"] = sha(b"other")
                elif target == "corrected hash":
                    manifest["acceptanceSha256"] = sha(b"other")
                else:
                    manifest["recovery"]["review"]["result"]["correctedAcceptanceSha256"] = sha(b"other")
                with self.assertRaises(ValueError):
                    self.validate(manifest)
        with self.assertRaises(ValueError):
            self.validate(spec=self.corrected + "\n// unreviewed")

    def test_recovery_preserves_prd_contract_plan_case_ids_and_browser_coverage(self):
        for key in (
            "schemaVersion", "eventId", "baseCommit", "planSha256", "prdSha256",
            "contractSha256", "cases", "status", "acceptanceIds",
            "browserJourney", "browserJourneySha256",
        ):
            with self.subTest(key=key):
                manifest = copy.deepcopy(self.manifest)
                manifest[key] = "changed frozen input"
                with self.assertRaises(ValueError):
                    self.validate(manifest)
        for field in ("title", "acceptanceIds", "purpose"):
            with self.subTest(case_field=field):
                manifest = copy.deepcopy(self.manifest)
                manifest["cases"][1][field] = "changed passing case"
                with self.assertRaises(ValueError):
                    self.validate(manifest)
        with self.assertRaises(ValueError):
            recovery.validate_recovery(self.manifest, self.corrected, {"AC-1", "AC-2"})

    def test_recovery_rejects_tampered_original_checks_and_manifest_hash(self):
        for key in ("baseCommit", "patchSha256", "acceptanceSha256", "errors"):
            with self.subTest(check_field=key):
                manifest = copy.deepcopy(self.manifest)
                manifest["recovery"]["checks"][key] = "tampered evidence"
                with self.assertRaises(ValueError):
                    self.validate(manifest)
        manifest = copy.deepcopy(self.manifest)
        manifest["recovery"]["original"]["manifest"]["sessionId"] = "sess_other_original"
        with self.assertRaises(ValueError):
            self.validate(manifest)

    def test_original_code_review_must_bind_the_same_patch_and_original_spec(self):
        for key in ("patchSha256", "acceptanceSha256"):
            for missing in (False, True):
                with self.subTest(key=key, missing=missing):
                    manifest = copy.deepcopy(self.manifest)
                    review = manifest["recovery"]["codeReview"]
                    if missing:
                        review.pop(key)
                    else:
                        review[key] = sha(b"another candidate or acceptance bundle")
                    with self.assertRaises(ValueError):
                        self.validate(manifest)

    def test_recovery_requires_three_independent_sessions_and_turns(self):
        for role in ("diagnosis", "author", "review"):
            for session in ("sess_original_author", "sess_implementer", "sess_code_reviewer", "", None):
                with self.subTest(role=role, session=session):
                    manifest = copy.deepcopy(self.manifest)
                    manifest["recovery"][role]["sessionId"] = session
                    with self.assertRaises(ValueError):
                        self.validate(manifest, excluded_sessions=("sess_implementer", "sess_code_reviewer"))
            for turn in ("", None, "invalid-turn"):
                with self.subTest(role=role, turn=turn):
                    manifest = copy.deepcopy(self.manifest)
                    manifest["recovery"][role]["turnId"] = turn
                    with self.assertRaises(ValueError):
                        self.validate(manifest)
        for left, right in (("diagnosis", "author"), ("author", "review"), ("diagnosis", "review")):
            for key in ("sessionId", "turnId"):
                with self.subTest(left=left, right=right, key=key):
                    manifest = copy.deepcopy(self.manifest)
                    manifest["recovery"][right][key] = manifest["recovery"][left][key]
                    with self.assertRaises(ValueError):
                        self.validate(manifest)
        for key in ("sessionId", "turnId"):
            with self.subTest(manifest_author=key):
                manifest = copy.deepcopy(self.manifest)
                manifest[key] = "different"
                with self.assertRaises(ValueError):
                    self.validate(manifest)

    def test_preservation_requires_positive_reasoned_assessment_for_every_case(self):
        mutations = {
            "missing passing case": lambda a: a["caseAssessments"].pop(),
            "duplicate assessment": lambda a: a["caseAssessments"].__setitem__(
                2, copy.deepcopy(a["caseAssessments"][0])),
            "unknown title": lambda a: a["caseAssessments"][1].update(title="another case"),
            "negative assessment": lambda a: a["caseAssessments"][1].update(preserved=False),
            "string boolean": lambda a: a["caseAssessments"][1].update(preserved="true"),
            "empty reason": lambda a: a["caseAssessments"][1].update(reason=" "),
            "empty rationale": lambda a: a.update(rationale=" "),
            "declined": lambda a: a.update(verdict="reject"),
            "criteria not preserved": lambda a: a.update(preservesAllCriteria=False),
            "application edit": lambda a: a.update(changesOnlyTestDefects=False),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                manifest = copy.deepcopy(self.manifest)
                mutate(manifest["recovery"]["review"]["result"])
                with self.assertRaises(ValueError):
                    self.validate(manifest)

    def test_recovery_rejects_non_test_diagnoses_and_a_second_automatic_correction(self):
        for classification in ("application_defect", "uncertain"):
            with self.subTest(classification=classification):
                manifest = copy.deepcopy(self.manifest)
                manifest["recovery"]["diagnosis"]["result"]["classification"] = classification
                with self.assertRaises(ValueError):
                    self.validate(manifest)
        manifest = copy.deepcopy(self.manifest)
        manifest["recovery"]["original"]["manifest"]["recovery"] = {}
        with self.assertRaises(ValueError):
            self.validate(manifest)

    def test_unrecovered_manifest_needs_no_recovery_audit(self):
        self.assertIsNone(recovery.validate_recovery(self.fixture["manifest"], SPEC, self.ids))

    def test_recover_diagnoses_but_does_not_author_application_defects_or_uncertainty(self):
        for classification in ("application_defect", "uncertain"):
            with self.subTest(classification=classification), tempfile.TemporaryDirectory() as directory:
                role = copy.deepcopy(self.record["diagnosis"])
                role["result"].update(classification=classification,
                                      evidence="The available evidence does not establish a test defect.")
                stages = Mock(spec=FeatureStages)
                stages.run.return_value = stage_response(role)
                snapshot = copy.deepcopy(self.fixture)
                result, diagnosis = self.invoke(stages, directory)
                self.assertIsNone(result)
                self.assertEqual(diagnosis, role)
                stages.run.assert_called_once()
                self.assertTrue(stages.run.call_args.args[0].startswith("test-diagnosis-"))
                self.assertEqual(stages.run.call_args.kwargs["archive"], b"candidate archive")
                self.assertEqual(self.fixture, snapshot)
                self.assertEqual(json.loads((Path(directory) / "diagnosis.json").read_text()), role)
                self.assertFalse((Path(directory) / "proposal.json").exists())

    def test_recover_uses_candidate_only_for_diagnosis_and_baseline_for_author_and_audit(self):
        stages = Mock(spec=FeatureStages)
        stages.run.side_effect = [stage_response(self.record[role])
                                  for role in ("diagnosis", "author", "review")]
        snapshot = copy.deepcopy(self.fixture)
        with tempfile.TemporaryDirectory() as directory:
            result, diagnosis = self.invoke(stages, directory)
            self.assertEqual(result, {"manifest": self.manifest, "spec": self.corrected})
            self.assertEqual(diagnosis, self.record["diagnosis"])
            self.assertEqual(json.loads((Path(directory) / "proposal.json").read_text()), self.record)
        self.assertEqual(self.fixture, snapshot)
        self.assertEqual(stages.run.call_count, 3)
        for call, name, archive in zip(
            stages.run.call_args_list, ("diagnosis", "repair", "review"),
            (b"candidate archive", b"baseline archive", b"baseline archive"),
        ):
            self.assertTrue(call.args[0].startswith(f"test-{name}-"))
            self.assertEqual(call.kwargs["archive"], archive)
            for text in (self.fixture["prd"], self.fixture["contract"], TITLES[1], TITLES[2]):
                self.assertIn(json.dumps(text)[1:-1], call.args[2])
        self.assertEqual(stages.run.call_args_list[-1].kwargs["extra_identity"],
                         {**self.record["identity"],
                          "correctedAcceptanceSha256": sha(self.corrected.encode())})

    def test_recover_accepts_live_evidence_list_and_nested_preservation_identity(self):
        expected = copy.deepcopy(self.manifest)
        record = expected["recovery"]
        diagnosis = record["diagnosis"]["result"]
        diagnosis["evidence"] = ["Tab traversal can leave the document.", diagnosis["evidence"]]
        audit = record["review"]["result"]
        keys = [*self.record["identity"], "correctedAcceptanceSha256"]
        audit["identity"] = {key: audit.pop(key) for key in keys}
        stages = Mock(spec=FeatureStages)
        stages.run.side_effect = [stage_response(record[role])
                                  for role in ("diagnosis", "author", "review")]
        with tempfile.TemporaryDirectory() as directory:
            result, observed_diagnosis = self.invoke(stages, directory)
            self.assertEqual(result, {"manifest": expected, "spec": self.corrected})
            self.assertEqual(observed_diagnosis, record["diagnosis"])
            saved = json.loads((Path(directory) / "proposal.json").read_text())
            self.assertEqual(saved, record)
        self.assertEqual(stages.run.call_count, 3)
        self.validate(result["manifest"], result["spec"])

    def test_recover_preserves_declined_audit_evidence_without_returning_a_replacement(self):
        audit = copy.deepcopy(self.record["review"])
        audit["result"]["verdict"] = "reject"
        stages = Mock(spec=FeatureStages)
        stages.run.side_effect = [stage_response(self.record["diagnosis"]),
                                  stage_response(self.record["author"]), stage_response(audit)]
        snapshot = copy.deepcopy(self.fixture)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.invoke(stages, directory)
            record = json.loads((Path(directory) / "proposal.json").read_text())
            self.assertEqual(record["review"], audit)
            self.assertEqual(record["original"]["spec"], SPEC)
        self.assertEqual(stages.run.call_count, 3)
        self.assertEqual(self.fixture, snapshot)

    def test_recover_rejects_ineligible_checks_and_second_recovery_before_any_stage(self):
        invalid_checks = copy.deepcopy(self.fixture["checks"])
        invalid_checks["infrastructureFailure"] = True
        for overrides in ({"checks": invalid_checks},
                          {"review": {**self.fixture["review"], "verdict": "request_changes"}},
                          {"manifest": self.manifest, "spec": self.corrected}):
            with self.subTest(overrides=list(overrides)), tempfile.TemporaryDirectory() as directory:
                stages = Mock(spec=FeatureStages)
                with self.assertRaises(ValueError):
                    self.invoke(stages, directory, **overrides)
                stages.run.assert_not_called()
                self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
