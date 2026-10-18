"""Run a src-only candidate against the pinned base and frozen feature contract.

``check_candidate`` writes result.json and a fresh run directory beneath output.
Failures are data for the correction loop. ``featureCases`` contains project runs;
``executedFeatureCases`` counts distinct spec cases, not desktop/mobile duplicates.
This is process/environment isolation, not a sandbox for hostile native code.
"""
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit


ACCEPTANCE_PATH = "tests/browser/workload.acceptance.spec.js"
REQUIRED_ACS = {f"AC-{number}" for number in range(1, 8)}
MAX_REPORT = 16 * 1024 * 1024
COMMAND_TIMEOUT = 300
BROWSER_TIMEOUT = 240
TEST_TIMEOUT_MS = 30_000


def https_origin(value):
    """Normalize one explicitly configured HTTPS origin, never a URL from a PR."""
    if not isinstance(value, str) or len(value) > 300 or any(
            ord(char) <= 32 or ord(char) >= 127 for char in value):
        raise ValueError("Expected a configured HTTPS origin")
    parsed = urlsplit(value)
    host = parsed.hostname
    if (parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment or "\\" in value or "%" in value):
        raise ValueError("Expected a configured HTTPS origin without credentials or a path")
    # DNS names and public IPv4 are sufficient for this CloudFront deployment.
    if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[1-9][0-9]{0,4})?", parsed.netloc):
        raise ValueError("Invalid HTTPS origin authority")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid HTTPS port")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError("Live acceptance requires a public HTTPS origin")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                                  for part in host.split(".")):
            raise ValueError("Invalid HTTPS hostname")
    else:
        if not address.is_global:
            raise ValueError("Live acceptance requires a public HTTPS origin")
    return f"https://{host}" + (f":{parsed.port}" if parsed.port not in {None, 443} else "")


def isolated_env(root: Path) -> dict:
    """Allowlist child settings; never copy credentials, npmrc, or node options."""
    home = root / "home"
    cache = root / "npm-cache"
    temporary = root / "tmp"
    for path in (home, cache, temporary, root / "git-template"):
        path.mkdir(parents=True, exist_ok=True)
    user_npmrc, global_npmrc = root / "npm-user.config", root / "npm-global.config"
    user_npmrc.write_text("")
    global_npmrc.write_text("")
    # Keep executable lookup without inheriting shell startup or other settings.
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "CI": "true",
        "LANG": "C.UTF-8",
        "npm_config_cache": str(cache),
        "npm_config_userconfig": str(user_npmrc),
        "npm_config_globalconfig": str(global_npmrc),
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "npm_config_update_notifier": "false",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TEMPLATE_DIR": str(root / "git-template"),
        "GIT_TERMINAL_PROMPT": "0",
        # Also covers the existing canonical_patch helper's subprocesses.
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": os.devnull,
        "GIT_CONFIG_KEY_1": "protocol.allow",
        "GIT_CONFIG_VALUE_1": "never",
        "GIT_CONFIG_KEY_2": "protocol.file.allow",
        "GIT_CONFIG_VALUE_2": "always",
    }
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured == "0":
        env["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    elif configured and Path(configured).is_absolute():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(configured).resolve())
    else:
        # Only share the browser binary location, never the caller's HOME.
        caller_home = Path.home()
        if sys.platform == "darwin":
            browsers = caller_home / "Library/Caches/ms-playwright"
        else:
            browsers = Path(os.environ.get("XDG_CACHE_HOME", caller_home / ".cache")) / "ms-playwright"
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers.resolve())
    return env


def _run(name, argv, cwd, env, logs, *, timeout=COMMAND_TIMEOUT, stdin=None):
    """Capture separate logs and kill the entire process group, including servers."""
    stdout = logs / f"{name}.stdout.log"
    stderr = logs / f"{name}.stderr.log"
    value = {"name": name, "command": [str(arg) for arg in argv],
             "status": "failed", "exitCode": None, "timedOut": False,
             "stdout": str(stdout), "stderr": str(stderr), "timeoutSeconds": timeout}
    started = time.monotonic()
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process = None
        try:
            process = subprocess.Popen(value["command"], cwd=cwd, env=env,
                                       stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True)
            process.communicate(input=stdin, timeout=timeout)
            value["exitCode"] = process.returncode
            value["status"] = "passed" if process.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            value["timedOut"] = True
            value["status"] = "timeout"
            err.write(f"\nController timeout after {timeout} seconds.\n".encode())
        except OSError as error:
            err.write(f"\n{type(error).__name__}: {error}\n".encode())
        finally:
            if process is not None:
                # A successful parent can still leave a Vite/browser descendant.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
                value["exitCode"] = process.returncode
    value["durationSeconds"] = round(time.monotonic() - started, 3)
    return value


