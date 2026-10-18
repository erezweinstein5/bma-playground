"""Publish a frozen, independently verified feature through ordinary auto-merge.

Run only from a trusted controller checkout, never PR code. Input files and the
publication checkpoint must be restored from controller-owned storage. Hashes
bind artifacts; they are not signatures or a substitute for artifact ownership.
The workflow must serialize by issue and persist the checkpoint even on failure.

No AWS calls occur here. --validate-only performs no GitHub calls. Publication
uses an isolated clone and a create-only Git ref lease; it never changes the
controller checkout, updates an existing branch, or bypasses required checks.
"""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlencode, urlsplit

import requests

from design_board import canonical_json, validate_plan
from feature_browser import (design_links, digest, json_object, read_file,
                             validate_feature_calls)
from feature_cycle import validate_review
from feature_contract import (GENERIC_KIND, LEGACY_KIND, requirement_ids,
                              validate_acceptance_ids, validate_prd_requirements)
from feature_delivery import validate_plan_binding
from feature_checks import exact_case_errors
from feature_stages import validate_acceptance


HASH_FIELDS = ("patchSha256", "acceptanceSha256", "contractSha256",
               "planSha256", "prdSha256")
REQUIRED_COMMANDS = {"canonical-patch", "clone", "checkout-base", "verify-base",
                     "apply-candidate", "protected-inputs", "npm-ci", "npm-test",
                     "npm-build", "board-browser", "repair-browser", "feature-browser"}
ACS = {f"AC-{n}" for n in range(1, 8)}
BOT = "bma-playground[bot]"
FEATURE_CHECK = "Feature delivery"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path, limit=8 * 1024 * 1024):
    return json_object(read_file(path, limit))


def sha(value, size=64):
    return isinstance(value, str) and re.fullmatch(rf"[a-f0-9]{{{size}}}", value) is not None


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None


def authorized_assignment(assignment, event, event_name, operators, repository):
    """Reproduce feature-assignment.mjs authorization from the original event."""
    allowed = {login.strip().lower() for login in operators.split(",") if login.strip()}
    issue, sender = event.get("issue", {}), event.get("sender", {})
    require(bool(allowed) and event_name == "issues"
            and event.get("action") in {"opened", "labeled"}, "Unauthorized feature trigger")
    require(sender.get("type") == "User" and isinstance(sender.get("login"), str)
            and sender["login"].lower() in allowed, "Unauthorized feature operator")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository or "")
            and event.get("repository", {}).get("full_name") == repository,
            "Wrong trigger repository")
    require("pull_request" not in issue and issue.get("state") == "open"
            and any(row.get("name") == "agent-feature" for row in issue.get("labels", []))
            and (event["action"] != "labeled"
                 or event.get("label", {}).get("name") == "agent-feature"),
            "Feature issue must be open and explicitly labeled")
    require(all(type(issue.get(key)) is int and 0 < issue[key] <= 2**53 - 1
                for key in ("id", "number")), "Invalid issue identity")
    require(isinstance(issue.get("title"), str) and issue["title"].strip()
            and (issue.get("body") is None or isinstance(issue["body"], str)), "Malformed PRD")
    prd = f'# {issue["title"]}\n\n{issue.get("body") or ""}\n'
    require(100 <= len(prd.encode()) <= 32000, "Invalid bounded PRD")
    version, kind = assignment.get("schemaVersion"), assignment.get("kind")
    require(type(version) is int and (version, kind) in
            {(1, LEGACY_KIND), (2, GENERIC_KIND)}, "Unsupported feature assignment schema/kind")
    if version == 1:
        require(all(re.search(rf"\bAC-{n}\b", prd) for n in range(1, 8)),
                "Invalid bounded Team Workload PRD")
    require(sha(assignment.get("baseCommit"), 40), "Assignment must pin a full commit")
    expected = {"schemaVersion": version, "kind": kind, "repository": repository,
                "issueNumber": issue["number"], "eventId": f'issue-feature-{issue["id"]}',
                "actor": sender["login"], "baseCommit": assignment["baseCommit"],
                "title": issue["title"], "prd": prd, "prdSha256": digest(prd.encode()),
                "status": "prepared"}
    # The trusted intake currently emits no branch field. Derive it, never take
    # an arbitrary branch from the model or permit a probe to become an issue run.
    require(type(assignment.get("schemaVersion")) is int and assignment == expected,
            "Assignment differs from its authorized issue snapshot")
    return f'codex/issue-{issue["number"]}-{expected["eventId"]}'


