"""Exercise recovery routing, durable replay and the unchanged-candidate stop."""
import copy
from contextlib import chdir
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import feature_cycle as cycle
from design_board import canonical_json, validate_plan
from feature_stages import digest
from test_acceptance_recovery import issue25_fixture, recovered_fixture, stage_response
from test_design_board import search_plan


def passing(checks, manifest):
    value = copy.deepcopy(checks)
    value.update(passed=True, status="passed", errors=[],
                 acceptanceSha256=manifest["acceptanceSha256"],
                 actualAcceptanceSha256=manifest["acceptanceSha256"],
                 coveredAcceptanceIds=manifest["acceptanceIds"])
    for row in value["commands"]:
        row.update(status="passed", exitCode=0, timedOut=False)
    for row in value["featureCases"]:
        row.update(status="expected", outcomes=["passed"], errors=[], observations=[], passed=True)
    return value


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.event = "probe-cycle-recovery"
        self.folder = self.root / "artifacts" / self.event
        self.planning = self.folder / "design"
        self.acceptance = self.folder / "acceptance"
        self.planning.mkdir(parents=True)
        self.acceptance.mkdir()
        self.fixture = issue25_fixture()
        plan = search_plan()
        plan.update(uiContract=self.fixture["contract"],
                    requirements=[{**row, "verification": row["behavior"]}
                                  for row in self.fixture["plan"]["requirements"]])
        self.fixture["plan"] = validate_plan(plan)
        manifest = self.fixture["manifest"]
        manifest.update(eventId=self.event, planSha256=digest(canonical_json(plan)),
                        browserJourney=plan["browserJourney"],
                        browserJourneySha256=digest(canonical_json(plan["browserJourney"])))
        self.revised_manifest, self.revised_spec = recovered_fixture(self.fixture)
        self.revised = {"manifest": self.revised_manifest, "spec": self.revised_spec}
        self.provenance = {
            "schemaVersion": 2, "planSchemaVersion": 2, "status": "planned",
            "eventId": self.event, "baseCommit": manifest["baseCommit"],
            "planSha256": manifest["planSha256"], "prdSha256": manifest["prdSha256"],
            "sessionId": "sess_planning", "turnId": "turn_planning",
        }
        for name, value in (("plan.json", plan), ("provenance.json", self.provenance)):
            (self.planning / name).write_text(json.dumps(value))
        (self.planning / "prd.md").write_text(self.fixture["prd"])
        (self.acceptance / "manifest.json").write_text(json.dumps(manifest))
        (self.acceptance / "ui-contract.md").write_text(self.fixture["contract"])
        (self.acceptance / "workload.acceptance.spec.js").write_text(self.fixture["spec"])
        self.stage = Mock(event=self.event, bucket="bucket")
        self.cloud = {}
        self.stage.read.side_effect = lambda key, **kwargs: self.cloud.get(key)

        def freeze(key, data, *args):
            if key in self.cloud:
                self.assertEqual(self.cloud[key], data)
            self.cloud[key] = data
        self.stage.freeze.side_effect = freeze
        self.stage.s3.put_object.side_effect = lambda **kwargs: self.cloud.update(
            {kwargs["Key"]: kwargs["Body"]})
        role = {"sessionId": "sess_implementation", "turnId": "turn_implementation",
                "result": {"schemaVersion": 1, "summary": "Candidate exported"}}
        self.stage.run.return_value = stage_response(role)
        self.stage.continue_implementation.return_value = stage_response(
            {**role, "turnId": "turn_correction"})
        self.patch_bytes = b"unchanged application patch"
        self.output = self.root / "github-output"

    def evaluate(self, *, manifest, **kwargs):
        checks = (passing(self.fixture["checks"], manifest) if "recovery" in manifest
                  else copy.deepcopy(self.fixture["checks"]))
        review = {**self.fixture["review"], "acceptanceSha256": manifest["acceptanceSha256"]}
        state = {"sessionId": "sess_code_recheck" if "recovery" in manifest else "sess_code_initial",
                 "outcome": {"turnId": "turn_code_recheck" if "recovery" in manifest else "turn_code_initial"}}
        return checks, review, state

    def run_cycle(self, evaluator=None, recovery=None):
        with chdir(self.root), patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.output)}, clear=True), \
             patch.object(sys, "argv", ["cycle", "--planning", str(self.planning), "--event", self.event]), \
             patch.object(cycle, "FeatureStages", return_value=self.stage), \
             patch.object(cycle, "source_archive", return_value=b"baseline"), \
             patch.object(cycle, "review_archive", return_value=(b"candidate archive", "b" * 40)), \
             patch.object(cycle, "collect_patch", return_value=(self.patch_bytes, {})), \
             patch.object(cycle, "evaluate_candidate", side_effect=evaluator or self.evaluate) as evaluate, \
             patch.object(cycle, "recover", side_effect=recovery) as repair:
            if recovery is None:
                repair.return_value = (self.revised, self.revised_manifest["recovery"]["diagnosis"])
            self.last_evaluate, self.last_repair = evaluate, repair
            return cycle.main()

    def test_test_defect_rechecks_same_patch_preserves_original_and_selects_new_bundle(self):
        original = (self.acceptance / "workload.acceptance.spec.js").read_bytes()
        self.assertEqual(self.run_cycle(), 0)
        self.stage.continue_implementation.assert_not_called()
        self.assertEqual(self.last_evaluate.call_count, 2)
        for call in self.last_evaluate.call_args_list:
            self.assertEqual(call.kwargs["patch"], self.patch_bytes)
        self.assertEqual((self.acceptance / "workload.acceptance.spec.js").read_bytes(), original)
        candidate = json.loads((self.folder / "candidate.json").read_text())
        self.assertEqual(candidate["acceptanceSha256"], self.revised_manifest["acceptanceSha256"])
        self.assertEqual(candidate["patchSha256"], digest(self.patch_bytes))
        self.assertIn("acceptance-revisions/" + candidate["acceptanceSha256"], self.output.read_text())
        summary = json.loads((self.folder / "cycle.json").read_text())
        self.assertEqual(summary["identity"]["acceptanceSha256"], self.fixture["manifest"]["acceptanceSha256"])
        self.assertEqual(len(summary["candidates"]), 1)
        self.assertEqual(summary["status"], "checks-and-review-passed")

    def test_rerun_restores_selected_bundle_and_candidate_without_reexecution(self):
        self.run_cycle()
        # Simulate loss of all local implementation/recovery outputs on a new runner.
        import shutil
        shutil.rmtree(self.folder / "candidate-0")
        shutil.rmtree(self.folder / "acceptance-revisions")
        (self.folder / "cycle.json").unlink()
        self.assertEqual(self.run_cycle(), 0)
        self.last_evaluate.assert_not_called()
        self.last_repair.assert_not_called()
        self.stage.continue_implementation.assert_not_called()
        self.assertEqual(json.loads((self.folder / "candidate.json").read_text())["acceptanceSha256"],
                         self.revised_manifest["acceptanceSha256"])

    def test_uncertain_diagnosis_stops_without_code_correction_or_candidate_publication(self):
        diagnosis = copy.deepcopy(self.revised_manifest["recovery"]["diagnosis"])
        diagnosis["result"]["classification"] = "uncertain"
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            self.run_cycle(recovery=lambda *args, **kwargs: (None, diagnosis))
        self.stage.continue_implementation.assert_not_called()
        self.assertFalse((self.folder / "candidate.json").exists())
        self.assertFalse(self.output.exists())

    def test_unchanged_real_application_failure_stops_after_one_correction(self):
        def evaluate(**kwargs):
            checks, review, state = self.evaluate(**kwargs)
            review.update(verdict="request_changes", findings=[{
                "severity": "major", "path": "src/main.js", "line": 1, "acceptanceIds": ["AC-1"],
                "title": "Control remains", "evidence": "Forbidden button is still present",
                "requestedChange": "Remove the control",
            }])
            return checks, review, state
        with self.assertRaisesRegex(RuntimeError, "Unchanged patch"):
            self.run_cycle(evaluator=evaluate)
        self.stage.continue_implementation.assert_called_once()
        self.last_repair.assert_not_called()
        self.assertEqual(self.last_evaluate.call_count, 2)
        self.assertFalse((self.folder / "candidate.json").exists())

    def test_repaired_suite_cannot_loop_until_green(self):
        def evaluate(**kwargs):
            checks, review, state = self.evaluate(**kwargs)
            if "recovery" in kwargs["manifest"]:
                checks = {**self.fixture["checks"],
                          "acceptanceSha256": kwargs["manifest"]["acceptanceSha256"]}
            return checks, review, state
        with self.assertRaisesRegex(RuntimeError, "single automatic repair"):
            self.run_cycle(evaluator=evaluate)
        self.last_repair.assert_called_once()
        self.stage.continue_implementation.assert_not_called()
        self.assertFalse((self.folder / "candidate.json").exists())

    def test_runner_timeout_without_an_application_finding_stops_for_inspection(self):
        def evaluate(**kwargs):
            checks, review, state = self.evaluate(**kwargs)
            command = next(row for row in checks["commands"] if row["name"] == "feature-browser")
            command.update(status="timeout", timedOut=True, exitCode=-9)
            return checks, review, state
        with self.assertRaisesRegex(RuntimeError, "Failed runner or incomplete report"):
            self.run_cycle(evaluator=evaluate)
        self.last_repair.assert_not_called()
        self.stage.continue_implementation.assert_not_called()
        self.assertFalse((self.folder / "candidate.json").exists())

    def test_new_acceptance_version_can_still_require_a_real_application_correction(self):
        evaluations = 0

        def evaluate(**kwargs):
            nonlocal evaluations
            evaluations += 1
            checks, review, state = self.evaluate(**kwargs)
            if evaluations == 2:
                checks = {**self.fixture["checks"],
                          "acceptanceSha256": kwargs["manifest"]["acceptanceSha256"]}
                review.update(verdict="request_changes", findings=[{
                    "severity": "major", "path": "src/main.js", "line": 1, "acceptanceIds": ["AC-1"],
                    "title": "Control remains", "evidence": "Now-observable forbidden control",
                    "requestedChange": "Remove the control",
                }])
            return checks, review, state
        self.assertEqual(self.run_cycle(evaluator=evaluate), 0)
        self.stage.continue_implementation.assert_called_once()
        correction = self.stage.continue_implementation.call_args
        self.assertEqual(correction.args[3]["acceptanceSha256"], self.revised_manifest["acceptanceSha256"])
        self.assertIn(self.revised_spec, json.loads(correction.args[2].split("Feedback:\n", 1)[1])["currentAcceptanceSpec"])
        self.last_repair.assert_called_once()

    def test_evaluation_restores_exact_checks_before_collecting_review(self):
        original = self.fixture["manifest"]
        candidate = self.folder / "eval"
        kwargs = dict(stages=self.stage, candidate=candidate, attempt=0,
            base=original["baseCommit"], patch=self.patch_bytes, patch_sha=digest(self.patch_bytes),
            archive=b"archive", tree="tree", prd=self.fixture["prd"], plan=self.fixture["plan"],
            contract=self.fixture["contract"], manifest=original,
            acceptance_path=self.acceptance / "workload.acceptance.spec.js",
            required_ids=self.fixture["required_ids"])
        self.stage.run.return_value = stage_response({
            "sessionId": "sess_code_initial", "turnId": "turn_code_initial",
            "result": self.fixture["review"]})
        with patch("feature_checks.check_candidate", return_value=self.fixture["checks"]) as check:
            first = cycle.evaluate_candidate(**kwargs)
            (candidate / "checks.json").unlink()
            second = cycle.evaluate_candidate(**kwargs)
        check.assert_called_once()
        self.assertEqual(first, second)
        calls = self.stage.run.call_args_list
        self.assertEqual(calls[-1], calls[-2])

    def test_review_archive_is_independent_of_wall_clock(self):
        repo = self.root / "git-repo"
        repo.mkdir()
        def git(*args):
            return subprocess.check_output(["git", "-C", str(repo), *args])
        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        (repo / "src").mkdir()
        (repo / "src/app.js").write_text("original\n")
        git("add", ".")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD").decode().strip()
        (repo / "src/app.js").write_text("changed\n")
        diff = git("diff")
        archive, tree = cycle.review_archive(repo, base, diff)
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
            self.assertEqual({row.mtime for row in handle.getmembers()}, {0})
        self.assertEqual(cycle.review_archive(repo, base, diff), (archive, tree))


if __name__ == "__main__":
    unittest.main()