def _strict_json(path: Path):
    if path.stat().st_size > MAX_REPORT:
        raise ValueError("Feature JSON report is oversized")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Nonfinite JSON value: {value}")

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs,
                      parse_constant=invalid_constant)


def parse_feature_report(report, *, expected_titles=None, required_ids=None) -> dict:
    """Fail closed on incomplete, flaky, focused, skipped, or xfailed reports."""
    summary = {"passed": False, "featureCases": [], "executedFeatureCases": 0,
               "executedProjectCases": 0, "coveredAcceptanceIds": [], "errors": []}
    errors = summary["errors"]
    covered = set()
    executed = set()
    seen = set()
    totals = {key: 0 for key in ("expected", "unexpected", "flaky", "skipped")}
    from feature_contract import validate_acceptance_ids
    required = REQUIRED_ACS if required_ids is None else validate_acceptance_ids(required_ids)

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def integer(value, minimum=0):
        return type(value) is int and value >= minimum

    def annotations(value):
        require(isinstance(value, list), "Missing or malformed test annotations")
        require(all(isinstance(item, dict) and isinstance(item.get("type"), str)
                    for item in value), "Malformed test annotation")
        return {item["type"] for item in value}

    def walk(suite, parents=()):
        require(isinstance(suite, dict), "Malformed suite")
        require(isinstance(suite.get("title"), str), "Missing suite title")
        require(suite.get("only", False) is False, "Focused suite is forbidden")
        specs, children = suite.get("specs"), suite.get("suites", [])
        require(isinstance(specs, list) and isinstance(children, list), "Malformed suite contents")
        titles = (*parents, suite["title"])
        for spec in specs:
            require(isinstance(spec, dict), "Malformed feature spec")
            require(spec.get("only", False) is False, "Focused test is forbidden")
            title = spec.get("title")
            require(isinstance(title, str) and bool(title.strip()), "Missing feature test title")
            require(spec.get("file") in {ACCEPTANCE_PATH, Path(ACCEPTANCE_PATH).name},
                    "Report contains a test outside the frozen feature spec")
            require(integer(spec.get("line"), 1) and integer(spec.get("column"), 1),
                    "Missing feature test location")
            require(type(spec.get("ok")) is bool, "Missing spec outcome")
            runs = spec.get("tests")
            require(isinstance(runs, list) and bool(runs), "Feature case has no project runs")
            tags = set(re.findall(r"\[(AC-[1-9][0-9]*)\]", title))
            identity = (spec["file"], spec["line"], spec["column"], *titles, title)
            for test in runs:
                require(isinstance(test, dict), "Malformed project test")
                project = test.get("projectName")
                require(isinstance(project, str) and project in projects, "Unknown test project")
                key = (identity, project)
                require(key not in seen, "Duplicate feature project case")
                seen.add(key)
                require(test.get("status") in totals, "Missing or invalid test outcome")
                totals[test["status"]] += 1
                require(test.get("expectedStatus") in {"passed", "failed", "skipped",
                                                       "timedOut", "interrupted"},
                        "Missing or invalid expected status")
                timeout = test.get("timeout")
                require(integer(timeout, 1) and timeout <= TEST_TIMEOUT_MS,
                        "Feature test timeout is disabled or exceeds the controller bound")
                notes = annotations(test.get("annotations"))
                results = test.get("results")
                require(isinstance(results, list), "Missing test results")
                case_errors = []
                if test["expectedStatus"] != "passed":
                    case_errors.append("skipped or expected-failure test")
                if test["status"] != "expected" or spec["ok"] is not True:
                    case_errors.append("test did not pass cleanly")
                if len(results) != 1:
                    case_errors.append("expected exactly one attempt without retries")
                did_execute = False
                outcomes = []
                for attempt in results:
                    require(isinstance(attempt, dict), "Malformed attempt")
                    status = attempt.get("status")
                    require(status in {"passed", "failed", "skipped", "timedOut", "interrupted"},
                            "Missing or invalid attempt status")
                    require(integer(attempt.get("retry")), "Missing or invalid retry number")
                    require(type(attempt.get("workerIndex")) is int, "Missing execution worker")
                    duration = attempt.get("duration")
                    require(type(duration) in (int, float) and math.isfinite(duration) and duration >= 0,
                            "Missing or invalid attempt duration")
                    require(isinstance(attempt.get("errors"), list), "Missing attempt errors")
                    notes |= annotations(attempt.get("annotations", []))
                    outcomes.append(status)
                    if status != "skipped" and attempt["workerIndex"] >= 0:
                        did_execute = True
                    if (status != "passed" or attempt["retry"] != 0 or attempt["workerIndex"] < 0
                            or attempt["errors"] or attempt.get("error")):
                        case_errors.append("failed, skipped, retried, or unexecuted attempt")
                if notes & {"skip", "fixme", "fail", "only"}:
                    case_errors.append("forbidden skip/fixme/fail/only annotation")
                passed = not case_errors and did_execute
                summary["featureCases"].append({
                    "title": title, "fullTitle": " > ".join((*titles, title)),
                    "file": spec["file"], "line": spec["line"], "project": project,
                    "status": test["status"], "expectedStatus": test["expectedStatus"],
                    "outcomes": outcomes, "executed": did_execute, "passed": passed,
                    "acceptanceIds": sorted(tags), "errors": case_errors,
                    "observations": [str(error.get("message", error))[:5000]
                                     for attempt in results for error in attempt.get("errors", [])],
                })
                if did_execute:
                    executed.add(identity)
                    summary["executedProjectCases"] += 1
                if passed:
                    covered.update(tags)
                if case_errors:
                    errors.append(f"{project}: {title}: " + "; ".join(case_errors))
        for child in children:
            walk(child, titles)

    try:
        require(isinstance(report, dict), "Feature report must be an object")
        require(isinstance(report.get("errors"), list), "Missing report errors")
        if report["errors"]:
            errors.append("Playwright reported global errors: " + json.dumps(report["errors"]))
        config = report.get("config")
        require(isinstance(config, dict) and config.get("forbidOnly") is True,
                "Report does not confirm forbidOnly")
        project_list = config.get("projects")
        require(isinstance(project_list, list) and bool(project_list), "Missing report projects")
        projects = set()
        for project in project_list:
            require(isinstance(project, dict) and isinstance(project.get("name"), str),
                    "Malformed report project")
            require(project["name"] not in projects, "Duplicate project")
            projects.add(project["name"])
            require(type(project.get("retries")) is int and project["retries"] == 0,
                    "Report must have zero retries")
            require(type(project.get("repeatEach")) is int and project["repeatEach"] == 1,
                    "Repeated cases cannot inflate feature coverage")
        suites = report.get("suites")
        require(isinstance(suites, list) and bool(suites), "Missing feature suites")
        for suite in suites:
            walk(suite)
        stats = report.get("stats")
        require(isinstance(stats, dict), "Missing report statistics")
        for key, count in totals.items():
            require(integer(stats.get(key)) and stats[key] == count,
                    f"Inconsistent or missing {key} statistics")
        if totals["unexpected"] or totals["flaky"] or totals["skipped"]:
            errors.append("Feature report contains failed, flaky, or skipped outcomes")
        # All configured projects must actually run every collected logical case.
        require(all((identity, project) in seen for identity in executed for project in projects),
                "Feature report is missing a configured project run")
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        errors.append(f"Invalid feature report: {error}")
    summary["executedFeatureCases"] = len(executed)
    summary["coveredAcceptanceIds"] = sorted(covered)
    minimum = 8 if required_ids is None else 1
    if len(executed) < minimum:
        errors.append(f"At least {minimum} distinct executed feature cases required; found {len(executed)}")
    if required - covered:
        errors.append("Missing passing title tags: " + ", ".join(sorted(required - covered)))
    if required_ids is not None and covered - required:
        errors.append("Passing title tags reference unknown plan criteria")
    if required_ids is not None and expected_titles is None:
        errors.append("Generic feature checks require the frozen manifest titles")
    if expected_titles is not None:
        errors.extend(exact_case_errors(summary, expected_titles, required_ids=required_ids))
    summary["passed"] = not errors
    return summary