def clean_git_env(directory):
    """No AWS/GitHub/npm credentials, global config, templates, or hooks."""
    directory = Path(directory)
    home, templates = directory / "home", directory / "templates"
    home.mkdir(exist_ok=True)
    templates.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", os.defpath), "HOME": str(home),
        "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TEMPLATE_DIR": str(templates), "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "3", "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": os.devnull, "GIT_CONFIG_KEY_1": "protocol.allow",
        "GIT_CONFIG_VALUE_1": "never", "GIT_CONFIG_KEY_2": "protocol.file.allow",
        "GIT_CONFIG_VALUE_2": "always",
    }


def canonical_without_credentials(source, base, patch):
    helper = (
        "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "from patch_boundary import canonical_patch; "
        "sys.stdout.buffer.write(canonical_patch(Path(sys.argv[2]),sys.argv[3],sys.stdin.buffer.read()))"
    )
    with tempfile.TemporaryDirectory(prefix="feature-publication-check-") as folder:
        return subprocess.check_output(
            [sys.executable, "-I", "-B", "-c", helper, str(Path(__file__).resolve().parent),
             str(Path(source).resolve()), base], input=patch, env=clean_git_env(folder), timeout=120,
            stderr=subprocess.PIPE)


def validate_checks(checks, candidate, acceptance, required_ids=None):
    ids = ACS if required_ids is None else validate_acceptance_ids(required_ids)
    require(type(checks.get("schemaVersion")) is int and checks.get("schemaVersion") == 1
            and checks.get("passed") is True
            and checks.get("status") == "passed" and checks.get("errors") == []
            and checks.get("infrastructureFailure") is False, "Independent checks did not pass")
    for field in ("baseCommit", "patchSha256", "acceptanceSha256"):
        require(checks.get(field) == candidate[field], f"Checks have a different {field}")
    require(checks.get("actualAcceptanceSha256") == candidate["acceptanceSha256"],
            "Checks did not execute the frozen acceptance bytes")
    commands = checks.get("commands", [])
    require(isinstance(commands, list) and all(isinstance(row, dict) for row in commands),
            "Malformed check commands")
    require({row.get("name") for row in commands} == REQUIRED_COMMANDS
            and len(commands) == len(REQUIRED_COMMANDS)
            and all(row.get("status") == "passed" and type(row.get("exitCode")) is int
                    and row["exitCode"] == 0 and row.get("timedOut") is False for row in commands),
            "Missing, duplicated, failed, or unexecuted required command")
    cases, expected = checks.get("featureCases", []), acceptance.get("cases", [])
    require(isinstance(expected, list)
            and (10 <= len(expected) <= 30 if required_ids is None else 1 <= len(expected) <= 60)
            and all(isinstance(row, dict) and isinstance(row.get("title"), str)
                    for row in expected), "Malformed frozen acceptance cases")
    titles = {row["title"] for row in expected}
    require(len(titles) == len(expected) and isinstance(cases, list)
            and len(cases) == len(expected)
            and all(isinstance(row, dict) for row in cases)
            and {row.get("title") for row in cases} == titles,
            "Executed feature cases differ from the frozen bundle")
    require(all(row.get("project") == "desktop" and row.get("passed") is True
                and row.get("executed") is True and row.get("errors") == []
                and row.get("status") == "expected" and row.get("expectedStatus") == "passed"
                and row.get("outcomes") == ["passed"] for row in cases),
            "Feature cases must execute once and pass without skips or retries")
    actual_acs = set()
    for row in cases:
        tags = set(re.findall(r"\[(AC-[1-9][0-9]*)\]", row["title"]))
        require(isinstance(row.get("acceptanceIds"), list)
                and validate_acceptance_ids(row["acceptanceIds"]) == tags and tags <= ids,
                "Feature case acceptance tags differ")
        actual_acs.update(tags)
    require(actual_acs == ids and validate_acceptance_ids(checks.get("coveredAcceptanceIds")) == ids
            and type(checks.get("executedFeatureCases")) is int
            and checks["executedFeatureCases"] == len(titles)
            and type(checks.get("executedProjectCases")) is int
            and checks.get("executedProjectCases") == len(titles),
            "Feature acceptance coverage is incomplete")
    errors = exact_case_errors(checks, [row["title"] for row in expected], required_ids=required_ids)
    require(not errors, "; ".join(errors))


