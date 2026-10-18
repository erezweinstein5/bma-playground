import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import feature_checks as checks


def report_fixture(count=8, project_names=("desktop", "mobile")):
    """Representative Playwright JSON, including nested describes and projects."""
    specs = []
    for index in range(count):
        specs.append({
            "title": f"[AC-{index % 7 + 1}] behavior {index + 1}",
            "file": "workload.acceptance.spec.js", "line": index + 3, "column": 1,
            "id": f"case-{index}", "ok": True, "tags": [],
            "tests": [{
                "projectName": project, "projectId": project,
                "timeout": 30_000, "expectedStatus": "passed",
                "status": "expected", "annotations": [],
                "results": [{"status": "passed", "retry": 0, "workerIndex": 0,
                             "duration": 10, "errors": [], "annotations": []}],
            } for project in project_names],
        })
    return {
        "config": {"forbidOnly": True, "projects": [
            {"name": name, "retries": 0, "repeatEach": 1} for name in project_names]},
        "suites": [{"title": "workload.acceptance.spec.js", "specs": [],
                    "suites": [{"title": "Workload interactions", "specs": specs}]}],
        "errors": [],
        "stats": {"expected": count * len(project_names), "unexpected": 0,
                  "flaky": 0, "skipped": 0},
    }


def specs(report):
    return report["suites"][0]["suites"][0]["specs"]