def exact_case_errors(summary, expected_titles, *, required_ids=None):
    """The frozen manifest, not collected test count, defines the complete suite."""
    low, high = (10, 30) if required_ids is None else (1, 60)
    if (not isinstance(expected_titles, (list, tuple)) or not low <= len(expected_titles) <= high
            or any(not isinstance(title, str) or not title.strip() for title in expected_titles)
            or len(set(expected_titles)) != len(expected_titles)):
        return ["Invalid frozen manifest titles"]
    cases = summary.get("featureCases", [])
    if (not isinstance(cases, list) or any(not isinstance(row, dict) for row in cases)
            or len(cases) != len(expected_titles)
            or {row.get("title") for row in cases} != set(expected_titles)):
        return ["Executed feature titles differ from the frozen manifest"]
    if any(row.get("project") != "desktop" or row.get("passed") is not True
           or row.get("executed") is not True or row.get("errors") != []
           or row.get("status") != "expected" or row.get("expectedStatus") != "passed"
           or row.get("outcomes") != ["passed"] for row in cases):
        return ["Every frozen feature case must execute once on desktop and pass cleanly"]
    if (type(summary.get("executedFeatureCases")) is not int
            or summary["executedFeatureCases"] != len(expected_titles)
            or type(summary.get("executedProjectCases")) is not int
            or summary["executedProjectCases"] != len(expected_titles)):
        return ["Executed feature counts differ from the frozen manifest"]
    if required_ids is not None:
        from feature_contract import validate_acceptance_ids
        required = validate_acceptance_ids(required_ids)
        observed = set()
        for row in cases:
            tags = set(re.findall(r"\[(AC-[1-9][0-9]*)\]", row["title"]))
            refs = row.get("acceptanceIds")
            if (not tags or not tags <= required or not isinstance(refs, list)
                    or any(not isinstance(ref, str) for ref in refs)
                    or len(set(refs)) != len(refs) or set(refs) != tags):
                return ["Executed feature case tags differ from the pinned plan"]
            observed.update(tags)
        if observed != required or summary.get("coveredAcceptanceIds") != sorted(required):
            return ["Executed coverage differs from the pinned plan"]
    return []