def validate_inputs(*, assignment_path, event_path, candidate_path, planning,
                    acceptance, browser_path, source, repository, operators,
                    event_name, origin):
    assignment, event = load(assignment_path), load(event_path)
    branch = authorized_assignment(assignment, event, event_name, operators, repository)
    candidate_path, planning, acceptance, browser_path = map(
        Path, (candidate_path, planning, acceptance, browser_path))
    candidate = load(candidate_path)
    patch = read_file(candidate_path.with_name("verified.patch"), 5 * 1024 * 1024)
    plan, provenance = validate_plan(load(planning / "plan.json")), load(planning / "provenance.json")
    manifest = load(acceptance / "manifest.json")
    browser, report = load(browser_path), load(browser_path.with_name("report.json"))
    items = load(browser_path.with_name("items.json")).get("items")
    generic = assignment["kind"] == GENERIC_KIND
    require(type(plan.get("schemaVersion")) is int
            and plan["schemaVersion"] == assignment["schemaVersion"],
            "Plan schema differs from the authorized assignment")
    required_ids = requirement_ids(plan) if generic else None
    if generic:
        validate_prd_requirements(plan, assignment["prd"])
    require(sha(candidate.get("baseCommit"), 40) and type(candidate.get("schemaVersion")) is int
            and candidate.get("schemaVersion") == 1
            and candidate.get("passed") is True and type(candidate.get("attempt")) is int
            and 0 <= candidate["attempt"] <= 4 and sha(candidate.get("tree"), 40),
            "Missing approved bounded feature candidate")
    expected_identity = {"repository": repository, "branch": branch,
                         "issueNumber": assignment["issueNumber"],
                         "eventId": assignment["eventId"], "baseCommit": assignment["baseCommit"]}
    for value in (candidate, provenance, manifest, browser, report):
        require(value.get("featureKind", assignment["kind"]) == assignment["kind"],
                "Artifact has a different feature kind")
        for field, expected in expected_identity.items():
            if field in value:
                require(value[field] == expected, f"Artifact has a different {field}")
    require(candidate["baseCommit"] == assignment["baseCommit"]
            and provenance.get("eventId") == assignment["eventId"]
            and provenance.get("baseCommit") == candidate["baseCommit"]
            and provenance.get("status") == "planned" and not plan["questions"],
            "Planning must be complete for this authorized event and base")
    require(type(manifest.get("schemaVersion")) is int
            and manifest["schemaVersion"] == assignment["schemaVersion"]
            and manifest.get("eventId") == assignment["eventId"]
            and manifest.get("baseCommit") == candidate["baseCommit"]
            and manifest.get("status") == "authored-not-executed",
            "Acceptance bundle must be the frozen authoring output for this event")
    actual = {"patchSha256": digest(patch),
              "planSha256": digest(canonical_json(plan)),
              "prdSha256": digest(read_file(planning / "prd.md", 32000)),
              "acceptanceSha256": digest(read_file(acceptance / "workload.acceptance.spec.js", 512 * 1024)),
              "contractSha256": digest(read_file(acceptance / "ui-contract.md", 512 * 1024))}
    for field, value in actual.items():
        require(sha(candidate.get(field)) and candidate[field] == value,
                f"Candidate has a different frozen {field}")
        if field != "patchSha256":
            require(manifest.get(field) == value, f"Acceptance manifest has a different {field}")
    require(all(provenance.get(key) == actual[key] for key in ("planSha256", "prdSha256"))
            and actual["prdSha256"] == assignment["prdSha256"], "PRD or plan identity changed")
    journey = None
    if generic:
        _, journey = validate_plan_binding(plan, manifest, (browser, report))
        require(validate_acceptance_ids(candidate.get("checks", {}).get("coveredAcceptanceIds")) == required_ids
                and candidate.get("browserJourneySha256") == manifest["browserJourneySha256"],
                "Candidate criteria/journey differ from the pinned plan")
        if "acceptanceIds" in candidate:
            require(validate_acceptance_ids(candidate["acceptanceIds"]) == required_ids,
                    "Candidate acceptance IDs differ from the pinned plan")
        if "browserJourney" in candidate:
            require(candidate["browserJourney"] == journey, "Candidate browser journey differs from the plan")
        if "acceptanceManifestSha256" in candidate:
            require(candidate["acceptanceManifestSha256"] == digest(canonical_json(manifest)),
                    "Candidate acceptance manifest hash mismatch")
        validate_acceptance({"schemaVersion": 2, "acceptanceIds": manifest["acceptanceIds"],
                             "cases": manifest.get("cases"),
                             "spec": read_file(acceptance / "workload.acceptance.spec.js",
                                               512 * 1024).decode()},
                            required_ids=required_ids)
    else:
        for value in (manifest, candidate, browser, report):
            require(not any(key in value for key in
                            ("acceptanceIds", "browserJourney", "browserJourneySha256")),
                    "Legacy evidence cannot downgrade a generic feature")
    export = candidate.get("exportManifest", {})
    require(export.get("eventId") == assignment["eventId"] + "-implementation"
            and export.get("baseCommit") == candidate["baseCommit"]
            and export.get("patchSha256") == actual["patchSha256"]
            and export.get("patchKey") == f'repairs/{assignment["eventId"]}-implementation/repair.patch',
            "Candidate export belongs to a different event or patch")
    validate_checks(candidate.get("checks", {}), candidate, manifest, required_ids=required_ids)
    review = validate_review(candidate.get("review", {}), actual["patchSha256"],
                             actual["acceptanceSha256"], required_ids=required_ids)
    require(review["verdict"] == "approve", "Fresh review did not approve this candidate")
    stages = {
        "planning": {"session": provenance.get("sessionId"), "turn": provenance.get("turnId")},
        "acceptance": {"session": manifest.get("sessionId"), "turn": manifest.get("turnId")},
        "implementation": {"session": candidate.get("implementationSessionId"),
                           "turn": candidate.get("implementationTurnId")},
        "review": {"session": candidate.get("reviewSessionId"), "turn": candidate.get("reviewTurnId")},
        "browser": {"session": browser.get("sessionId"), "turn": browser.get("verificationTurnId")},
    }
    require(all(identifier(value) for stage in stages.values() for value in stage.values())
            and len({stage["session"] for stage in stages.values()}) == len(stages)
            and len({stage["turn"] for stage in stages.values()}) == len(stages),
            "Planning, acceptance, implementation, review, and browser require distinct sessions")
    candidate_sha = digest(canonical_json(candidate))
    for value in (browser, report):
        require(type(value.get("schemaVersion")) is int
                and value.get("schemaVersion") == (2 if generic else 1)
                and value.get("taskKind") == "feature"
                and value.get("passed") is True and value.get("eventId") == assignment["eventId"]
                and value.get("baseCommit") == candidate["baseCommit"]
                and value.get("candidateSha256") == candidate_sha
                and all(value.get(field) == actual[field] for field in HASH_FIELDS),
                "Browser evidence does not bind the approved candidate")
    require(report.get("purpose") == "feature" and report.get("checksPassed") is True
            and report.get("reviewVerdict") == "approve"
            and report.get("planningProvenanceSha256") == digest(canonical_json(provenance))
            and report.get("bmaSessionId") == browser["sessionId"]
            and report.get("verificationTurnId") == browser["verificationTurnId"]
            and report.get("approvedCandidate") == {key: candidate[key] for key in
                ("implementationSessionId", "implementationTurnId", "reviewSessionId", "reviewTurnId")},
            "Browser report has stale stage identities")
    origin = origin.rstrip("/")
    parsed = urlsplit(origin)
    require(parsed.scheme == "https" and parsed.netloc and not any(
        (parsed.path, parsed.query, parsed.fragment, parsed.username, parsed.password)),
        "Evidence must use a configured HTTPS origin")
    design = design_links(planning, plan, provenance, origin)
    evidence_url = f'{origin}/evidence/{assignment["eventId"]}/{actual["patchSha256"]}/feature'
    evidence = browser.get("browserEvidence", {})
    require(browser.get("design") == design and report.get("design") == design
            and evidence.get("url") == evidence_url
            and evidence.get("watchUrl") == evidence_url + "/index.html"
            and evidence.get("reportUrl") == evidence_url + "/report.json",
            "Evidence/design links do not match their content-addressed identities")
    descriptor = report.get("preview", {})
    browser_event = assignment["eventId"] + "-browser-" + actual["patchSha256"][:12]
    require(descriptor.get("eventId") == browser_event
            and descriptor.get("parentEventId") == assignment["eventId"]
            and descriptor.get("baseCommit") == candidate["baseCommit"]
            and descriptor.get("taskKind") == "feature" and descriptor.get("phase") == "after"
            and descriptor.get("url") == f"{origin}/previews/{browser_event}/after/index.html"
            and all(descriptor.get(field) == actual[field] for field in HASH_FIELDS),
            "Browser preview does not identify this feature")
    phases = report.get("phases", {})
    require(set(phases) == {"after"} and isinstance(items, list), "Missing feature recording transcript")
    observations = validate_feature_calls(items, browser["verificationTurnId"], descriptor,
                                          phases["after"], journey=journey)
    require(report.get("verifiedObservations") == observations
            and evidence.get("browserSessions") == {"after": phases["after"]["browserSessionId"]},
            "Browser observations/session do not match the transcript")
    finals = [row for row in items if row.get("type") == "message"
              and row.get("role") == "assistant" and row.get("phase") == "final_answer"]
    require({row.get("turn_id") for row in items if row.get("turn_id")}
            == {browser["verificationTurnId"]} and len(finals) == 1
            and finals[0].get("status") == "completed"
            and not any(row.get("status") in {"failed", "incomplete", "cancelled", "queued",
                                               "in_progress"} for row in items),
            "Browser transcript has no unambiguous completed verification turn")
    for filename in ("after.mp4", "after.png"):
        artifact = report.get("artifacts", {}).get(filename, {})
        require(artifact.get("url") == evidence_url + "/" + filename
                and sha(artifact.get("sha256")), "Malformed published media identity")
    require(canonical_without_credentials(source, candidate["baseCommit"], patch) == patch,
            "Feature patch must be canonical and src-only")
    identity = {**expected_identity, **actual, "kind": "feature", "featureKind": assignment["kind"],
                "candidateSha256": candidate_sha,
                "assignmentSha256": digest(canonical_json(assignment)),
                "triggerSha256": digest(canonical_json(event)),
                "planningProvenanceSha256": digest(canonical_json(provenance)),
                "acceptanceManifestSha256": digest(canonical_json(manifest)),
                "browserResultSha256": digest(canonical_json(browser)),
                "browserReportSha256": digest(canonical_json(report)),
                "browserItemsSha256": digest(canonical_json({"items": items}))}
    if generic:
        identity.update(acceptanceIds=sorted(required_ids),
                        browserJourneySha256=manifest["browserJourneySha256"])
    metadata = {"schemaVersion": 2, "kind": "feature", "featureKind": assignment["kind"],
                "issue": assignment["issueNumber"], "session": stages["implementation"]["session"],
                "event": assignment["eventId"], "base": candidate["baseCommit"], **actual,
                "publicationSha256": digest(canonical_json(identity))}
    if generic:
        metadata.update({key: identity[key] for key in
                         ("acceptanceIds", "browserJourneySha256", "acceptanceManifestSha256",
                          "browserReportSha256")})
    return {"identity": identity, "metadata": metadata, "stages": stages, "patch": patch,
            "tree": candidate["tree"], "design": design, "evidence": evidence,
            "issue": event["issue"], "caseCount": candidate["checks"]["executedFeatureCases"]}


