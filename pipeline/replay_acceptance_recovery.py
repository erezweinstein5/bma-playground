"""Linux regression replay of issue 25's frozen BMA test correction.

No AWS, GitHub token or agent invocation. Each run uses a disposable checkout
and the same controller runner as feature delivery. Artifacts retain failures.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from acceptance_recovery import require, validate_recovery
from feature_checks import check_candidate
from feature_stages import digest


def baseline_patch(source, base):
    with tempfile.TemporaryDirectory(prefix="bma-baseline-replay-") as temporary:
        repo = Path(temporary) / "repo"
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(source), str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", "--detach", base], check=True)
        path = repo / "src/main.js"
        path.write_bytes(path.read_bytes() + b"\n// Acceptance recovery negative control.\n")
        return subprocess.check_output(["git", "-C", str(repo), "diff", "--", "src/main.js"])


def replay(output, summary):
    source = Path(__file__).resolve().parent.parent
    fixture = source / "tests/fixtures/acceptance-recovery"
    bundle = json.loads((fixture / "reviewed-bundle.json").read_text())
    manifest, spec = bundle["manifest"], bundle["spec"]
    ids = manifest["acceptanceIds"]
    validate_recovery(manifest, spec, ids)
    record = manifest["recovery"]
    patch = (fixture / "rollback.patch").read_bytes()
    require(digest(patch) == record["identity"]["patchSha256"], "Replay patch identity changed")
    scenarios = [
        ("original-failure", patch, record["original"]["manifest"], record["original"]["spec"]),
        ("corrected-suite", patch, manifest, spec),
        ("feature-still-present", baseline_patch(source, manifest["baseCommit"]), manifest, spec),
    ]
    results = {}
    for name, candidate, selected, acceptance in scenarios:
        path = output / f"{name}.spec.js"
        path.write_text(acceptance)
        checks = check_candidate(source, manifest["baseCommit"], candidate, path,
                                 output / name, selected["acceptanceSha256"],
                                 expected_titles=[row["title"] for row in selected["cases"]],
                                 required_ids=ids)
        results[name] = checks
        summary["scenarios"][name] = {
            "passed": checks["passed"], "executed": checks["executedFeatureCases"],
            "failedTitles": [row["title"] for row in checks["featureCases"] if not row["passed"]],
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps({"scenario": name, "passed": checks["passed"],
                          "executed": checks["executedFeatureCases"],
                          "failedTitles": [row["title"] for row in checks["featureCases"]
                                           if not row["passed"]]}), flush=True)
    # Do not make a failed run look successful merely because it was expected
    # to fail: require the exact feature-level observations and all setup gates.
    failed_titles = lambda result: {row["title"] for row in result["featureCases"] if not row["passed"]}
    affected = set(record["diagnosis"]["result"]["affectedTitles"])
    for result in results.values():
        require(result["infrastructureFailure"] is False
                and result["executedFeatureCases"] == len(manifest["cases"])
                and all(row["status"] == "passed" for row in result["commands"]
                        if row["name"] != "feature-browser"),
                "Replay failed outside the intended feature assertions")
    for name in ("original-failure", "feature-still-present"):
        result = results[name]
        command = next(row for row in result["commands"] if row["name"] == "feature-browser")
        require(command["status"] == "failed" and command["exitCode"] == 1
                and command["timedOut"] is False
                and all(row["executed"] is True and row["outcomes"] in (["passed"], ["failed"])
                        for row in result["featureCases"])
                and json.loads(Path(result["featureReport"]).read_text()).get("errors") == [],
                "Expected regression must fail normal assertions, not timeout or crash the runner")
    require(results["original-failure"]["passed"] is False
            and failed_titles(results["original-failure"]) == affected,
            "Original fixture did not reproduce the exact diagnosed failure")
    require(results["corrected-suite"]["passed"] is True,
            "Independently corrected suite did not pass every case")
    require(results["feature-still-present"]["passed"] is False
            and affected <= failed_titles(results["feature-still-present"]),
            "Corrected suite failed to reject the unchanged feature")
    summary.update({
        "passed": True, "status": "passed", "sourceIssue": 25, "sourceRun": 39244331833,
        "patchSha256": digest(patch), "acceptanceSha256": manifest["acceptanceSha256"],
        "scenarios": {key: {"passed": value["passed"],
                            "executed": value["executedFeatureCases"],
                            "failedTitles": sorted(failed_titles(value))}
                      for key, value in results.items()},
    })
    print("Acceptance recovery replay passed: original failure reproduced, corrected suite passed, regression rejected.")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/acceptance-recovery-replay"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"passed": False, "status": "running", "scenarios": {}}
    summary_path = args.output / "summary.json"
    # Invalidate an earlier success before reading or running anything.
    summary_path.write_text(json.dumps(summary, indent=2))
    try:
        return replay(args.output, summary)
    except Exception as error:
        summary.update(passed=False, status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        summary_path.write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    sys.exit(main())