class FeatureReportTests(unittest.TestCase):
    def test_manifest_titles_require_exact_clean_desktop_runs(self):
        report = report_fixture(count=10, project_names=("desktop",))
        titles = [row["title"] for row in specs(report)]
        self.assertTrue(checks.parse_feature_report(report, expected_titles=titles)["passed"])
        for changed in (titles[:-1], titles + ["extra [AC-1]"],
                        [*titles[:-1], "different [AC-1]"], [titles[0]] * 10):
            self.assertFalse(checks.parse_feature_report(report, expected_titles=changed)["passed"])
        self.assertFalse(checks.parse_feature_report(report_fixture(count=10),
                                                    expected_titles=titles)["passed"])
        specs(report)[0]["tests"][0]["results"].append(
            copy.deepcopy(specs(report)[0]["tests"][0]["results"][0]))
        self.assertFalse(checks.parse_feature_report(report, expected_titles=titles)["passed"])

    def test_nested_report_extracts_passing_title_tags_and_distinct_cases(self):
        result = checks.parse_feature_report(report_fixture())
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(result["executedFeatureCases"], 8)
        self.assertEqual(result["executedProjectCases"], 16)
        self.assertEqual(len(result["featureCases"]), 16)
        self.assertEqual(result["coveredAcceptanceIds"], [f"AC-{n}" for n in range(1, 8)])
        self.assertIn("Workload interactions", result["featureCases"][0]["fullTitle"])

    def test_project_duplicates_cannot_inflate_minimum_cases(self):
        report = report_fixture(count=4)
        specs(report)[0]["title"] = " ".join(f"[AC-{n}]" for n in range(1, 8))
        result = checks.parse_feature_report(report)
        self.assertFalse(result["passed"])
        self.assertEqual(result["executedProjectCases"], 8)
        self.assertEqual(result["executedFeatureCases"], 4)
        self.assertTrue(any("8 distinct" in error for error in result["errors"]))

    def test_metadata_tags_and_partial_title_matches_do_not_count(self):
        report = report_fixture()
        for spec in specs(report):
            spec["title"] = "case [AC-10] AC-7 [AC-07]"
            spec["tags"] = list(checks.REQUIRED_ACS)
        result = checks.parse_feature_report(report)
        self.assertFalse(result["passed"])
        self.assertEqual(result["coveredAcceptanceIds"], ["AC-10"])

    def test_skipped_expected_failure_and_flaky_results_are_rejected(self):
        for expected, outcome, status, retry, annotation in (
            ("skipped", "skipped", "skipped", 0, "skip"),
            ("failed", "expected", "failed", 0, "fail"),
            ("passed", "flaky", "passed", 1, None),
            ("passed", "unexpected", "failed", 0, None),
            ("passed", "unexpected", "timedOut", 0, None),
            ("passed", "unexpected", "interrupted", 0, None),
        ):
            with self.subTest(expected=expected, outcome=outcome, status=status):
                report = report_fixture(project_names=("desktop",))
                test = specs(report)[6]["tests"][0]
                test.update(expectedStatus=expected, status=outcome)
                test["results"][0].update(status=status, retry=retry)
                if annotation:
                    test["annotations"] = [{"type": annotation}]
                report["stats"]["expected"] -= 1
                report["stats"][outcome] += 1
                result = checks.parse_feature_report(report)
                self.assertFalse(result["passed"])
                self.assertNotIn("AC-7", result["coveredAcceptanceIds"])

    def test_multiple_attempts_even_if_both_pass_are_rejected(self):
        report = report_fixture()
        test = specs(report)[0]["tests"][0]
        test["results"].append(copy.deepcopy(test["results"][0]))
        self.assertFalse(checks.parse_feature_report(report)["passed"])

    def test_unexecuted_and_empty_results_do_not_count(self):
        for results in ([], [{"status": "passed", "retry": 0, "workerIndex": -1,
                              "duration": 0, "errors": []}]):
            with self.subTest(results=results):
                report = report_fixture(project_names=("desktop",))
                specs(report)[0]["tests"][0]["results"] = results
                result = checks.parse_feature_report(report)
                self.assertFalse(result["passed"])
                self.assertEqual(result["executedFeatureCases"], 7)

    def test_forbidden_annotations_are_rejected_even_with_a_passing_status(self):
        for note in ("skip", "fixme", "fail", "only"):
            for place in ("test", "attempt"):
                with self.subTest(note=note, place=place):
                    report = report_fixture()
                    test = specs(report)[0]["tests"][0]
                    target = test if place == "test" else test["results"][0]
                    target["annotations"] = [{"type": note, "description": "not allowed"}]
                    self.assertFalse(checks.parse_feature_report(report)["passed"])

    def test_focused_suite_or_test_and_missing_forbid_only_fail(self):
        for place in ("suite", "test", "config"):
            with self.subTest(place=place):
                report = report_fixture()
                if place == "suite":
                    report["suites"][0]["only"] = True
                elif place == "test":
                    specs(report)[0]["only"] = True
                else:
                    report["config"]["forbidOnly"] = False
                self.assertFalse(checks.parse_feature_report(report)["passed"])

    def test_global_errors_wrong_file_or_duplicate_case_fail(self):
        for mutation in (
            lambda report: report["errors"].append({"message": "test.only forbidden"}),
            lambda report: specs(report)[0].update(file="board.spec.js"),
            lambda report: specs(report).append(copy.deepcopy(specs(report)[0])),
            lambda report: report["stats"].update(expected=999),
            lambda report: specs(report)[0]["tests"].pop(),
        ):
            with self.subTest(mutation=mutation):
                report = report_fixture()
                mutation(report)
                self.assertFalse(checks.parse_feature_report(report)["passed"])

    def test_disabled_or_enlarged_timeout_and_retry_configuration_fail(self):
        for timeout in (0, 30_001, -1, True, "30000"):
            with self.subTest(timeout=timeout):
                report = report_fixture()
                specs(report)[0]["tests"][0]["timeout"] = timeout
                self.assertFalse(checks.parse_feature_report(report)["passed"])
        for field, value in (("retries", 1), ("repeatEach", 2), ("retries", False)):
            with self.subTest(field=field, value=value):
                report = report_fixture()
                report["config"]["projects"][0][field] = value
                self.assertFalse(checks.parse_feature_report(report)["passed"])

    def test_malformed_report_shapes_fail_closed_without_raising(self):
        malformed = [None, [], {}, {"suites": []}]
        for field in ("config", "suites", "stats", "errors"):
            report = report_fixture()
            del report[field]
            malformed.append(report)
        for field in ("expectedStatus", "annotations", "results", "status", "projectName"):
            report = report_fixture()
            del specs(report)[0]["tests"][0][field]
            malformed.append(report)
        for field in ("status", "retry", "duration", "errors", "workerIndex"):
            report = report_fixture()
            del specs(report)[0]["tests"][0]["results"][0][field]
            malformed.append(report)
        for value in (None, "unexpected", {}, [None]):
            report = report_fixture()
            specs(report)[0]["tests"][0]["results"] = value
            malformed.append(report)
        for report in malformed:
            with self.subTest(report=report):
                result = checks.parse_feature_report(report)
                self.assertFalse(result["passed"])
                self.assertTrue(result["errors"])

    def test_invalid_json_duplicate_keys_and_nonfinite_values_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            for raw in ('{"stats": {}, "stats": {}}', '{"value": NaN}', '{"value": Infinity}',
                        '{"suites":', 'prefix {"suites": []}', "[] trailing"):
                with self.subTest(raw=raw):
                    path.write_text(raw)
                    with self.assertRaises(ValueError):
                        checks._strict_json(path)

    def test_nonfinite_or_overflowing_durations_fail_as_results(self):
        for duration in (float("nan"), float("inf"), 10 ** 400, True, -1):
            with self.subTest(duration=duration):
                report = report_fixture()
                specs(report)[0]["tests"][0]["results"][0]["duration"] = duration
                result = checks.parse_feature_report(report)
                self.assertFalse(result["passed"])
                self.assertTrue(result["errors"])