def marker(context):
    return "<!-- bma-playground:" + canonical_json(context["metadata"]).decode() + " -->"


def title(context):
    label = ("PRD feature" if context["identity"]["featureKind"] == GENERIC_KIND
             else "Team Workload")
    return f'Implement {label} for issue #{context["identity"]["issueNumber"]}'


def body(context, head):
    identity = context["identity"]
    stages = "\n".join(f'- {name.capitalize()}: session `{row["session"]}`, turn `{row["turn"]}`.'
                       for name, row in context["stages"].items())
    hashes = "\n".join(f"- {name}: `{identity[name]}`." for name in HASH_FIELDS)
    keyboard = ("" if identity["featureKind"] == GENERIC_KIND else
                " Keyboard coverage comes from acceptance checks, not the recording.")
    return (
        f'Refs #{identity["issueNumber"]}.\n\n'
        "BMA planned and implemented this feature from the authorized PRD. "
        "The candidate changes regular files under `src/` only. The controller owns "
        "the frozen acceptance bundle, design proposal, and evidence separately.\n\n"
        f'{context["caseCount"]} frozen feature cases, existing board/repair checks, unit tests, '
        "and build passed for this patch. A fresh BMA review approved it. A separate BMA "
        f"session verified the recorded browser interactions.{keyboard}\n\n"
        f'[Visual design proposal]({context["design"]["url"]}) · '
        f'[Watch browser verification (MP4)]({context["evidence"]["watchUrl"]})\n\n'
        f'[Verification report]({context["evidence"]["reportUrl"]})\n\n'
        "GitHub must pass its required checks on this PR revision before ordinary "
        "squash auto-merge. Merge, deployment, and live verification have not been "
        "claimed by these pre-publication results. Deployment closes the issue only "
        "after the feature-specific live smoke check succeeds.\n\n"
        f'Event: `{identity["eventId"]}`. Tested base: `{identity["baseCommit"]}`.\n'
        f'PR revision: `{head}`. Candidate tree: `{context["tree"]}`.\n\n'
        + stages + "\n\n" + hashes + "\n\n" + marker(context)
    )


