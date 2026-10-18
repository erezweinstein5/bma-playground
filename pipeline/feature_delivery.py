"""Read-only exact-revision feature gate, run from trusted controller code.

CI: checkout the PR head (not GitHub's synthetic merge), fetch its base, and run
``python pipeline/feature_delivery.py ci`` with GITHUB_EVENT_PATH,
GITHUB_REPOSITORY and BMA_PREVIEW_ORIGIN. No GitHub or AWS token is used.

Live: the trusted deployment identifier writes artifacts/deployment.json as the
flat bot marker plus pr, repository, deploySha, and pullRequest (the complete
merged PR API snapshot, including body, user, base/head, merged_at and merge SHA).
Checkout DEPLOY_SHA and fetch the original base, then run this command with
``live``, APP_URL and the same evidence origin. null/ordinary repair metadata
skips. Malformed feature metadata fails. revision.json must serve DEPLOY_SHA
before and after the frozen suite; no local Vite server is used in live mode.

Public artifacts are trusted only at the configured immutable evidence origin.
Hashes bind identities, not signatures. Frozen JS uses the existing validator
and isolated runner; neither is a security sandbox for arbitrary hostile code.
The caller must upload the result directory even on failure.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import requests

from design_board import canonical_json
from feature_checks import (check_candidate, exact_case_errors, https_origin,
                            isolated_env, _run)
from feature_stages import validate_acceptance


BOT = "bma-playground[bot]"
HASH_FIELDS = ("patchSha256", "acceptanceSha256", "contractSha256",
               "planSha256", "prdSha256")
LIMIT = 2 * 1024 * 1024
PATCH_LIMIT = 5 * 1024 * 1024
MARKER = "<!-- bma-playground:"
CI_COMMANDS = {"canonical-patch", "clone", "checkout-base", "verify-base",
               "apply-candidate", "protected-inputs", "npm-ci", "npm-test",
               "npm-build", "board-browser", "repair-browser", "feature-browser"}
LIVE_COMMANDS = CI_COMMANDS - {"npm-test", "npm-build", "board-browser", "repair-browser"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value, length=64):
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None


def digest(value):
    return hashlib.sha256(value).hexdigest()


def json_object(raw):
    def pairs(values):
        obj = {}
        for key, value in values:
            require(key not in obj, "Duplicate JSON field")
            obj[key] = value
        return obj

    def constant(_):
        raise ValueError("Nonfinite JSON value")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    require(isinstance(value, dict), "Expected a JSON object")
    return value


def read_json(path, *, allow_null=False):
    with Path(path).open("rb") as handle:
        raw = handle.read(LIMIT + 1)
    require(len(raw) <= LIMIT, "Oversized input file")
    return None if allow_null and raw.strip() == b"null" else json_object(raw)


def feature_hint(pr):
    if not isinstance(pr, dict):
        return False
    head = pr.get("head") or {}
    branch = head.get("ref", "") if isinstance(head, dict) else ""
    body = pr.get("body") or ""
    return ((isinstance(branch, str) and "issue-feature" in branch)
            or (isinstance(body, str) and MARKER in body
                and re.search(r'"kind"\s*:\s*"feature', body) is not None))


def pr_context(pr, repository):
    """Recognize feature PRs before validating them; never skip a broken marker."""
    if not feature_hint(pr):
        return None
    require(isinstance(repository, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository), "Invalid configured repository")
    require(isinstance(pr.get("user"), dict) and pr["user"].get("login") == BOT
            and pr["user"].get("type") == "Bot", "Feature PR must be authored by the exact App bot")
    body = pr.get("body")
    require(isinstance(body, str) and len(body.encode()) <= 100_000
            and body.count(MARKER) == 1, "Feature PR needs exactly one bounded marker")
    matches = re.findall(r"<!-- bma-playground:(\{[^\n]*\}) -->", body)
    require(len(matches) == 1, "Malformed feature marker")
    metadata = json_object(matches[0].encode())
    require(type(metadata.get("schemaVersion")) is int and metadata["schemaVersion"] == 2
            and metadata.get("kind") == "feature"
            and metadata.get("featureKind") == "team-workload-v1", "Unsupported feature marker schema")
    issue = metadata.get("issue")
    require(type(issue) is int and 0 < issue <= 2**53 - 1, "Invalid feature issue")
    event = metadata.get("event")
    require(isinstance(event, str) and re.fullmatch(r"issue-feature-[1-9][0-9]{0,15}", event)
            and int(event.rsplit("-", 1)[1]) <= 2**53 - 1, "Invalid authorized feature event")
    require(all(sha(metadata.get(field)) for field in HASH_FIELDS)
            and sha(metadata.get("publicationSha256")), "Invalid feature hashes")
    require(isinstance(metadata.get("session"), str) and re.fullmatch(
        r"sess_[A-Za-z0-9_-]{1,120}", metadata["session"]), "Invalid implementation session")
    require(sha(metadata.get("base"), 40), "Feature base must be a full commit SHA")
    base, head = pr.get("base"), pr.get("head")
    require(isinstance(base, dict) and isinstance(head, dict), "Missing PR revisions")
    require(sha(base.get("sha"), 40) and sha(head.get("sha"), 40)
            and metadata["base"] == base["sha"], "Feature marker base differs from PR base SHA")
    require(base.get("ref") == "main" and head.get("ref") == f"codex/issue-{issue}-{event}",
            "Wrong feature branch or target")
    for side in (base, head):
        require(isinstance(side.get("repo"), dict)
                and side["repo"].get("full_name") == repository, "Feature PR repository mismatch")
    require(metadata.get("repository", repository) == repository, "Marker repository mismatch")
    require(type(pr.get("number")) is int and pr["number"] > 0, "Invalid PR number")
    return {"metadata": metadata, "baseCommit": base["sha"], "headCommit": head["sha"],
            "repository": repository, "pr": pr["number"], "eventId": event}


def ci_context(event, event_name, repository):
    if "pull_request" not in event:
        require(event_name != "pull_request", "PR event is missing pull_request")
        return None
    pr = event["pull_request"]
    require(isinstance(pr, dict), "Malformed pull_request event")
    context = pr_context(pr, repository)
    if context is None:
        return None
    require(event_name == "pull_request", "Feature delivery requires ordinary pull_request CI")
    require(isinstance(event.get("repository"), dict)
            and event["repository"].get("full_name") == repository, "PR event repository mismatch")
    require(pr.get("state") == "open" and not pr.get("merged")
            and pr.get("draft") is False, "Feature CI requires an open, ready PR")
    context["revision"] = context["headCommit"]
    return context


def live_context(deployment, repository, revision):
    if deployment is None:
        return None
    hint = deployment.get("kind") == "feature" or feature_hint(deployment.get("pullRequest"))
    hint = hint or "issue-feature" in str(deployment.get("event", ""))
    if not hint:
        return None
    require(sha(revision, 40), "DEPLOY_SHA must be a full commit SHA")
    pr = deployment.get("pullRequest")
    require(isinstance(pr, dict), "Feature deployment needs its merged bot PR snapshot")
    context = pr_context(pr, repository)
    require(context is not None, "Deployment snapshot is not a feature PR")
    require(all(type(deployment.get(key)) is type(value) and deployment[key] == value
                for key, value in context["metadata"].items()),
            "Deployment metadata differs from the merged PR marker")
    require(deployment.get("repository") == repository and deployment.get("deploySha") == revision
            and type(deployment.get("pr")) is int
            and deployment["pr"] == context["pr"], "Deployment identity mismatch")
    require(pr.get("state") == "closed" and isinstance(pr.get("merged_at"), str)
            and bool(pr["merged_at"]) and pr.get("merge_commit_sha") == revision
            and pr.get("merged", True) is True, "PR was not merged at DEPLOY_SHA")
    context["revision"] = revision
    return context


def fetch_json(origin, path, *, limit=LIMIT):
    """Only an internally constructed path on a configured origin, with no auth."""
    origin = https_origin(origin)
    require(isinstance(path, str) and re.fullmatch(r"/[A-Za-z0-9_./-]+", path)
            and not any(part in {".", "..", ""} for part in path[1:].split("/")),
            "Invalid evidence path")
    started = time.monotonic()
    # No .netrc, proxy credentials, inherited cookies, redirects, or URL supplied
    # by an artifact. A new session is used for each bounded GET.
    with requests.Session() as session:
        session.trust_env = False
        with session.get(origin + path, allow_redirects=False, stream=True, timeout=(10, 15),
                         headers={"Accept": "application/json", "Accept-Encoding": "identity",
                                  "Cache-Control": "no-cache"}) as response:
            require(response.status_code == 200, "Evidence GET must return 200 without redirects")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            require(content_type == "application/json", "Evidence response is not JSON")
            declared = response.headers.get("Content-Length")
            require(declared is None or (declared.isdigit() and int(declared) <= limit),
                    "Oversized or malformed response length")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=16_384):
                data.extend(chunk)
                require(len(data) <= limit, "Evidence response exceeded its byte bound")
                require(time.monotonic() - started <= 45, "Evidence response exceeded its time bound")
            return json_object(bytes(data))


def validate_bundle(context, report, bundle):
    metadata, event = context["metadata"], context["eventId"]
    require(set(bundle) == {"manifest", "spec", "contract"}, "Wrong acceptance bundle fields")
    manifest, spec, contract = (bundle[key] for key in ("manifest", "spec", "contract"))
    require(isinstance(manifest, dict) and isinstance(spec, str) and isinstance(contract, str)
            and 1 <= len(contract.encode()) <= 80_000, "Malformed frozen acceptance bundle")
    require(type(manifest.get("schemaVersion")) is int and manifest["schemaVersion"] == 1
            and manifest.get("status") == "authored-not-executed"
            and manifest.get("eventId") == event
            and manifest.get("baseCommit") == context["baseCommit"], "Acceptance manifest identity mismatch")
    require(digest(spec.encode()) == metadata["acceptanceSha256"]
            and digest(contract.encode()) == metadata["contractSha256"], "Frozen acceptance bytes changed")
    for field in HASH_FIELDS[1:]:
        require(manifest.get(field) == metadata[field], f"Acceptance {field} mismatch")
    # Current markers bind spec/contract/plan/PRD directly. Also enforce a
    # canonical manifest digest whenever a producer supplies that stronger bind.
    manifest_sha = digest(canonical_json(manifest))
    for identity in (metadata, report):
        if "acceptanceManifestSha256" in identity:
            require(identity["acceptanceManifestSha256"] == manifest_sha, "Acceptance manifest hash mismatch")
    cases = manifest.get("cases")
    require(isinstance(cases, list) and all(isinstance(case, dict) for case in cases),
            "Malformed manifest cases")
    validate_acceptance({"schemaVersion": manifest["schemaVersion"], "spec": spec, "cases": cases})
    for case in cases:
        tags = set(re.findall(r"\[(AC-[1-9][0-9]*)\]", case["title"]))
        require(tags == set(case["acceptanceIds"]), "Manifest title tags differ from acceptance IDs")
    require(type(report.get("schemaVersion")) is int and report["schemaVersion"] == 1
            and report.get("taskKind") == "feature" and report.get("purpose") == "feature"
            and report.get("passed") is True and report.get("checksPassed") is True
            and report.get("reviewVerdict") == "approve", "Browser evidence did not pass checks and review")
    require(report.get("baseCommit") == context["baseCommit"] and report.get("eventId") == event
            and report.get("repository", context["repository"]) == context["repository"],
            "Browser evidence base, event, or repository mismatch")
    for field in HASH_FIELDS:
        require(report.get(field) == metadata[field], f"Browser report {field} mismatch")
    require(sha(report.get("candidateSha256")) and sha(report.get("planningProvenanceSha256")),
            "Browser report lacks candidate/planning identities")
    approved = report.get("approvedCandidate")
    require(isinstance(approved, dict), "Missing approved candidate stages")
    sessions = [approved.get("implementationSessionId"), approved.get("reviewSessionId"),
                manifest.get("sessionId"), report.get("bmaSessionId")]
    turns = [approved.get("implementationTurnId"), approved.get("reviewTurnId"),
             manifest.get("turnId"), report.get("verificationTurnId")]
    for values, prefix in ((sessions, "sess_"), (turns, "turn_")):
        require(all(isinstance(value, str) and re.fullmatch(prefix + r"[A-Za-z0-9_-]{1,120}", value)
                    for value in values) and len(set(values)) == len(values),
                "Acceptance, implementation, review, and browser require distinct valid stages")
    require(sessions[0] == metadata["session"], "Marker implementation session mismatch")
    preview = report.get("preview")
    require(isinstance(preview, dict)
            and preview.get("eventId") == event + "-browser-" + metadata["patchSha256"][:12]
            and preview.get("parentEventId") == event
            and preview.get("baseCommit") == context["baseCommit"]
            and preview.get("taskKind") == "feature" and preview.get("phase") == "after"
            and all(preview.get(field) == metadata[field] for field in HASH_FIELDS),
            "Browser preview belongs to another feature")
    return [case["title"] for case in cases]


def exact_patch(source, context, directory):
    """Use committed bytes only; never fetch, checkout, or mutate the source."""
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="feature-delivery-git-") as temporary:
        env = isolated_env(Path(temporary))

        def run(name, argv, *, limit=PATCH_LIMIT, stdin=None):
            command = _run(name, argv, source, env, directory, timeout=120, stdin=stdin)
            require(command["status"] == "passed", f"Exact-revision git check failed: {name}")
            with Path(command["stdout"]).open("rb") as handle:
                raw = handle.read(limit + 1)
            require(len(raw) <= limit, f"Oversized git result: {name}")
            return raw

        git = ["git", "-C", str(source)]
        local = run("local-head", [*git, "rev-parse", "--verify", "HEAD"], limit=100).decode().strip()
        require(local == context["revision"], "Local HEAD is not the event's exact revision")
        base, revision = context["baseCommit"], context["revision"]
        run("base-ancestor", [*git, "merge-base", "--is-ancestor", base, revision], limit=100)
        raw = run("committed-diff", [*git, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                  "--src-prefix=a/", "--dst-prefix=b/", base, revision, "--"])
        helper = (
            "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from patch_boundary import canonical_patch; "
            "sys.stdout.buffer.write(canonical_patch(Path(sys.argv[2]),sys.argv[3],sys.stdin.buffer.read()))"
        )
        canonical = run("src-only-canonical", [sys.executable, "-I", "-B", "-c", helper,
                        str(Path(__file__).resolve().parent), str(source), base], stdin=raw)
        require(bool(canonical) and canonical == raw, "Commit diff is not a canonical src-only patch")
        require(digest(canonical) == context["metadata"]["patchSha256"], "Exact commit patch hash mismatch")
        return canonical


def validate_check_result(checks, context, expected_titles, mode):
    require(checks.get("passed") is True and checks.get("status") == "passed"
            and checks.get("errors") == [] and checks.get("infrastructureFailure") is False,
            "Independent feature execution failed")
    for field, expected in (("baseCommit", context["baseCommit"]),
                            ("patchSha256", context["metadata"]["patchSha256"]),
                            ("acceptanceSha256", context["metadata"]["acceptanceSha256"]),
                            ("actualAcceptanceSha256", context["metadata"]["acceptanceSha256"])):
        require(checks.get(field) == expected, f"Executed checks have a different {field}")
    commands = checks.get("commands")
    expected = CI_COMMANDS if mode == "ci" else LIVE_COMMANDS
    require(isinstance(commands, list) and all(isinstance(row, dict) for row in commands)
            and len(commands) == len(expected) and {row.get("name") for row in commands} == expected
            and all(row.get("status") == "passed" and type(row.get("exitCode")) is int
                    and row["exitCode"] == 0 and row.get("timedOut") is False for row in commands),
            "Required independent commands did not execute cleanly")
    errors = exact_case_errors(checks, expected_titles)
    require(not errors, "; ".join(errors))


def delivery(*, mode, event_path, deployment_path, source, output, env):
    """Return failure/skipped/passed data and always persist the gate result."""
    output = Path(output).resolve()
    result = {"schemaVersion": 1, "mode": mode, "status": "failed", "passed": False,
              "errors": [], "resultFile": str(output / "result.json")}
    try:
        output.mkdir(parents=True, exist_ok=True)
        require(mode in {"ci", "live"}, "Unknown feature delivery mode")
        repository = env.get("GITHUB_REPOSITORY")
        if mode == "ci":
            context = ci_context(read_json(event_path), env.get("GITHUB_EVENT_NAME"), repository)
        else:
            context = live_context(read_json(deployment_path, allow_null=True),
                                   repository, env.get("DEPLOY_SHA"))
        if context is None:
            result.update(status="skipped", reason="Not an identified BMA feature delivery")
        else:
            result.update({key: context[key] for key in
                           ("eventId", "pr", "repository", "baseCommit", "headCommit", "revision")})
            result.update({field: context["metadata"][field] for field in HASH_FIELDS})
            source = Path(source).resolve(strict=True)
            patch = exact_patch(source, context, output / "revision")
            origin = https_origin(env.get("BMA_PREVIEW_ORIGIN"))
            prefix = f'/evidence/{context["eventId"]}/{context["metadata"]["patchSha256"]}/feature'
            report = fetch_json(origin, prefix + "/report.json")
            bundle = fetch_json(origin, prefix + "/acceptance.json")
            titles = validate_bundle(context, report, bundle)
            result.update(evidencePrefix=origin + prefix + "/", expectedFeatureCases=len(titles),
                          acceptanceManifestSha256=digest(canonical_json(bundle["manifest"])))
            for name, value in (("report.json", report), ("acceptance.json", bundle)):
                (output / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            spec_path = output / "workload.acceptance.spec.js"
            spec_path.write_bytes(bundle["spec"].encode())
            live = https_origin(env.get("APP_URL")) if mode == "live" else None
            if live:
                before = fetch_json(live, "/revision.json", limit=4096)
                result["revisionBefore"] = before
                require(before.get("commit") == context["revision"], "Live revision before acceptance differs")
            checks = check_candidate(source, context["baseCommit"], patch, spec_path,
                                     output / "checks", context["metadata"]["acceptanceSha256"],
                                     expected_titles=titles, live_origin=live)
            result["checks"] = checks
            # Observe revision drift even when an assertion failed.
            if live:
                after = fetch_json(live, "/revision.json", limit=4096)
                result["revisionAfter"] = after
                require(after.get("commit") == context["revision"], "Live revision changed during acceptance")
                require(checks.get("liveOrigin") == live, "Acceptance did not target the deployed origin")
            validate_check_result(checks, context, titles, mode)
            result.update(status="passed", passed=True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError,
            subprocess.SubprocessError, requests.RequestException) as error:
        # Do not print request exception URLs: configured URLs must never carry
        # credentials, and remote error response bodies are not diagnostic input.
        result["errors"].append("Public evidence request failed" if isinstance(error, requests.RequestException)
                                else f"{type(error).__name__}: {error}")
    try:
        pending = output / "result.pending"
        pending.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        pending.replace(output / "result.json")
    except OSError as error:
        result.update(status="failed", passed=False)
        result["errors"].append(f"Could not persist delivery result: {error}")
    return result


def write_summary(result, destination):
    """Escape untrusted failure/test text before writing the Actions summary."""
    import html
    text = f'Feature delivery ({result["mode"]}): {result["status"]}\n\n'
    if "revision" in result:
        text += f'Exact revision: `{result["revision"]}`\n\n'
    checks = result.get("checks", {})
    rows = checks.get("commands", [])
    if rows:
        text += "| Executed check | Result |\n| --- | --- |\n"
        for row in rows:
            text += f'| {row["name"]} | {row["status"]} |\n'
        text += (f'\nFrozen cases: {checks.get("executedFeatureCases", 0)} executed / '
                 f'{result.get("expectedFeatureCases", 0)} expected.\n\n')
    if result.get("errors"):
        text += "<pre>" + html.escape("\n".join(result["errors"])) + "</pre>\n\n"
    text += "Artifact: <code>" + html.escape(result["resultFile"]) + "</code>\n"
    with Path(destination).open("a", encoding="utf-8") as handle:
        handle.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("ci", "live"))
    parser.add_argument("--event", type=Path, default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--deployment", type=Path, default=Path("artifacts/deployment.json"))
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, help="Default: artifacts/feature-delivery-{mode}")
    args = parser.parse_args(argv)
    result = delivery(mode=args.mode, event_path=args.event, deployment_path=args.deployment,
                      source=args.source, output=args.output or Path(f"artifacts/feature-delivery-{args.mode}"),
                      env=os.environ)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        write_summary(result, os.environ["GITHUB_STEP_SUMMARY"])
    print(f'Feature delivery: {result["status"]}')
    print(f'Result artifact: {result["resultFile"]}')
    return 0 if result["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    sys.exit(main())
