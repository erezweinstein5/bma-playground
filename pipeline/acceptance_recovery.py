"""Bounded, independently reviewed repairs to controller-owned acceptance tests.

The implementer never edits a test. Original bytes, failed checks, diagnosis,
explicit replacements and a separate preservation review travel with the new
manifest. Every original case must still execute against the unchanged patch.
"""
import json
from pathlib import Path
import re

from design_board import canonical_json
from feature_stages import digest, final_json, validate_acceptance


COMMANDS = {"canonical-patch", "clone", "checkout-base", "verify-base",
            "apply-candidate", "protected-inputs", "npm-ci", "npm-test",
            "npm-build", "board-browser", "repair-browser", "feature-browser"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def failure_evidence(checks):
    """Stable failure identity; omit run directories, ports, timing and prose."""
    return {
        "commands": sorted((row["name"], row["status"], row.get("exitCode"),
                            row.get("timedOut", False))
                           for row in checks.get("commands", []) if row["status"] != "passed"),
        "cases": sorted((row["title"], row.get("project", ""),
                         tuple(row.get("outcomes", [])))
                        for row in checks.get("featureCases", []) if not row.get("passed")),
    }


def unchanged_failure(previous, current):
    return (not current["passed"] and not previous["passed"]
            and previous["patchSha256"] == current["patchSha256"]
            and previous["acceptanceSha256"] == current["acceptanceSha256"]
            and failure_evidence(previous["checks"]) == failure_evidence(current["checks"]))


def eligible(checks, review):
    """Never route build/regression/setup failures or blocking code review to test repair."""
    rows = checks.get("commands", [])
    return (checks.get("passed") is False
            and checks.get("infrastructureFailure") is False
            and review.get("verdict") == "approve"
            and len(rows) == len(COMMANDS)
            and {row.get("name") for row in rows} == COMMANDS
            and all(row.get("status") == "passed" and row.get("exitCode") == 0
                    and row.get("timedOut") is False
                    for row in rows if row["name"] != "feature-browser")
            and any(row["name"] == "feature-browser" and row["status"] == "failed"
                    and row.get("exitCode") == 1 and row.get("timedOut") is False for row in rows)
            and bool(checks.get("featureCases"))
            and any(row.get("passed") is False for row in checks["featureCases"]))


def bound(value, identity):
    require(isinstance(value, dict) and type(value.get("schemaVersion")) is int
            and value.get("schemaVersion") == 1,
            "Acceptance recovery response has a different input identity")
    # BMA may echo the supplied identity object or its fields at the top level.
    # Both representations must bind every hash; conflicting duplicates fail.
    echoed = value.get("identity", value)
    require(isinstance(echoed, dict)
            and all(echoed.get(key) == item for key, item in identity.items())
            and ("identity" not in value or set(echoed) == set(identity))
            and all(key not in value or value[key] == item for key, item in identity.items()),
            "Acceptance recovery response has a different input identity")


def validate_diagnosis(value, identity, manifest, checks):
    bound(value, identity)
    evidence = value.get("evidence")
    evidence_rows = [evidence] if isinstance(evidence, str) else evidence
    require(value.get("classification") in {"test_defect", "application_defect", "uncertain"}
            and isinstance(evidence_rows, list) and 1 <= len(evidence_rows) <= 12
            and all(isinstance(row, str) and 0 < len(row.strip()) <= 10000 for row in evidence_rows),
            "Acceptance diagnosis must explain its classification")
    titles = value.get("affectedTitles")
    failed = {case["title"] for case in checks["featureCases"] if case.get("passed") is False}
    require(isinstance(titles, list) and all(isinstance(title, str) for title in titles)
            and len(titles) == len(set(titles)) and set(titles) <= failed
            and set(titles) <= {case["title"] for case in manifest["cases"]},
            "Diagnosis must identify actual failing frozen cases")
    if value["classification"] == "test_defect":
        require(bool(titles), "Test defect diagnosis needs affected cases")
    return value


def apply_replacements(spec, proposal, identity, manifest, required_ids):
    bound(proposal, identity)
    edits = proposal.get("replacements")
    require(isinstance(edits, list) and 1 <= len(edits) <= 5,
            "Acceptance correction requires 1-5 explicit replacements")
    updated = spec
    for edit in edits:
        require(isinstance(edit, dict)
                and all(isinstance(edit.get(key), str) and edit[key].strip()
                        for key in ("old", "new", "reason"))
                and edit["old"] != edit["new"]
                and max(len(edit["old"]), len(edit["new"])) <= 5000
                and spec.count(edit["old"]) == 1 and updated.count(edit["old"]) == 1,
                "Acceptance replacement must uniquely identify a bounded existing fragment")
        # Case declarations and their identity cannot be changed by the repair author.
        require(not re.search(r"\btest\s*(?:\.\s*describe)?\s*\(", edit["old"] + edit["new"]),
                "Acceptance correction cannot replace test declarations")
        updated = updated.replace(edit["old"], edit["new"], 1)
    validate_acceptance({**manifest, "spec": updated}, required_ids)
    declarations = r"""\btest\s*\(\s*(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`)"""
    require(re.findall(declarations, updated) == re.findall(declarations, spec),
            "Acceptance correction changed executable test titles")
    require(updated != spec, "Acceptance correction made no change")
    return updated


def validate_preservation(value, identity, cases):
    bound(value, identity)
    require(value.get("verdict") in {"approve", "reject"}
            and isinstance(value.get("rationale"), str) and value["rationale"].strip(),
            "Acceptance preservation review must explain its verdict")
    require(value["verdict"] == "approve"
            and value.get("preservesAllCriteria") is True
            and value.get("changesOnlyTestDefects") is True,
            "Independent reviewer did not approve preservation of acceptance requirements")
    rows = value.get("caseAssessments")
    require(isinstance(rows, list) and len(rows) == len(cases)
            and all(isinstance(row, dict) and isinstance(row.get("title"), str)
                    and row.get("preserved") is True
                    and isinstance(row.get("reason"), str) and row["reason"].strip() for row in rows)
            and {row["title"] for row in rows} == {case["title"] for case in cases},
            "Independent review must account for every original acceptance case")
    return value


def validate_recovery(manifest, spec, required_ids=None, excluded_sessions=()):
    """Used again by browser, PR publication, CI and live gates."""
    recovery = manifest.get("recovery")
    if recovery is None:
        return
    require(isinstance(recovery, dict) and recovery.get("schemaVersion") == 1,
            "Invalid acceptance recovery provenance")
    original = recovery["original"]
    parent, old_spec = original["manifest"], original["spec"]
    require("recovery" not in parent, "Only one automatic acceptance correction is allowed")
    validate_acceptance({**parent, "spec": old_spec}, required_ids)
    require(digest(old_spec.encode()) == parent["acceptanceSha256"],
            "Original acceptance bytes changed")
    for key in ("schemaVersion", "eventId", "baseCommit", "planSha256", "prdSha256",
                "contractSha256", "cases", "status", "acceptanceIds",
                "browserJourney", "browserJourneySha256"):
        require(manifest.get(key) == parent.get(key),
                "Acceptance recovery changed the frozen contract or cases")
    identity = recovery["identity"]
    require(identity == {
        "baseCommit": parent["baseCommit"], "planSha256": parent["planSha256"],
        "prdSha256": parent["prdSha256"], "acceptanceSha256": parent["acceptanceSha256"],
        "acceptanceManifestSha256": digest(canonical_json(parent)),
        "patchSha256": recovery["checks"]["patchSha256"],
        "checksSha256": digest(canonical_json(recovery["checks"])),
    }, "Acceptance recovery identity changed")
    require(recovery["checks"].get("baseCommit") == parent["baseCommit"]
            and recovery["checks"].get("acceptanceSha256") == parent["acceptanceSha256"]
            and eligible(recovery["checks"], recovery["codeReview"]),
            "Acceptance recovery lacks eligible original failed checks")
    code_review = recovery["codeReview"]
    require(code_review.get("patchSha256") == identity["patchSha256"]
            and code_review.get("acceptanceSha256") == parent["acceptanceSha256"]
            and isinstance(code_review.get("findings"), list)
            and all(isinstance(row, dict) and row.get("severity") == "minor"
                    for row in code_review["findings"]),
            "Original code review does not approve the same failed candidate")
    diagnosis = validate_diagnosis(recovery["diagnosis"]["result"], identity, parent, recovery["checks"])
    require(diagnosis["classification"] == "test_defect", "Recovery was not diagnosed as a test defect")
    require(apply_replacements(old_spec, recovery["author"]["result"], identity,
                               parent, required_ids) == spec,
            "Corrected acceptance bytes differ from reviewed replacements")
    reviewed_identity = {**identity, "correctedAcceptanceSha256": digest(spec.encode())}
    validate_preservation(recovery["review"]["result"], reviewed_identity, parent["cases"])
    roles = [recovery[key] for key in ("diagnosis", "author", "review")]
    sessions = [role.get("sessionId") for role in roles]
    turns = [role.get("turnId") for role in roles]
    require(all(isinstance(item, str) and item.startswith("sess_") for item in sessions)
            and all(isinstance(item, str) and item.startswith("turn_") for item in turns)
            and len(set(sessions)) == 3 and len(set(turns)) == 3
            and not set(sessions) & {parent["sessionId"], *excluded_sessions},
            "Acceptance diagnosis, repair and review must use independent sessions")
    require(manifest["sessionId"] == recovery["author"]["sessionId"]
            and manifest["turnId"] == recovery["author"]["turnId"]
            and manifest["acceptanceSha256"] == digest(spec.encode()),
            "Corrected acceptance author or digest mismatch")


def recover(stages, *, prd, plan, contract, manifest, spec, checks, review,
            candidate_archive, base_archive, directory, required_ids,
            excluded_sessions=()):
    """One repair proposal and one fresh preservation review, never a retry-until-green."""
    require(eligible(checks, review), "Only isolated feature test failures can enter acceptance diagnosis")
    require("recovery" not in manifest, "Automatic acceptance correction already used")
    identity = {
        "baseCommit": manifest["baseCommit"], "planSha256": manifest["planSha256"],
        "prdSha256": manifest["prdSha256"], "acceptanceSha256": manifest["acceptanceSha256"],
        "acceptanceManifestSha256": digest(canonical_json(manifest)),
        "patchSha256": checks["patchSha256"], "checksSha256": digest(canonical_json(checks)),
    }
    suffix = checks["patchSha256"][:12] + "-" + manifest["acceptanceSha256"][:12]
    context = json.dumps({"identity": identity, "prd": prd, "plan": plan, "uiContract": contract,
                          "cases": manifest["cases"], "spec": spec, "checks": checks,
                          "codeReview": review}, ensure_ascii=False)

    def run(role, instructions, prompt, archive, role_identity):
        state, items = stages.run("test-" + role + "-" + suffix, instructions, prompt,
                                  archive=archive, extra_identity=role_identity)
        return {"sessionId": state["sessionId"], "turnId": state["outcome"]["turnId"],
                "result": final_json(items, state["outcome"]["turnId"])}

    diagnosis = run("diagnosis",
        "Independently diagnose a failed acceptance gate. Read-only source inspection. "
        "Do not edit files, access credentials/GitHub, implement code, or approve a release.",
        "Decide whether the observed failure is caused by the generated test itself, "
        "the application, or insufficient evidence. A timeout alone proves no test defect. "
        "Use the pinned PRD/contract and actual failure evidence; the code review is an "
        "opinion to check, not authority. Return ONLY JSON with schemaVersion=1, every "
        "field from identity, classification='test_defect'|'application_defect'|'uncertain', "
        "affectedTitles (exact failing test titles) and evidence (specific explanation "
        "including why a conforming application can fail this test).\nINPUT:\n" + context,
        candidate_archive, identity)
    result = validate_diagnosis(diagnosis["result"], identity, manifest, checks)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "diagnosis.json").write_text(json.dumps(diagnosis, indent=2))
    if result["classification"] != "test_defect":
        return None, diagnosis
    author = run("repair",
        "You independently repair a demonstrated test-authoring defect. Inspect baseline "
        "source only. Do not implement application code, edit files, access credentials "
        "or GitHub. Preserve all product requirements and existing test coverage.",
        "Return ONLY JSON with schemaVersion=1, every identity field, and replacements "
        "(1-5 {old,new,reason} objects). Each old must match one exact source fragment. "
        "Correct ONLY the diagnosed test mechanics; preserve case declarations, every "
        "asserted requirement, negative paths, widths/views, persistence and regressions. "
        "No skipping, deleting cases, catches, conditional assertion escapes, arbitrary "
        "sleeps or assertions made to match the current implementation. Changes to a "
        "shared helper must preserve all its callers. Independent review will reject "
        "weakened or unrelated edits. The implementation patch is not supplied.\n"
        + json.dumps({"identity": identity, "prd": prd, "plan": plan,
                      "uiContract": contract, "cases": manifest["cases"], "spec": spec,
                      "diagnosis": diagnosis["result"]}, ensure_ascii=False),
        base_archive, identity)
    corrected = apply_replacements(spec, author["result"], identity, manifest, required_ids)
    updated_identity = {**identity, "correctedAcceptanceSha256": digest(corrected.encode())}
    audit = run("review",
        "You are a fresh independent acceptance preservation reviewer. Read-only baseline "
        "inspection; no edits, implementation, credentials or GitHub. Reject any weakening.",
        "Review the original and proposed tests against the unchanged PRD and contract. "
        "Approve only narrowly justified test-mechanics corrections, with every original "
        "criterion and case preserved. Check shared helpers and negative assertions for "
        "vacuous success or conditional escapes. No application patch is supplied. "
        "Return ONLY JSON with schemaVersion=1, every identity field below, "
        "verdict='approve'|'reject', preservesAllCriteria (boolean), changesOnlyTestDefects "
        "(boolean), rationale, and caseAssessments (one {title,preserved,reason} for EVERY "
        "original case). Reject uncertainty; do not claim tests have executed.\n"
        + json.dumps({"identity": updated_identity, "prd": prd, "plan": plan,
                      "uiContract": contract, "cases": manifest["cases"],
                      "originalSpec": spec, "correctedSpec": corrected,
                      "proposal": author["result"], "diagnosis": diagnosis["result"]},
                     ensure_ascii=False),
        base_archive, updated_identity)
    # Save rejected/invalid work too; it never replaces the old acceptance artifact.
    record = {"schemaVersion": 1, "identity": identity,
              "original": {"manifest": manifest, "spec": spec},
              "checks": checks, "codeReview": review,
              "diagnosis": diagnosis, "author": author, "review": audit}
    (directory / "proposal.json").write_text(json.dumps(record, indent=2))
    new_manifest = {**manifest, "acceptanceSha256": digest(corrected.encode()),
                    "sessionId": author["sessionId"], "turnId": author["turnId"], "recovery": record}
    validate_recovery(new_manifest, corrected, required_ids, excluded_sessions)
    return {"manifest": new_manifest, "spec": corrected}, diagnosis