class GitHub:
    """One HTTP attempt per call; no mutation retries or credential redirects."""
    def __init__(self, token):
        self.token = token

    def request(self, method, path, payload=None, *, missing=False):
        with requests.request(method, "https://api.github.com" + path, json=payload,
                              headers={"Authorization": "Bearer " + self.token,
                                       "Accept": "application/vnd.github+json",
                                       "X-GitHub-Api-Version": "2022-11-28"},
                              timeout=30, allow_redirects=False) as response:
            if missing and method == "GET" and response.status_code == 404:
                return None
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f"GitHub {method} failed with HTTP {response.status_code}; "
                                   "inspect checkpoint before retrying")
            return response.json() if response.content else None


class PreparedCommit:
    """Deterministic single-parent commit; no shared branch/index is modified."""
    def __init__(self, source, context):
        self.context, self.source = context, Path(source).resolve()

    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="feature-publication-")
        self.root = Path(self.temporary.name)
        self.env = clean_git_env(self.root)
        self.repo = self.root / "repo"
        try:
            self.git("clone", "--quiet", "--no-hardlinks", "--no-checkout",
                     str(self.source), str(self.repo), cwd=self.root)
            self.git("checkout", "--quiet", "--detach", self.context["identity"]["baseCommit"])
            self.git("apply", "--index", "-", data=self.context["patch"])
            self.tree = self.git("write-tree").decode().strip()
            require(self.tree == self.context["tree"], "Publication tree differs from reviewed candidate")
            stamp = int(self.git("show", "-s", "--format=%ct",
                                self.context["identity"]["baseCommit"]).decode().strip()) + 1
            self.env.update({"GIT_AUTHOR_NAME": BOT, "GIT_COMMITTER_NAME": BOT,
                             "GIT_AUTHOR_EMAIL": f"{BOT}@users.noreply.github.com",
                             "GIT_COMMITTER_EMAIL": f"{BOT}@users.noreply.github.com",
                             "GIT_AUTHOR_DATE": f"{stamp} +0000", "GIT_COMMITTER_DATE": f"{stamp} +0000"})
            message = title(self.context) + "\n\n" + marker(self.context) + "\n"
            self.head = self.git("commit-tree", self.tree, "-p", self.context["identity"]["baseCommit"],
                                 data=message.encode()).decode().strip()
            require(sha(self.head, 40), "Invalid prepared commit")
            return self
        except BaseException:
            self.temporary.cleanup()
            raise

    def git(self, *args, cwd=None, data=None, env=None):
        return subprocess.check_output(["git", "-C", str(cwd or self.repo), *args],
                                       input=data, env=env or self.env, timeout=120)

    def push_new(self, token):
        ref = "refs/heads/" + self.context["identity"]["branch"]
        auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env = {**self.env, "GIT_CONFIG_COUNT": "6",
               "GIT_CONFIG_KEY_3": "http.https://github.com/.extraheader",
               "GIT_CONFIG_VALUE_3": "AUTHORIZATION: basic " + auth,
               "GIT_CONFIG_KEY_4": "protocol.https.allow", "GIT_CONFIG_VALUE_4": "always",
               "GIT_CONFIG_KEY_5": "http.followRedirects", "GIT_CONFIG_VALUE_5": "false"}
        # Empty expected value is a compare-and-create lease. Even a branch at
        # the base commit cannot be advanced by this call. Never force-update it.
        self.git("push", "--porcelain", f"--force-with-lease={ref}:",
                 f'https://github.com/{self.context["identity"]["repository"]}.git',
                 f"{self.head}:{ref}", env=env)

    def __exit__(self, *args):
        self.temporary.cleanup()