def _controller_config(repo: Path, port: int, *, feature_only=False, live_origin=None):
    """Overlay only execution controls; inherit base projects and assertions."""
    path = repo / ("feature-only.playwright.config.mjs" if feature_only
                   else "feature-checks.playwright.config.mjs")
    if path.exists() or path.is_symlink():
        raise ValueError("Controller config path already exists in the base")
    url = https_origin(live_origin) if live_origin is not None else f"http://127.0.0.1:{port}"
    server = ("  webServer: undefined,\n" if live_origin is not None else
              "  webServer: {\n"
              "    ...base.webServer,\n"
              f"    command: 'npm run preview -- --host 127.0.0.1 --port {port} --strictPort',\n"
              "    url, reuseExistingServer: false, timeout: 60000,\n"
              "  },\n")
    path.write_text(
        "import base from './playwright.config.js';\n"
        f"const url = {json.dumps(url)};\n"
        "export default {\n"
        "  ...base,\n"
        "  testDir: './tests/browser',\n"
        "  forbidOnly: true, retries: 0, repeatEach: 1, workers: 2,\n"
        f"  timeout: {TEST_TIMEOUT_MS}, globalTimeout: {BROWSER_TIMEOUT * 1000},\n"
        "  failOnFlakyTests: true,\n"
        "  use: { ...base.use, baseURL: url },\n"
        "  projects: base.projects" +
        (".filter(project => project.name === 'desktop')" if feature_only else "") +
        ".map(project => ({\n"
        "    ...project, retries: 0, repeatEach: 1,\n"
        f"    timeout: {TEST_TIMEOUT_MS},\n"
        "    use: { ...project.use, baseURL: url },\n"
        "  })),\n"
        + server +
        "};\n", encoding="utf-8")
    return path