def freeze_bundle(stages, root, bundle, contract):
    manifest, spec = bundle["manifest"], bundle["spec"]
    folder = Path(root) / "acceptance-revisions" / manifest["acceptanceSha256"]
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in (("manifest.json", json.dumps(manifest, indent=2).encode()),
                       ("workload.acceptance.spec.js", spec.encode()),
                       ("ui-contract.md", contract.encode())):
        path = folder / name
        require(not path.exists() or path.read_bytes() == data, "Frozen acceptance revision changed")
        path.write_bytes(data)
    stages.freeze(f"sources/{stages.event}/acceptance-{manifest['acceptanceSha256']}.json",
                  json.dumps(bundle).encode(), "application/json")
    return folder


def select_bundle(stages, root, original_path, original_manifest, result, contract, required_ids):
    if result["acceptanceSha256"] == original_manifest["acceptanceSha256"]:
        return original_path, original_manifest
    sha = result["acceptanceSha256"]
    require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha),
            "Invalid selected acceptance digest")
    raw = stages.read(f"sources/{stages.event}/acceptance-{sha}.json", limit=1_000_000)
    bundle = json.loads(raw)
    manifest = bundle["manifest"]
    require(manifest["acceptanceSha256"] == sha
            and manifest.get("recovery", {}).get("original", {}).get("manifest") == original_manifest,
            "Selected acceptance revision has a different original bundle")
    validate_recovery(manifest, bundle["spec"], required_ids)
    return freeze_bundle(stages, root, bundle, contract), manifest