@contextmanager
def checkpoint_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another local publication owns this checkpoint") from error
        yield


def save_checkpoint(path, value):
    path = Path(path)
    require(not path.is_symlink(), "Publication checkpoint cannot be a symlink")
    fd, pending = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def current_policy(github, context, feature_check):
    repo, base = context["identity"]["repository"], context["identity"]["baseCommit"]
    require(github.request("GET", f"/repos/{repo}/git/ref/heads/main")["object"]["sha"] == base,
            "Main changed; start a new feature assignment from current main")
    # Branch metadata exposes required contexts with Contents read access.
    # The administration-only protection endpoint would needlessly expand the
    # publishing App's permissions. Strict freshness is configured on main;
    # this adapter also pins main and the independently tested candidate base.
    policy = github.request("GET", f"/repos/{repo}/branches/main")
    protection = policy.get("protection") or {}
    checks = protection.get("required_status_checks") or {}
    names = set(checks.get("contexts", [])) | {row["context"] for row in checks.get("checks", [])}
    require(policy.get("protected") is True and protection.get("enabled") is True
            and feature_check in names,
            f"Main must be protected with required status check {feature_check!r}")
    issue = github.request("GET", f'/repos/{repo}/issues/{context["identity"]["issueNumber"]}')
    frozen = context["issue"]
    require(all(issue.get(key) == frozen.get(key) for key in ("id", "number", "title"))
            and (issue.get("body") or "") == (frozen.get("body") or "")
            and issue.get("state") == "open" and not issue.get("pull_request")
            and any(row.get("name") == "agent-feature" for row in issue.get("labels", [])),
            "Issue was edited, closed, relabeled, or changed identity after assignment")