class EnvironmentTests(unittest.TestCase):
    def test_safe_https_origin_rejects_credentials_path_query_local_and_js(self):
        self.assertEqual(checks.https_origin("https://Example.com:443/"), "https://example.com")
        self.assertEqual(checks.https_origin("https://example.com:8443"), "https://example.com:8443")
        for value in (None, "", "http://example.com", "https://example.com/path",
                      "https://user:secret@example.com", "https://example.com?token=secret",
                      "https://example.com#fragment", "https://127.0.0.1", "https://localhost",
                      "https://example.com:99999", "https://example.com\\evil",
                      "https://example.com'\nconsole.log('oops')", "https://example..com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                checks.https_origin(value)

    def test_live_config_disables_inherited_server_and_overrides_project_urls(self):
        with tempfile.TemporaryDirectory() as folder:
            config = checks._controller_config(Path(folder), None, feature_only=True,
                                               live_origin="https://app.example.com")
            text = config.read_text()
            self.assertIn('const url = "https://app.example.com"', text)
            self.assertIn("webServer: undefined", text)
            self.assertNotIn("npm run preview", text)
            self.assertNotIn("127.0.0.1", text)
            self.assertIn("...project.use, baseURL: url", text)
            self.assertIn("forbidOnly: true, retries: 0", text)

    def test_child_cannot_inherit_cloud_git_npm_or_node_credentials(self):
        secrets = {
            "AWS_ACCESS_KEY_ID": "secret-access-key", "AWS_SECRET_ACCESS_KEY": "secret-key",
            "AWS_SESSION_TOKEN": "secret-session", "AWS_PROFILE": "privileged-profile",
            "AWS_SHARED_CREDENTIALS_FILE": "/user/.aws/credentials",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://credentials.invalid",
            "AWS_WEB_IDENTITY_TOKEN_FILE": "/user/token",
            "AWS_BEARER_TOKEN_BEDROCK": "secret-bedrock",
            "GH_TOKEN": "secret-github", "GITHUB_TOKEN": "secret-github",
            "GH_CONFIG_DIR": "/user/.config/gh",
            "NPM_TOKEN": "secret-npm", "npm_config_registry": "https://secret.invalid",
            "NODE_OPTIONS": "--require=/user/secret.js", "PYTHONPATH": "/user/python",
            "GIT_CONFIG_COUNT": "99", "GIT_CONFIG_KEY_99": "credential.helper",
            "GIT_CONFIG_VALUE_99": "secret-helper", "SSH_AUTH_SOCK": "/user/agent",
            "APP_URL": "http://127.0.0.1:4173", "HTTP_PROXY": "http://user:password@proxy",
            "PLAYWRIGHT_JSON_OUTPUT_FILE": "/user/old-report.json",
        }
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, secrets):
            root = Path(folder)
            env = checks.isolated_env(root)
            for key in secrets:
                if key == "GIT_CONFIG_COUNT":
                    self.assertNotEqual(env[key], secrets[key])
                else:
                    self.assertNotIn(key, env)
            self.assertEqual(env["HOME"], str(root / "home"))
            self.assertEqual(env["npm_config_cache"], str(root / "npm-cache"))
            self.assertNotEqual(env["npm_config_userconfig"], env["npm_config_globalconfig"])
            self.assertEqual(Path(env["npm_config_userconfig"]).read_text(), "")
            self.assertEqual(Path(env["npm_config_globalconfig"]).read_text(), "")
            command = checks._run(
                "environment", [sys.executable, "-I", "-c",
                                "import json, os; print(json.dumps(dict(os.environ)))"],
                root, env, root, timeout=10)
            self.assertEqual(command["status"], "passed")
            inherited = json.loads(Path(command["stdout"]).read_text())
            for value in secrets.values():
                self.assertNotIn(value, inherited.values())
            self.assertEqual(os.environ["AWS_SECRET_ACCESS_KEY"], "secret-key")

    def test_existing_browser_cache_is_shared_by_path_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            browsers = root / "caller-cache"
            browsers.mkdir()
            (browsers / "browser").write_text("binary-placeholder")
            with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(browsers)}):
                env = checks.isolated_env(root / "isolated")
            self.assertEqual(env["PLAYWRIGHT_BROWSERS_PATH"], str(browsers.resolve()))
            self.assertNotEqual(env["HOME"], str(Path.home()))
            self.assertFalse((Path(env["HOME"]) / "browser").exists())

    def test_timeout_is_reported_and_process_group_is_killed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "descendant-finished"
            child = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch()"
            program = (
                "import subprocess, sys, time; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(20)"
            )
            result = checks._run("timeout", [sys.executable, "-c", program],
                                 root, checks.isolated_env(root), root, timeout=0.2)
            self.assertEqual(result["status"], "timeout")
            self.assertTrue(result["timedOut"])
            self.assertLess(result["durationSeconds"], 5)
            # If a descendant survives, it creates the marker after its parent dies.
            import time
            time.sleep(1.1)
            self.assertFalse(marker.exists())


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Feature runner test")
        self.git("config", "user.email", "test@example.invalid")
        for name, text in {
            "src/app.js": "export const feature = false;\n",
            "package.json": '{"type":"module","scripts":{"test":"node --test"}}\n',
            "package-lock.json": '{"lockfileVersion":3}\n',
            "playwright.config.js": "export default {projects:[{name:'desktop'}]};\n",
            "tests/browser/board.spec.js": "// frozen board regression\n",
            "tests/browser/repair.spec.js": "// frozen repair regression\n",
        }.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git("add", ".")
        self.git("commit", "-qm", "Pinned test base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        (self.source / "src/app.js").write_text("export const feature = true;\n")
        self.candidate = self.git("diff", "HEAD")
        self.acceptance = self.root / "acceptance.spec.js"
        self.acceptance.write_text("// frozen BMA-generated spec fixture\n")
        self.acceptance_hash = hashlib.sha256(self.acceptance.read_bytes()).hexdigest()
        self.output = self.root / "output"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args],
                                       stderr=subprocess.PIPE)

    def check(self, **overrides):
        args = dict(source=self.source, base=self.base, patch=self.candidate,
                    acceptance_file=self.acceptance, output=self.output,
                    acceptance_sha256=self.acceptance_hash)
        args.update(overrides)
        return checks.check_candidate(**args)

    def test_hash_mismatch_fails_before_any_subprocess_and_writes_result(self):
        with patch.object(checks, "_run") as run:
            result = self.check(acceptance_sha256="0" * 64)
        run.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertIn("SHA-256 mismatch", " ".join(result["errors"]))
        self.assertEqual(result["actualAcceptanceSha256"], self.acceptance_hash)
        self.assertEqual(json.loads((self.output / "result.json").read_text()), result)

    def test_unpinned_base_and_invalid_frozen_file_fail_as_results(self):
        for overrides in ({"base": "HEAD"}, {"acceptance_sha256": "invalid"},
                          {"acceptance_file": self.root / "missing.spec.js"}):
            with self.subTest(overrides=overrides), patch.object(checks, "_run") as run:
                result = self.check(**overrides)
                run.assert_not_called()
                self.assertFalse(result["passed"])

    def test_unwritable_output_returns_a_failure_without_running_commands(self):
        self.output.write_text("A file cannot be used as an artifact directory")
        with patch.object(checks, "_run") as run:
            result = self.check()
        run.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertIn("Cannot persist check result", " ".join(result["errors"]))

    def test_existing_src_only_boundary_rejects_regression_changes(self):
        (self.source / "tests/browser/repair.spec.js").write_text("// weakened\n")
        before = self.git("status", "--porcelain")
        result = self.check(patch=self.git("diff", "HEAD"))
        self.assertFalse(result["passed"])
        self.assertEqual([item["name"] for item in result["commands"]], ["canonical-patch"])
        self.assertIn("src-only", " ".join(result["errors"]))
        self.assertEqual(self.git("status", "--porcelain"), before)
        self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), self.base)

    def fake_commands(self, *, failure=None, feature_report=None, corrupt_acceptance=False,
                      corrupt_regression=False):
        """Keep real git/canonicalization; stub only npm/browser execution."""
        real_run = checks._run
        observed = []

        def run(name, argv, cwd, env, logs, **kwargs):
            observed.append((name, list(argv), Path(cwd), dict(env)))
            if not name.startswith(("npm-", "board-", "repair-", "feature-browser")):
                return real_run(name, argv, cwd, env, logs, **kwargs)
            repo = Path(cwd)
            self.assertNotEqual(repo, self.source)
            self.assertEqual((repo / "src/app.js").read_text(), "export const feature = true;\n")
            self.assertEqual((repo / "tests/browser/repair.spec.js").read_text(),
                             "// frozen repair regression\n")
            if name == "npm-ci":
                self.assertEqual((repo / checks.ACCEPTANCE_PATH).read_bytes(), self.acceptance.read_bytes())
                self.assertEqual((repo / "package.json").read_bytes(),
                                 self.git("show", f"{self.base}:package.json"))
            out, err = logs / f"{name}.stdout.log", logs / f"{name}.stderr.log"
            if name == "feature-browser":
                raw = report_fixture() if feature_report is None else feature_report
                out.write_text(raw if isinstance(raw, str) else json.dumps(raw))
            else:
                out.write_text("simulated command output\n")
            err.write_text("")
            if corrupt_acceptance and name == "npm-build":
                (repo / checks.ACCEPTANCE_PATH).write_text("// changed during checks")
            if corrupt_regression and name == "npm-test":
                (repo / "tests/browser/repair.spec.js").write_text("// weakened at runtime")
            return {"name": name, "command": list(argv), "status": "failed" if failure == name else "passed",
                    "exitCode": 1 if failure == name else 0, "timedOut": False,
                    "stdout": str(out), "stderr": str(err)}

        return run, observed

    def test_disposable_clone_uses_pinned_files_and_does_not_touch_source(self):
        # A dirty source file is not part of the pinned regression contract.
        (self.source / "tests/browser/repair.spec.js").write_text("// local user edits\n")
        before = self.git("status", "--porcelain")
        branch = self.git("symbolic-ref", "HEAD")
        run, observed = self.fake_commands()
        with socket.socket() as existing_server:
            existing_server.bind(("127.0.0.1", 0))
            existing_server.listen()
            with patch.dict(os.environ, {"AWS_SESSION_TOKEN": "secret", "GH_TOKEN": "secret"}), \
                 patch.object(checks, "_run", side_effect=run):
                result = self.check()
            self.assertNotEqual(result["serverPort"], existing_server.getsockname()[1])
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(self.git("status", "--porcelain"), before)
        self.assertEqual(self.git("symbolic-ref", "HEAD"), branch)
        self.assertEqual(result["baseCommit"], self.base)
        self.assertEqual(result["acceptanceSha256"], self.acceptance_hash)
        canonical = Path(result["artifacts"]) / "candidate.patch"
        self.assertEqual(result["patchSha256"], hashlib.sha256(canonical.read_bytes()).hexdigest())
        names = [entry[0] for entry in observed]
        self.assertEqual(names[-6:], ["npm-ci", "npm-test", "npm-build", "board-browser",
                                     "repair-browser", "feature-browser"])
        for name, argv, cwd, env in observed:
            self.assertNotIn("AWS_SESSION_TOKEN", env)
            self.assertNotIn("GH_TOKEN", env)
            self.assertNotEqual(env["HOME"], str(Path.home()))
            self.assertFalse(Path(env["HOME"]).exists(), "Temporary HOME must be removed")
            if name == "npm-ci":
                self.assertEqual(argv, ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"])
            if name.endswith("-browser"):
                self.assertEqual(argv[0:3], ["node", "node_modules/@playwright/test/cli.js", "test"])
                self.assertIn("--retries=0", argv)
                self.assertIn("--forbid-only", argv)
        config = (Path(result["artifacts"]) / "feature-checks.playwright.config.mjs").read_text()
        self.assertIn("import base from './playwright.config.js'", config)
        self.assertIn("...project.use", config)
        self.assertIn("reuseExistingServer: false", config)
        self.assertIn(f"--port {result['serverPort']} --strictPort", config)

    def test_failed_command_cannot_be_overridden_by_passing_feature_report(self):
        run, _ = self.fake_commands(failure="npm-test")
        with patch.object(checks, "_run", side_effect=run):
            result = self.check()
        self.assertFalse(result["passed"])
        self.assertEqual(result["executedFeatureCases"], 8)
        self.assertIn("npm-test: failed", " ".join(result["errors"]))

    def test_live_runs_only_frozen_remote_suite_and_never_reserves_local_port(self):
        report = report_fixture(count=10, project_names=("desktop",))
        titles = [row["title"] for row in specs(report)]
        run, observed = self.fake_commands(feature_report=report)
        with patch.object(checks, "_run", side_effect=run), patch.object(checks.socket, "socket") as socket_call:
            result = self.check(live_origin="https://app.example.com", expected_titles=titles)
        self.assertTrue(result["passed"], result["errors"])
        socket_call.assert_not_called()
        self.assertNotIn("serverPort", result)
        self.assertEqual(result["liveOrigin"], "https://app.example.com")
        names = [entry[0] for entry in observed]
        self.assertEqual(names[-2:], ["npm-ci", "feature-browser"])
        for name in ("npm-test", "npm-build", "board-browser", "repair-browser"):
            self.assertNotIn(name, names)
        config = (Path(result["artifacts"]) / "feature-only.playwright.config.mjs").read_text()
        self.assertIn("webServer: undefined", config)
        self.assertNotIn("npm run preview", config)

    def test_exact_manifest_mismatch_fails_even_with_enough_passing_cases(self):
        report = report_fixture(count=10, project_names=("desktop",))
        run, _ = self.fake_commands(feature_report=report)
        titles = [row["title"] for row in specs(report)]
        titles[-1] = "missing frozen case [AC-3]"
        with patch.object(checks, "_run", side_effect=run):
            result = self.check(expected_titles=titles, live_origin="https://app.example.com")
        self.assertFalse(result["passed"])
        self.assertIn("frozen manifest", " ".join(result["errors"]))

    def test_invalid_live_origin_fails_before_any_commands(self):
        with patch.object(checks, "_run") as run:
            result = self.check(live_origin="https://user:secret@app.example.com")
        run.assert_not_called()
        self.assertFalse(result["passed"])

    def test_install_failure_records_unrun_commands(self):
        run, observed = self.fake_commands(failure="npm-ci")
        with patch.object(checks, "_run", side_effect=run):
            result = self.check()
        self.assertFalse(result["passed"])
        self.assertEqual(observed[-1][0], "npm-ci")
        self.assertEqual(len([c for c in result["commands"] if c["status"] == "not-run"]), 5)

    def test_malformed_report_cannot_reuse_prior_success(self):
        run, _ = self.fake_commands()
        with patch.object(checks, "_run", side_effect=run):
            first = self.check()
        self.assertTrue(first["passed"])
        run, _ = self.fake_commands(feature_report='{"suites": [')
        with patch.object(checks, "_run", side_effect=run):
            second = self.check()
        self.assertFalse(second["passed"])
        self.assertNotEqual(first["artifacts"], second["artifacts"])
        self.assertEqual(second["executedFeatureCases"], 0)
        self.assertEqual(json.loads((self.output / "result.json").read_text()), second)

    def test_frozen_acceptance_tampering_stops_feature_execution(self):
        run, observed = self.fake_commands(corrupt_acceptance=True)
        with patch.object(checks, "_run", side_effect=run):
            result = self.check()
        self.assertFalse(result["passed"])
        self.assertNotIn("feature-browser", [entry[0] for entry in observed])
        self.assertIn("Frozen acceptance spec changed", " ".join(result["errors"]))

    def test_runtime_regression_tampering_cannot_approve_the_candidate(self):
        run, observed = self.fake_commands(corrupt_regression=True)
        with patch.object(checks, "_run", side_effect=run):
            result = self.check()
        self.assertFalse(result["passed"])
        self.assertNotIn("board-browser", [entry[0] for entry in observed])
        self.assertIn("Protected base or controller inputs changed", " ".join(result["errors"]))


if __name__ == "__main__":
    unittest.main()
