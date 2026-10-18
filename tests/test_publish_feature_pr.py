"""Offline publication-boundary and lost-response tests; no cloud writes."""
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import publish_feature_pr as publisher
import feature_browser as browser


BASE, HEAD, TREE = "a" * 40, "b" * 40, "c" * 40
REPO, EVENT, ORIGIN = "demo/playground", "issue-feature-123", "https://example.invalid"
PATCH = b"candidate patch"


def fixture(root):
    plan = {
        "schemaVersion": 1, "title": "Workload", "problem": "Uneven work", "approach": "Balance effort",
        "assumptions": [], "questions": [], "nonGoals": [],
        "requirements": [{"id": f"AC-{n}", "behavior": "Visible behavior", "verification": "UI"}
                         for n in range(1, 8)],
        "tasks": [{"title": "Implement", "detail": "Add view", "paths": ["src/main.js"],
                   "acceptanceIds": ["AC-1"]}],
        "flow": [{"title": "Reassign", "detail": "Balance owners"}],
        "screens": [{"kind": kind, "title": kind, "caption": "Proposal", "html": "<p>Proposal</p>",
                     "css": "p{color:black}", "width": 800, "height": 600, "acceptanceIds": ["AC-1"]}
                    for kind in ("balanced", "mobile", "overloaded", "validation", "wireframe")],
    }
    issue = {"id": 123, "number": 7, "title": "Team Workload",
             "body": "\n".join(f"AC-{n}: Verify meaningful observable behavior." for n in range(1, 8)),
             "state": "open", "labels": [{"name": "agent-feature"}]}
    event = {"action": "opened", "sender": {"type": "User", "login": "Operator"},
             "repository": {"full_name": REPO}, "issue": issue}
    prd = f'# {issue["title"]}\n\n{issue["body"]}\n'
    spec, contract = b"frozen acceptance spec", b"frozen UI contract"
    hashes = {"patchSha256": publisher.digest(PATCH),
              "planSha256": publisher.digest(publisher.canonical_json(plan)),
              "prdSha256": publisher.digest(prd.encode()),
              "acceptanceSha256": publisher.digest(spec), "contractSha256": publisher.digest(contract)}
    assignment = {"schemaVersion": 1, "kind": "team-workload-v1", "repository": REPO,
                  "issueNumber": 7, "eventId": EVENT, "actor": "Operator", "baseCommit": BASE,
                  "title": issue["title"], "prd": prd, "prdSha256": hashes["prdSha256"], "status": "prepared"}
    provenance = {"schemaVersion": 1, "eventId": EVENT, "baseCommit": BASE, "status": "planned",
                  "sessionId": "sess_plan", "turnId": "turn_plan",
                  **{key: hashes[key] for key in ("planSha256", "prdSha256")}}
    cases = [{"title": f"case {n} [AC-{n % 7 + 1}]", "purpose": "Behavior",
              "acceptanceIds": [f"AC-{n % 7 + 1}"]} for n in range(10)]
    acceptance = {"schemaVersion": 1, "eventId": EVENT, "baseCommit": BASE,
                  **{key: value for key, value in hashes.items() if key != "patchSha256"},
                  "sessionId": "sess_acceptance", "turnId": "turn_acceptance",
                  "status": "authored-not-executed", "cases": cases}
    checks = {"schemaVersion": 1, "passed": True, "status": "passed", "errors": [],
              "baseCommit": BASE, "patchSha256": hashes["patchSha256"],
              "acceptanceSha256": hashes["acceptanceSha256"],
              "actualAcceptanceSha256": hashes["acceptanceSha256"], "infrastructureFailure": False,
              "executedFeatureCases": 10, "executedProjectCases": 10,
              "coveredAcceptanceIds": sorted(publisher.ACS),
              "commands": [{"name": name, "status": "passed", "exitCode": 0, "timedOut": False}
                           for name in sorted(publisher.REQUIRED_COMMANDS)],
              "featureCases": [{"title": row["title"], "acceptanceIds": row["acceptanceIds"],
                                "project": "desktop", "status": "expected", "expectedStatus": "passed",
                                "outcomes": ["passed"], "executed": True, "passed": True, "errors": []}
                               for row in cases]}
    candidate = {"schemaVersion": 1, "passed": True, "attempt": 1, "baseCommit": BASE, "tree": TREE,
                 **hashes, "checks": checks,
                 "review": {"schemaVersion": 1, "verdict": "approve", "summary": "Inspected",
                            "findings": [], "patchSha256": hashes["patchSha256"],
                            "acceptanceSha256": hashes["acceptanceSha256"]},
                 "exportManifest": {"eventId": EVENT + "-implementation", "baseCommit": BASE,
                                    "patchSha256": hashes["patchSha256"],
                                    "patchKey": f"repairs/{EVENT}-implementation/repair.patch"},
                 "implementationSessionId": "sess_implement", "implementationTurnId": "turn_implement",
                 "reviewSessionId": "sess_review", "reviewTurnId": "turn_review"}
    for folder in ("planning", "acceptance", "browser"):
        (root / folder).mkdir()
    (root / "planning/prd.md").write_text(prd)
    (root / "planning/index.html").write_text("<html>Frozen design viewer</html>")
    (root / "acceptance/workload.acceptance.spec.js").write_bytes(spec)
    (root / "acceptance/ui-contract.md").write_bytes(contract)
    (root / "verified.patch").write_bytes(PATCH)
    design = browser.design_links(root / "planning", plan, provenance, ORIGIN)
    browser_event = EVENT + "-browser-" + hashes["patchSha256"][:12]
    preview = {"eventId": browser_event, "parentEventId": EVENT, "baseCommit": BASE, **hashes,
               "sourceSha256": "d" * 64, "taskKind": "feature", "phase": "after",
               "url": f"{ORIGIN}/previews/{browser_event}/after/index.html"}

    def action(kind, selector="", value=""):
        return {"action": kind, "selector": selector, "value": value, "target": ""}

    def assertion(selector, text):
        return {"action": "assert_text", "selector": selector, "expected": text,
                "actual": text, "passed": True}

    actions = [action("click", "#reset"), action("click", browser.WORKLOAD),
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
    capture = {key: preview[key] for key in ("eventId", "phase", "sourceSha256")}
    capture.update({"outcome": "passed", "browserSessionId": "browser_session",
                    "previewUrl": preview["url"], "captureSha256": "e" * 64,
                    "screenshotSha256": "f" * 64, "frameCount": 20, "duration": 25,
                    "summary": "Balanced workload and verified persistence",
                    "actions": [{"action": "open"}, *actions, {"action": "finish"}]})
    for seconds, row in enumerate(capture["actions"]):
        row["seconds"] = seconds

    def call(name, args, value):
        return {"type": "mcp_call", "name": name, "turn_id": "turn_browser",
                "server_label": "agentcore_browser", "status": "completed", "arguments": args,
                "output": {"isError": False, "content": [{"type": "text", "text": json.dumps(value)}]}}

    items = [call("browser_open", {"event": browser_event, "phase": "after"},
                  {"url": preview["url"], "browserSessionId": capture["browserSessionId"]})]
    for row in actions:
        if row["action"] == "assert_text":
            items.append(call("browser_assert_text", {key: row[key] for key in ("selector", "expected")}, row))
        else:
            items.append(call("browser_action", {key: row[key] for key in ("action", "selector", "value", "target")},
                              {"url": preview["url"]}))
    items += [call("browser_finish", {"outcome": "passed", "summary": capture["summary"]}, capture),
              {"type": "message", "role": "assistant", "phase": "final_answer",
               "status": "completed", "turn_id": "turn_browser", "content": []}]
    common = {"schemaVersion": 1, "passed": True, "taskKind": "feature",
              "eventId": EVENT, "baseCommit": BASE, **hashes,
              "candidateSha256": publisher.digest(publisher.canonical_json(candidate))}
    evidence_url = f'{ORIGIN}/evidence/{EVENT}/{hashes["patchSha256"]}/feature'
    result = {**common, "sessionId": "sess_browser", "verificationTurnId": "turn_browser",
              "design": design, "browserEvidence": {"url": evidence_url,
                  "watchUrl": evidence_url + "/index.html", "reportUrl": evidence_url + "/report.json",
                  "browserSessions": {"after": capture["browserSessionId"]}, "after": capture["summary"]}}
    report = {**common, "purpose": "feature", "checksPassed": True, "reviewVerdict": "approve",
              "planningProvenanceSha256": publisher.digest(publisher.canonical_json(provenance)),
              "approvedCandidate": {key: candidate[key] for key in
                  ("implementationSessionId", "implementationTurnId", "reviewSessionId", "reviewTurnId")},
              "bmaSessionId": "sess_browser", "verificationTurnId": "turn_browser",
              "design": design, "preview": preview, "phases": {"after": capture},
              "verifiedObservations": browser.validate_feature_actions(actions),
              "artifacts": {name: {"url": evidence_url + "/" + name, "sha256": "1" * 64}
                            for name in ("after.mp4", "after.png")}}
    documents = {"assignment.json": assignment, "event.json": event, "candidate.json": candidate,
                 "planning/plan.json": plan, "planning/provenance.json": provenance,
                 "acceptance/manifest.json": acceptance, "browser/result.json": result,
                 "browser/report.json": report, "browser/items.json": {"items": items}}
    for path, value in documents.items():
        (root / path).write_text(json.dumps(value))
    return documents


class FakeGitHub:
    def __init__(self, context):
        self.context = context
        self.token = "never-a-real-token"
        self.remote, self.pr = None, None
        self.main, self.strict, self.feature_check = BASE, True, True
        self.issue = copy.deepcopy(context["issue"])
        self.calls, self.pushes = [], 0
        self.lose_push, self.lose_pr, self.lose_auto = False, False, False
        self.fail_push_before, self.auto_error, self.duplicate = False, False, False

    def new_pr(self):
        identity = self.context["identity"]
        return {"number": 8, "node_id": "PR_8", "html_url": f"https://github.com/{REPO}/pull/8",
                "state": "open", "draft": False, "merged_at": None, "auto_merge": None,
                "title": publisher.title(self.context), "body": publisher.body(self.context, HEAD),
                "user": {"login": publisher.BOT},
                "head": {"ref": identity["branch"], "sha": HEAD, "repo": {"full_name": REPO}},
                "base": {"ref": "main", "repo": {"full_name": REPO}}}

    def request(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, payload))
        prefix = f"/repos/{REPO}"
        if method == "GET":
            if path.startswith(prefix + "/pulls?"):
                return ([copy.deepcopy(self.pr)] * (2 if self.duplicate else 1)) if self.pr else []
            if path == prefix + "/pulls/8":
                return copy.deepcopy(self.pr)
            if path == prefix + "/git/ref/heads/main":
                return {"object": {"sha": self.main}}
            if path == prefix + "/git/ref/heads/" + self.context["identity"]["branch"]:
                return {"object": {"sha": self.remote}} if self.remote else None
            if path == prefix + "/branches/main":
                return {"protected": self.strict, "protection": {"enabled": self.strict,
                        "required_status_checks": {
                        "contexts": [publisher.FEATURE_CHECK] if self.feature_check else ["Repair scope"]}}}
            if path == prefix + "/issues/7":
                return copy.deepcopy(self.issue)
        if method == "POST" and path == prefix + "/pulls":
            self.pr = self.new_pr()
            if self.lose_pr:
                raise TimeoutError("PR response lost after creation")
            return copy.deepcopy(self.pr)
        if method == "POST" and path == "/graphql":
            if self.auto_error:
                return {"errors": [{"message": "Cannot enable"}]}
            self.pr["auto_merge"] = {"merge_method": "squash"}
            if self.lose_auto:
                raise TimeoutError("Auto-merge response lost")
            return {"data": {"enablePullRequestAutoMerge": {"pullRequest": {"number": 8}}}}
        raise AssertionError(f"Unexpected network operation: {method} {path}")

    def prepare(self, source, context):
        github = self

        class Prepared:
            head, tree = HEAD, TREE

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def push_new(self, token):
                github.pushes += 1
                if github.fail_push_before:
                    raise TimeoutError("Push outcome unknown; branch absent")
                github.remote = HEAD
                if github.lose_push:
                    raise TimeoutError("Push response lost")

        return Prepared()

    def writes(self):
        return [(method, path) for method, path, _ in self.calls if method != "GET"]


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.documents = fixture(self.root)
        self.canonical = patch.object(publisher, "canonical_without_credentials", return_value=PATCH).start()
        self.network_guard = patch.object(publisher.requests, "request",
                                         side_effect=AssertionError("No live network")).start()
        self.addCleanup(patch.stopall)
        self.options = dict(
            assignment_path=self.root / "assignment.json", event_path=self.root / "event.json",
            candidate_path=self.root / "candidate.json", planning=self.root / "planning",
            acceptance=self.root / "acceptance", browser_path=self.root / "browser/result.json",
            source=self.root, repository=REPO, operators="operator", event_name="issues", origin=ORIGIN)
        self.result_path = self.root / "publication.json"

    def validate(self):
        return publisher.validate_inputs(**self.options)

    def change(self, path, field, value):
        document = publisher.load(self.root / path)
        target = document
        parts = field.split(".")
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        target[parts[-1]] = value
        (self.root / path).write_text(json.dumps(document))

    def restore(self):
        for path, value in self.documents.items():
            (self.root / path).write_text(json.dumps(value))

    def run_publication(self, github):
        return publisher.publish(github.context, self.root, self.result_path, github, prepare=github.prepare)

    def test_valid_frozen_inputs_bind_all_stages_and_emit_one_watch_link(self):
        context = self.validate()
        self.assertEqual(context["identity"]["branch"], "codex/issue-7-issue-feature-123")
        self.assertEqual(set(context["stages"]), {"planning", "acceptance", "implementation", "review", "browser"})
        text = publisher.body(context, HEAD)
        self.assertEqual(text.count(context["evidence"]["watchUrl"]), 1)
        self.assertEqual(text.count(context["design"]["url"]), 1)
        self.assertNotIn(".gif", text.lower())
        self.assertNotIn("Closes #", text)
        self.assertIn("Refs #7", text)
        self.assertIn('"kind":"feature"', text)
        self.assertIn('"session":"sess_implement"', text)
        self.network_guard.assert_not_called()

    def test_validate_only_cli_needs_no_token_or_publication(self):
        arguments = ["--validate-only"]
        for flag, path in (("assignment", "assignment.json"), ("event", "event.json"),
                           ("candidate", "candidate.json"), ("planning", "planning"),
                           ("acceptance", "acceptance"), ("browser", "browser/result.json"),
                           ("result", "publication.json")):
            arguments += ["--" + flag, str(self.root / path)]
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "issues",
                                    "DEMO_OPERATORS": "operator", "BMA_PREVIEW_ORIGIN": ORIGIN},
                        clear=True), patch.object(publisher, "publish") as publish:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(publisher.main(arguments), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "validated-not-published")
            publish.assert_not_called()
        self.assertFalse(self.result_path.exists())

    def test_intake_authorization_matches_node_on_opened_and_labeled_events(self):
        module = Path(__file__).resolve().parents[1] / "scripts/feature-assignment.mjs"
        script = ("import {featureAssignment} from " + json.dumps(module.as_uri()) +
                  ";let x='';for await(const c of process.stdin)x+=c;"
                  "const v=JSON.parse(x);console.log(JSON.stringify(featureAssignment(v,'issues','operator',"
                  + json.dumps(BASE) + ")));")
        for action in ("opened", "labeled"):
            event = copy.deepcopy(self.documents["event.json"])
            event.update({"action": action, "label": {"name": "agent-feature"}})
            actual = json.loads(subprocess.check_output(["node", "--input-type=module", "-e", script],
                                                       input=json.dumps(event).encode(), timeout=15))
            self.assertEqual(actual, self.documents["assignment.json"])
            self.assertEqual(publisher.authorized_assignment(actual, event, "issues", "operator", REPO),
                             "codex/issue-7-issue-feature-123")

    def test_unauthorized_or_replayed_intake_is_rejected(self):
        cases = [("event.json", "sender.login", "intruder"), ("event.json", "sender.type", "Bot"),
                 ("event.json", "issue.labels", []), ("event.json", "issue.state", "closed"),
                 ("event.json", "issue.number", True), ("event.json", "issue.id", 2**53),
                 ("event.json", "repository.full_name", "other/repo"),
                 ("event.json", "action", "edited"), ("assignment.json", "eventId", "probe-feature"),
                 ("assignment.json", "actor", "intruder"), ("assignment.json", "prd", "changed")]
        for path, key, value in cases:
            with self.subTest(path=path, key=key):
                self.restore()
                self.change(path, key, value)
                with self.assertRaises((ValueError, KeyError)):
                    self.validate()

    def test_missing_or_failed_stage_and_hash_drift_is_rejected(self):
        cases = [("candidate.json", "passed", False), ("candidate.json", "attempt", 5),
                 ("candidate.json", "patchSha256", "0" * 64),
                 ("candidate.json", "review.verdict", "request_changes"),
                 ("candidate.json", "reviewSessionId", "sess_implement"),
                 ("candidate.json", "checks.commands.0.exitCode", 1),
                 ("candidate.json", "checks.commands.0.timedOut", True),
                 ("candidate.json", "checks.commands", []),
                 ("candidate.json", "checks.featureCases.0.outcomes", ["passed", "passed"]),
                 ("candidate.json", "checks.featureCases.0.executed", False),
                 ("candidate.json", "checks.actualAcceptanceSha256", "0" * 64),
                 ("candidate.json", "checks.coveredAcceptanceIds", ["AC-1"]),
                 ("candidate.json", "exportManifest.eventId", "issue-feature-999-implementation"),
                 ("planning/provenance.json", "eventId", "probe-design"),
                 ("planning/provenance.json", "status", "questions"),
                 ("acceptance/manifest.json", "planSha256", "0" * 64),
                 ("browser/result.json", "candidateSha256", "0" * 64),
                 ("browser/result.json", "sessionId", "sess_review"),
                 ("browser/report.json", "approvedCandidate.reviewTurnId", "stale"),
                 ("browser/report.json", "planningProvenanceSha256", "0" * 64),
                 ("browser/report.json", "preview.patchSha256", "0" * 64)]
        for path, key, value in cases:
            with self.subTest(path=path, key=key):
                self.restore()
                self.change(path, key, value)
                with self.assertRaises((ValueError, KeyError)):
                    self.validate()
        self.restore()
        self.change("browser/report.json", "artifacts", {"after.mp4": {"url": "https://evil.invalid"}})
        with self.assertRaises(ValueError):
            self.validate()

    def test_browser_success_flag_cannot_replace_real_ordered_calls(self):
        for field, value in (("items", []), ("items.0.status", "failed"),
                             ("items.0.server_label", "shell"), ("items.2.turn_id", "stale")):
            self.restore()
            self.change("browser/items.json", field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate()
        self.restore()
        self.change("browser/report.json", "phases.after.actions.3.actual", "fiction")
        with self.assertRaises(ValueError):
            self.validate()

    def test_changed_input_bytes_symlinks_duplicate_json_and_noncanonical_patch_stop(self):
        target = self.root / "acceptance/workload.acceptance.spec.js"
        target.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "acceptanceSha256"):
            self.validate()
        target.write_bytes(b"frozen acceptance spec")
        self.canonical.return_value = b"other canonical bytes"
        with self.assertRaisesRegex(ValueError, "canonical"):
            self.validate()
        self.canonical.return_value = PATCH
        (self.root / "verified.patch").unlink()
        (self.root / "verified.patch").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.validate()
        (self.root / "assignment.json").write_text('{"schemaVersion":1,"schemaVersion":2}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.validate()

    def test_design_and_evidence_urls_cannot_be_substituted(self):
        for path, key in (("browser/result.json", "browserEvidence.watchUrl"),
                          ("browser/result.json", "design.url"),
                          ("browser/report.json", "preview.url")):
            self.restore()
            self.change(path, key, "https://evil.invalid/tracker")
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate()

    def test_real_src_only_boundary_without_creating_any_commit(self):
        source = Path(__file__).resolve().parents[1]
        base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"]).decode().strip()
        good = (b"diff --git a/src/publication-boundary-fixture.txt b/src/publication-boundary-fixture.txt\n"
                b"new file mode 100644\n--- /dev/null\n+++ b/src/publication-boundary-fixture.txt\n"
                b"@@ -0,0 +1 @@\n+fixture\n")
        with patch.object(publisher, "canonical_without_credentials", self.real_canonical):
            actual = publisher.canonical_without_credentials(source, base, good)
            self.assertIn(b"src/publication-boundary-fixture.txt", actual)
            with self.assertRaises(subprocess.CalledProcessError):
                publisher.canonical_without_credentials(source, base, good.replace(b"src/", b".github/"))

    real_canonical = staticmethod(publisher.canonical_without_credentials)

    def test_fresh_publication_creates_once_and_uses_only_ordinary_auto_merge(self):
        github = FakeGitHub(self.validate())
        result = self.run_publication(github)
        self.assertEqual(result["status"], "auto-merge-enabled")
        self.assertEqual(github.pushes, 1)
        self.assertEqual(github.writes(), [("POST", f"/repos/{REPO}/pulls"), ("POST", "/graphql")])
        graphql = next(payload for _, path, payload in github.calls if path == "/graphql")
        self.assertIn("enablePullRequestAutoMerge", graphql["query"])
        self.assertNotIn("mergePullRequest(", graphql["query"])
        self.assertNotIn("admin", str(graphql).lower())
        github.calls.clear()
        self.run_publication(github)
        self.assertEqual(github.writes(), [])
        self.assertEqual(github.pushes, 1)

    def test_stale_main_missing_feature_gate_and_issue_edit_prevent_writes(self):
        for attribute, value in (("main", "f" * 40), ("strict", False), ("feature_check", False)):
            github = FakeGitHub(self.validate())
            setattr(github, attribute, value)
            with self.subTest(attribute=attribute), self.assertRaises(ValueError):
                self.run_publication(github)
            self.assertEqual(github.writes(), [])
            self.assertEqual(github.pushes, 0)
        github = FakeGitHub(self.validate())
        github.issue["body"] += "\nNew request"
        with self.assertRaisesRegex(ValueError, "edited"):
            self.run_publication(github)
        self.assertEqual(github.pushes, 0)

    def test_lost_push_response_is_reconciled_by_exact_branch_without_repush(self):
        github = FakeGitHub(self.validate())
        github.lose_push = True
        with self.assertRaises(TimeoutError):
            self.run_publication(github)
        self.assertEqual(publisher.load(self.result_path)["status"], "push-submitting")
        self.assertEqual(self.run_publication(github)["status"], "auto-merge-enabled")
        self.assertEqual(github.pushes, 1)

    def test_main_advance_after_push_stops_before_pr_creation(self):
        github = FakeGitHub(self.validate())
        original = github.request

        def advancing_request(method, path, *args, **kwargs):
            if github.pushes:
                github.main = "f" * 40
            return original(method, path, *args, **kwargs)

        github.request = advancing_request
        with self.assertRaisesRegex(ValueError, "Main changed"):
            self.run_publication(github)
        self.assertEqual(github.pushes, 1)
        self.assertEqual(github.writes(), [])
        self.assertEqual(publisher.load(self.result_path)["status"], "branch-ready")

    def test_uncertain_push_without_branch_is_not_blindly_retried(self):
        github = FakeGitHub(self.validate())
        github.fail_push_before = True
        with self.assertRaises(TimeoutError):
            self.run_publication(github)
        github.calls.clear()
        with self.assertRaisesRegex(ValueError, "no repeat push"):
            self.run_publication(github)
        self.assertEqual(github.pushes, 1)
        self.assertEqual(github.writes(), [])

    def test_lost_pr_response_reconciles_exact_pr_without_duplicate(self):
        github = FakeGitHub(self.validate())
        github.lose_pr = True
        with self.assertRaises(TimeoutError):
            self.run_publication(github)
        self.assertEqual(publisher.load(self.result_path)["status"], "pr-submitting")
        self.assertEqual(self.run_publication(github)["status"], "auto-merge-enabled")
        self.assertEqual(github.writes().count(("POST", f"/repos/{REPO}/pulls")), 1)

    def test_uncertain_pr_creation_without_visible_pr_never_reposts(self):
        github = FakeGitHub(self.validate())
        github.lose_pr = True
        with self.assertRaises(TimeoutError):
            self.run_publication(github)
        github.pr = None
        github.calls.clear()
        with self.assertRaisesRegex(ValueError, "no duplicate"):
            self.run_publication(github)
        self.assertEqual(github.writes(), [])

    def test_lost_auto_merge_response_reconciles_without_second_mutation(self):
        github = FakeGitHub(self.validate())
        github.lose_auto = True
        with self.assertRaises(TimeoutError):
            self.run_publication(github)
        self.assertEqual(publisher.load(self.result_path)["status"], "auto-merge-submitting")
        github.calls.clear()
        self.assertEqual(self.run_publication(github)["status"], "auto-merge-enabled")
        self.assertEqual(github.writes(), [])

    def test_rejected_or_disabled_auto_merge_never_retries_or_overrides(self):
        github = FakeGitHub(self.validate())
        github.auto_error = True
        with self.assertRaisesRegex(ValueError, "not confirmed"):
            self.run_publication(github)
        github.calls.clear()
        with self.assertRaisesRegex(ValueError, "instead of resubmitting"):
            self.run_publication(github)
        self.assertEqual(github.writes(), [])
        self.assertIsNotNone(github.pr)

    def test_known_exact_pr_recovers_without_checkpoint(self):
        github = FakeGitHub(self.validate())
        github.remote, github.pr = HEAD, github.new_pr()
        github.pr["auto_merge"] = {"merge_method": "squash"}
        self.assertEqual(self.run_publication(github)["status"], "auto-merge-enabled")
        self.assertEqual(github.pushes, 0)
        self.assertEqual(github.writes(), [])

    def test_known_pr_without_checkpoint_and_absent_auto_merge_is_read_only(self):
        github = FakeGitHub(self.validate())
        github.remote, github.pr = HEAD, github.new_pr()
        with self.assertRaisesRegex(ValueError, "instead of resubmitting"):
            self.run_publication(github)
        self.assertEqual(github.pushes, 0)
        self.assertEqual(github.writes(), [])
        self.assertEqual(publisher.load(self.result_path)["status"], "recovered-pr")

    def test_known_branch_without_checkpoint_or_pr_is_ambiguous(self):
        github = FakeGitHub(self.validate())
        github.remote = HEAD
        with self.assertRaisesRegex(ValueError, "history is ambiguous"):
            self.run_publication(github)
        self.assertEqual(github.writes(), [])
        self.assertEqual(github.pushes, 0)

    def test_changed_branch_pr_body_author_or_duplicate_pr_cannot_be_adopted(self):
        variants = ("branch", "body", "author", "duplicate", "head", "base", "closed")
        for variant in variants:
            github = FakeGitHub(self.validate())
            github.remote, github.pr = HEAD, github.new_pr()
            if variant == "branch":
                github.remote = "f" * 40
            elif variant == "body":
                github.pr["body"] += "\nEdited"
            elif variant == "author":
                github.pr["user"]["login"] = "someone"
            elif variant == "duplicate":
                github.duplicate = True
            elif variant == "head":
                github.pr["head"]["sha"] = "f" * 40
            elif variant == "base":
                github.pr["base"]["repo"]["full_name"] = "other/repo"
            else:
                github.pr["state"] = "closed"
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                self.run_publication(github)
            self.assertEqual(github.writes(), [])

    def test_merged_known_pr_reconciles_read_only_after_main_moves_and_branch_deleted(self):
        github = FakeGitHub(self.validate())
        github.pr = github.new_pr()
        github.pr.update({"state": "closed", "merged_at": "2026-10-18T12:00:00Z",
                          "merge_commit_sha": "f" * 40})
        github.main = "f" * 40
        result = self.run_publication(github)
        self.assertEqual(result["status"], "merged")
        self.assertEqual(github.writes(), [])
        self.assertEqual(github.pushes, 0)

    def test_changed_checkpoint_cannot_publish_different_identity(self):
        github = FakeGitHub(self.validate())
        self.run_publication(github)
        self.change("publication.json", "identity.acceptanceSha256", "f" * 64)
        github.calls.clear()
        with self.assertRaisesRegex(ValueError, "checkpoint binds"):
            self.run_publication(github)
        self.assertEqual(github.calls, [])

    def test_removed_recorded_pr_cannot_be_recreated(self):
        github = FakeGitHub(self.validate())
        self.run_publication(github)
        github.pr = None
        github.calls.clear()
        with self.assertRaisesRegex(ValueError, "Recorded PR is missing"):
            self.run_publication(github)
        self.assertEqual(github.writes(), [])

    def test_checkpoint_lock_and_symlink_rejection(self):
        with publisher.checkpoint_lock(self.result_path):
            with self.assertRaisesRegex(RuntimeError, "Another local"):
                with publisher.checkpoint_lock(self.result_path):
                    self.fail("Second owner acquired lock")
        self.result_path.symlink_to(self.root / "candidate.json")
        with self.assertRaisesRegex(ValueError, "symlink"):
            publisher.save_checkpoint(self.result_path, {})

    def test_push_is_create_only_uses_exact_https_repo_and_secrets_only_in_env(self):
        context = self.validate()
        prepared = publisher.PreparedCommit(self.root, context)
        prepared.head, prepared.env = HEAD, {}
        prepared.git = Mock()
        prepared.push_new("synthetic-secret")
        args, kwargs = prepared.git.call_args
        branch = context["identity"]["branch"]
        self.assertIn(f"--force-with-lease=refs/heads/{branch}:", args)
        self.assertIn(f"https://github.com/{REPO}.git", args)
        self.assertNotIn("synthetic-secret", " ".join(args))
        self.assertEqual(kwargs["env"]["GIT_CONFIG_VALUE_5"], "false")

    def test_prepared_commit_is_deterministic_single_parent_without_running_commit(self):
        context = self.validate()
        calls = []

        def fake_git(instance, *args, **kwargs):
            calls.append((args, kwargs, dict(instance.env)))
            if args[0] == "write-tree":
                return TREE.encode()
            if args[0] == "show":
                return b"1234567890\n"
            if args[0] == "commit-tree":
                return HEAD.encode()
            return b""

        with patch.object(publisher.PreparedCommit, "git", fake_git):
            for _ in range(2):
                with publisher.PreparedCommit(self.root, context) as prepared:
                    self.assertEqual(prepared.head, HEAD)
        commits = [row for row in calls if row[0][0] == "commit-tree"]
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0][0], ("commit-tree", TREE, "-p", BASE))
        self.assertEqual(commits[0][1]["data"], commits[1][1]["data"])
        self.assertIn(publisher.marker(context).encode(), commits[0][1]["data"])
        self.assertEqual(commits[0][2]["GIT_AUTHOR_DATE"], "1234567891 +0000")
        self.assertEqual(commits[0][2]["GIT_COMMITTER_DATE"], "1234567891 +0000")
        self.assertFalse(any(row[0][0] == "push" for row in calls))

    def test_git_child_environment_excludes_inherited_credentials(self):
        with patch.dict(os.environ, {"GH_TOKEN": "secret", "AWS_ACCESS_KEY_ID": "secret",
                                     "GIT_CONFIG_VALUE_0": "hostile", "NODE_OPTIONS": "hostile"}):
            env = publisher.clean_git_env(self.root)
        for key in ("GH_TOKEN", "AWS_ACCESS_KEY_ID", "NODE_OPTIONS"):
            self.assertNotIn(key, env)
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], os.devnull)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)


if __name__ == "__main__":
    unittest.main()