def validate_pr(pr, context, head, bot):
    identity = context["identity"]
    require(type(pr.get("number")) is int and pr["number"] > 0 and identifier(pr.get("node_id"))
            and pr.get("html_url") == f'https://github.com/{identity["repository"]}/pull/{pr["number"]}',
            "Malformed publication PR identity")
    require(pr.get("user", {}).get("login") == bot
            and pr.get("head", {}).get("ref") == identity["branch"]
            and pr.get("head", {}).get("sha") == head
            and pr.get("head", {}).get("repo", {}).get("full_name") == identity["repository"]
            and pr.get("base", {}).get("ref") == "main"
            and pr.get("base", {}).get("repo", {}).get("full_name") == identity["repository"]
            and pr.get("title") == title(context) and pr.get("body") == body(context, head)
            and pr.get("draft") is False, "Existing PR differs from the exact feature publication")
    require(pr.get("state") in {"open", "closed"}
            and (pr["state"] == "open" or pr.get("merged_at")), "Feature PR was closed without merge")
    return pr


def publish(context, source, result_path, github, *, feature_check=FEATURE_CHECK,
            bot=BOT, prepare=PreparedCommit):
    """Reconcile before mutating; uncertain absent resources stop, never resend."""
    require(isinstance(feature_check, str) and feature_check.strip(), "A required feature check is mandatory")
    path = Path(result_path)
    with checkpoint_lock(path), prepare(source, context) as prepared:
        state = load(path) if path.exists() else None
        binding = {"schemaVersion": 1, "identity": context["identity"],
                   "headCommit": prepared.head, "tree": prepared.tree,
                   "featureCheck": feature_check, "botLogin": bot}
        if state is not None:
            require(all(state.get(key) == value for key, value in binding.items()),
                    "Publication checkpoint binds different inputs or commit")
        repo, branch = context["identity"]["repository"], context["identity"]["branch"]
        query = urlencode({"state": "all", "head": repo.split("/")[0] + ":" + branch,
                           "base": "main", "per_page": 100})
        pulls = github.request("GET", f"/repos/{repo}/pulls?{query}")
        require(isinstance(pulls, list) and len(pulls) <= 1, "Ambiguous existing feature PRs")
        pr = None
        if pulls:
            require(type(pulls[0].get("number")) is int, "Malformed PR discovery")
            pr = validate_pr(github.request("GET", f'/repos/{repo}/pulls/{pulls[0]["number"]}'),
                             context, prepared.head, bot)
        ref_path = f"/repos/{repo}/git/ref/heads/{branch}"
        remote = github.request("GET", ref_path, missing=True)
        if remote is not None:
            require(remote.get("object", {}).get("sha") == prepared.head,
                    "Known feature branch points at a different commit; no update permitted")
        if state and state.get("pullRequest"):
            require(pr and state["pullRequest"] == pr["html_url"]
                    and state.get("pullRequestNumber") == pr["number"],
                    "Recorded PR is missing or changed; no replacement publication")
        # A successful prior publication may already have merged and lost its
        # branch. Reconcile it read-only even though main has necessarily moved.
        if pr and pr.get("merged_at"):
            value = {**binding, "status": "merged", "pullRequest": pr["html_url"],
                     "pullRequestNumber": pr["number"], "mergeCommit": pr.get("merge_commit_sha")}
            save_checkpoint(path, value)
            return value
        current_policy(github, context, feature_check)
        if pr is not None:
            require(remote is not None, "Open feature PR lost its expected branch")
        if state is None:
            require(remote is None or pr is not None,
                    "Known branch has no PR/checkpoint; creation history is ambiguous")
            # With a lost checkpoint, an existing PR proves publication but
            # cannot prove whether an auto-merge request is still in flight.
            # Reconcile an enabled/merged result; never blindly enable again.
            state = {**binding, "status": "recovered-pr" if pr else "prepared"}
            save_checkpoint(path, state)

        def record(status, **fields):
            state.update({"status": status, **fields})
            save_checkpoint(path, state)

        if remote is None:
            require(state["status"] == "prepared",
                    "Prior branch mutation is uncertain or branch disappeared; no repeat push")
            current_policy(github, context, feature_check)
            record("push-submitting")
            prepared.push_new(github.token)
            remote = github.request("GET", ref_path, missing=True)
            require(remote and remote.get("object", {}).get("sha") == prepared.head,
                    "Pushed branch is not confirmed; reconcile before proceeding")
            record("branch-ready")
        elif state["status"] == "push-submitting":
            record("branch-ready")  # The exact deterministic commit proves this push.
        if pr is None:
            require(state["status"] == "branch-ready",
                    "Prior PR mutation is uncertain or PR disappeared; no duplicate creation")
            current_policy(github, context, feature_check)
            record("pr-submitting")
            response = github.request("POST", f"/repos/{repo}/pulls",
                                      {"title": title(context), "head": branch, "base": "main",
                                       "body": body(context, prepared.head), "draft": False})
            require(type(response.get("number")) is int, "PR creation response is ambiguous")
            pr = validate_pr(github.request("GET", f'/repos/{repo}/pulls/{response["number"]}'),
                             context, prepared.head, bot)
            record("pr-created", pullRequest=pr["html_url"], pullRequestNumber=pr["number"])
        elif state["status"] in {"prepared", "push-submitting", "branch-ready", "pr-submitting"}:
            record("pr-created", pullRequest=pr["html_url"], pullRequestNumber=pr["number"])
        # Re-read exact PR revision immediately before requesting auto-merge.
        pr = validate_pr(github.request("GET", f'/repos/{repo}/pulls/{pr["number"]}'),
                         context, prepared.head, bot)
        if pr.get("merged_at"):
            record("merged", pullRequest=pr["html_url"], pullRequestNumber=pr["number"],
                   mergeCommit=pr.get("merge_commit_sha"))
            return state
        require("auto_merge" in pr, "GitHub did not expose auto-merge state")
        if pr["auto_merge"] is not None:
            require(pr["auto_merge"].get("merge_method") == "squash",
                    "Existing auto-merge uses a different merge method")
            record("auto-merge-enabled", pullRequest=pr["html_url"], pullRequestNumber=pr["number"])
            return state
        require(state["status"] == "pr-created",
                "Auto-merge is absent after an earlier request; inspect instead of resubmitting")
        current_policy(github, context, feature_check)
        record("auto-merge-submitting", pullRequest=pr["html_url"], pullRequestNumber=pr["number"])
        response = github.request("POST", "/graphql", {
            "query": "mutation($id:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$id,"
                     "mergeMethod:SQUASH}){pullRequest{number autoMergeRequest{enabledAt}}}}",
            "variables": {"id": pr["node_id"]}})
        require(not response.get("errors"), "Auto-merge was not confirmed; PR is retained for inspection")
        confirmed = validate_pr(github.request("GET", f'/repos/{repo}/pulls/{pr["number"]}'),
                                context, prepared.head, bot)
        if confirmed.get("merged_at"):
            record("merged", mergeCommit=confirmed.get("merge_commit_sha"))
        else:
            require(confirmed.get("auto_merge") is not None
                    and confirmed["auto_merge"].get("merge_method") == "squash",
                    "Auto-merge response is uncertain; reconcile without another request")
            record("auto-merge-enabled")
        return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("assignment", "event", "candidate", "planning", "acceptance", "browser", "result"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--feature-check", default=FEATURE_CHECK)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    context = validate_inputs(
        assignment_path=args.assignment, event_path=args.event, candidate_path=args.candidate,
        planning=args.planning, acceptance=args.acceptance, browser_path=args.browser, source=Path.cwd(),
        repository=os.environ["GITHUB_REPOSITORY"], operators=os.environ["DEMO_OPERATORS"],
        event_name=os.environ["GITHUB_EVENT_NAME"], origin=os.environ["BMA_PREVIEW_ORIGIN"])
    if args.validate_only:
        print(json.dumps({"status": "validated-not-published", "identity": context["identity"],
                          "branch": context["identity"]["branch"]}, indent=2))
        return 0
    result = publish(context, Path.cwd(), args.result, GitHub(os.environ["GH_TOKEN"]),
                     feature_check=args.feature_check, bot=os.environ.get("BMA_GITHUB_BOT_LOGIN", BOT))
    print(json.dumps(result, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(f'Feature [PR #{result["pullRequestNumber"]}]({result["pullRequest"]}): '
                         f'**{result["status"]}**. Deployment remains a separate verified stage.\n')
    return 0


if __name__ == "__main__":
    sys.exit(main())