def _protected_digest(repo: Path, names):
    """Detect runtime writes to the base's non-src files and controller inputs."""
    digest = hashlib.sha256()
    for name in sorted(names):
        path = repo / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(repo):
            raise ValueError(f"Protected check input is missing or not a regular file: {name}")
        digest.update(name.encode() + b"\0")
        digest.update(str(path.stat().st_mode).encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _application_digest(repo):
    return _protected_digest(repo, {path.relative_to(repo).as_posix()
                                   for path in (repo / "src").rglob("*")
                                   if path.is_file() or path.is_symlink()})


def check_candidate(source: Path, base: str, patch: bytes, acceptance_file: Path,
                    output: Path, acceptance_sha256: str, *, expected_titles=None,
                    live_origin=None, required_ids=None) -> dict:
    """Check a candidate without editing source or inheriting AWS/GitHub secrets."""
    output = Path(output).resolve()
    logs = output / ("run-" + uuid.uuid4().hex)
    result = {
        "schemaVersion": 1, "passed": False, "status": "failed", "baseCommit": base,
        "patchSha256": None, "acceptanceSha256": acceptance_sha256,
        "commands": [], "featureCases": [], "executedFeatureCases": 0,
        "executedProjectCases": 0, "coveredAcceptanceIds": [], "errors": [],
        "artifacts": str(logs), "resultFile": str(output / "result.json"),
    }
    try:
        output.mkdir(parents=True, exist_ok=True)
        logs.mkdir()
        if required_ids is not None:
            from feature_contract import validate_acceptance_ids
            required_ids = validate_acceptance_ids(required_ids)
            result["acceptanceIds"] = sorted(required_ids)
            if expected_titles is None:
                raise ValueError("Generic feature checks require frozen manifest titles")
        if live_origin is not None:
            live_origin = https_origin(live_origin)
            result["liveOrigin"] = live_origin
        if not isinstance(base, str) or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", base):
            raise ValueError("base must be a full pinned commit SHA")
        if not isinstance(acceptance_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", acceptance_sha256):
            raise ValueError("acceptance_sha256 must be a SHA-256 digest")
        acceptance = Path(acceptance_file).read_bytes()
        actual = hashlib.sha256(acceptance).hexdigest()
        result["actualAcceptanceSha256"] = actual
        if actual != acceptance_sha256:
            raise ValueError("Frozen acceptance SHA-256 mismatch")
        if not acceptance or len(acceptance) > 512 * 1024:
            raise ValueError("Frozen acceptance spec must be nonempty and at most 512 KiB")
        acceptance.decode("utf-8")
        if not isinstance(patch, bytes):
            raise ValueError("patch must be bytes")
        source = Path(source).resolve(strict=True)
        if not source.is_dir():
            raise ValueError("source must be a local repository directory")
        with tempfile.TemporaryDirectory(prefix="bma-feature-check-") as temporary:
            root = Path(temporary).resolve()
            repo = root / "repo"
            env = isolated_env(root)

            def run(name, argv, cwd=root, **kwargs):
                command = _run(name, argv, cwd, env, logs, **kwargs)
                result["commands"].append(command)
                if command["status"] != "passed":
                    result["errors"].append(f"{name}: {command['status']} (exit {command['exitCode']})")
                    for stream in ("stdout", "stderr"):
                        command[stream + "Tail"] = Path(command[stream]).read_text(errors="replace")[-8000:]
                return command

            # A child process keeps canonical_patch's git calls in the same clean
            # environment without mutating os.environ in a concurrent controller.
            helper = (
                "import sys; from pathlib import Path; "
                "sys.path.insert(0, sys.argv[1]); "
                "from patch_boundary import canonical_patch; "
                "sys.stdout.buffer.write(canonical_patch(Path(sys.argv[2]), "
                "sys.argv[3], sys.stdin.buffer.read()))"
            )
            verified = run("canonical-patch", [sys.executable, "-I", "-B", "-c", helper,
                           str(Path(__file__).resolve().parent), str(source), base], stdin=patch)
            if verified["status"] != "passed":
                raise ValueError("Candidate failed the existing src-only canonical_patch boundary")
            canonical = Path(verified["stdout"]).read_bytes()
            if not canonical:
                raise ValueError("Canonical patch is empty")
            result["patchSha256"] = hashlib.sha256(canonical).hexdigest()
            (logs / "candidate.patch").write_bytes(canonical)
            for name, argv in (
                ("clone", ["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(source), str(repo)]),
                ("checkout-base", ["git", "-C", str(repo), "checkout", "--quiet", "--detach", base]),
                ("verify-base", ["git", "-C", str(repo), "rev-parse", "HEAD"]),
            ):
                command = run(name, argv)
                if command["status"] != "passed":
                    raise ValueError(f"Cannot prepare pinned candidate: {name}")
            if Path(command["stdout"]).read_text().strip() != base:
                raise ValueError("Disposable clone does not match the pinned base")
            applied = run("apply-candidate", ["git", "apply", "--index", "-"], cwd=repo, stdin=canonical)
            if applied["status"] != "passed":
                raise ValueError("Verified candidate could not be applied")
            destination = repo / ACCEPTANCE_PATH
            if destination.exists() or destination.is_symlink():
                raise ValueError("Frozen feature path already exists in the base")
            if destination.parent.resolve() != repo / "tests/browser":
                raise ValueError("Acceptance directory escapes the disposable clone")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(acceptance)
            (logs / "workload.acceptance.spec.js").write_bytes(acceptance)
            # Bind an OS-assigned loopback port. A race after release fails closed:
            # Playwright refuses an existing listener and Vite uses --strictPort.
            port = None
            if live_origin is None:
                with socket.socket() as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    port = reservation.getsockname()[1]
                result["serverPort"] = port
            config = _controller_config(repo, port, live_origin=live_origin)
            feature_config = _controller_config(repo, port, feature_only=True, live_origin=live_origin)
            shutil.copyfile(config, logs / config.name)
            shutil.copyfile(feature_config, logs / feature_config.name)
            tracked = run("protected-inputs", ["git", "ls-files", "-z"], cwd=repo)
            if tracked["status"] != "passed":
                raise ValueError("Cannot enumerate protected base inputs")
            protected = {name for name in Path(tracked["stdout"]).read_text().split("\0")
                         if name and not name.startswith("src/")}
            protected.update({ACCEPTANCE_PATH, config.name, feature_config.name})
            protected_digest = _protected_digest(repo, protected)
            application_digest = _application_digest(repo)
            installed = run("npm-ci", ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=repo)
            if _protected_digest(repo, protected) != protected_digest:
                raise ValueError("Protected base or controller inputs changed during npm ci")
            if _application_digest(repo) != application_digest:
                raise ValueError("Candidate source changed during npm ci")
            checks = [
                ("npm-test", ["npm", "test"]),
                ("npm-build", ["npm", "run", "build"]),
            ]
            cli = ["node", "node_modules/@playwright/test/cli.js", "test",
                   f"--config={config.name}", "--forbid-only", "--retries=0",
                   f"--timeout={TEST_TIMEOUT_MS}", f"--global-timeout={BROWSER_TIMEOUT * 1000}",
                   "--fail-on-flaky-tests"]
            checks.extend([
                ("board-browser", [*cli, "tests/browser/board.spec.js", "--reporter=line"]),
                ("repair-browser", [*cli, "tests/browser/repair.spec.js", "--reporter=line"]),
                ("feature-browser", [*(f"--config={feature_config.name}" if
                                      arg == f"--config={config.name}" else arg for arg in cli),
                                     ACCEPTANCE_PATH, "--reporter=json"]),
            ])
            if live_origin is not None:
                # Deployment already built and published this exact revision.
                # Execute only the frozen suite against its remote baseURL.
                checks = [checks[-1]]
            if installed["status"] != "passed":
                for name, argv in checks:
                    result["commands"].append({"name": name, "command": argv, "status": "not-run",
                                               "exitCode": None, "reason": "npm ci failed"})
            else:
                for name, argv in checks:
                    if name == "feature-browser":
                        if hashlib.sha256(destination.read_bytes()).hexdigest() != acceptance_sha256:
                            raise ValueError("Frozen acceptance spec changed before execution")
                    command = run(name, argv, cwd=repo)
                    if hashlib.sha256(destination.read_bytes()).hexdigest() != acceptance_sha256:
                        raise ValueError("Frozen acceptance spec changed during checks")
                    if _protected_digest(repo, protected) != protected_digest:
                        raise ValueError(f"Protected base or controller inputs changed during {name}")
                    if _application_digest(repo) != application_digest:
                        raise ValueError(f"Candidate source changed during {name}")
                # stdout is the JSON reporter stream. Do not mine a JSON-looking
                # substring from malformed output or reuse an earlier run's file.
                result["featureReport"] = command["stdout"]
                feature = parse_feature_report(_strict_json(Path(command["stdout"])),
                                               expected_titles=expected_titles,
                                               required_ids=required_ids)
                result.update({key: value for key, value in feature.items()
                               if key not in {"passed", "errors"}})
                result["errors"].extend(feature["errors"])
                if hashlib.sha256(destination.read_bytes()).hexdigest() != acceptance_sha256:
                    raise ValueError("Frozen acceptance spec changed during execution")
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, RecursionError) as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    result["passed"] = not result["errors"] and bool(result["commands"]) and all(
        command["status"] == "passed" for command in result["commands"])
    install = next((command for command in result["commands"] if command["name"] == "npm-ci"), None)
    result["infrastructureFailure"] = not install or install["status"] != "passed"
    result["status"] = "passed" if result["passed"] else "failed"
    try:
        pending = logs / "result.json"
        pending.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        published = output / (logs.name + ".result.pending")
        shutil.copyfile(pending, published)
        published.replace(output / "result.json")
    except OSError as error:
        result["passed"] = False
        result["status"] = "failed"
        result["errors"].append(f"Cannot persist check result: {error}")
    return result
