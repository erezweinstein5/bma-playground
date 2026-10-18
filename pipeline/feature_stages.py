"""Durable, separate BMA roles for the feature-demo rehearsal."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from bma_client import BmaClient


def digest(data):
    return hashlib.sha256(data).hexdigest()


def final_json(items, turn_id, max_bytes=180_000):
    finals = [item for item in items if item.get("turn_id") == turn_id
              and item.get("type") == "message" and item.get("role") == "assistant"
              and item.get("phase") == "final_answer" and item.get("status") == "completed"]
    if len(finals) != 1:
        raise ValueError("Stage must have exactly one correlated completed final answer")
    text = "".join(part.get("text", "") for part in finals[0].get("content", [])
                   if part.get("type") in {"output_text", "text"}).strip()
    if len(text.encode()) > max_bytes:
        raise ValueError("Stage response exceeds its artifact limit")
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Stage result must be an object")
    return value


class FeatureStages:
    def __init__(self, event, base, directory):
        if not re.fullmatch(r"(issue|comment|probe)-[a-z0-9-]+", event):
            raise ValueError("Invalid feature event")
        if not re.fullmatch(r"[0-9a-f]{40}", base):
            raise ValueError("Invalid feature source revision")
        self.event, self.base = event, base
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.bucket = os.environ["BMA_ARTIFACT_BUCKET"]
        region = os.environ["AWS_REGION"]
        aws = boto3.Session(region_name=region)
        self.s3 = aws.client("s3", config=Config(connect_timeout=20, read_timeout=120,
            retries={"mode": "standard", "max_attempts": 5}))
        self.client = BmaClient(aws, region)

    def _key(self, stage):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,60}", stage):
            raise ValueError("Invalid stage")
        return f"state/features/{self.event}/{stage}.json"

    def read(self, key, limit=500_000, optional=False):
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if optional and error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                return None
            raise
        with response["Body"] as body:
            data = body.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Stage object exceeds its bound")
        return data

    def save(self, stage, state):
        data = json.dumps(state, indent=2).encode()
        folder = self.directory / stage
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / "state.pending"
        temporary.write_bytes(data)
        temporary.replace(folder / "state.json")
        self.s3.put_object(Bucket=self.bucket, Key=self._key(stage), Body=data,
                           ContentType="application/json")

    def freeze(self, key, data, content_type="application/octet-stream"):
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType=content_type, IfNoneMatch="*")
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
                raise
            if self.read(key, limit=len(data)) != data:
                raise ValueError("Immutable stage artifact already exists with different bytes") from error

    def run(self, stage, instructions, prompt, *, archive=None, extra_identity=None):
        """A repeated stage can collect its one turn, but never resubmits input."""
        key = self._key(stage)
        folder = self.directory / stage
        folder.mkdir(parents=True, exist_ok=True)
        stage_event = self.event + "-" + stage
        source_digest = digest(archive) if archive is not None else None
        if archive is not None:
            if len(archive) > 5 * 1024 * 1024:
                raise ValueError("Stage source exceeds runtime limit")
            prepare = shlex.join(["python", "/opt/bma/tools/workspace_io.py", "prepare",
                "--bucket", self.bucket, "--event", stage_event, "--base", self.base,
                "--sha256", source_digest])
            prompt = (f"First run exactly:\n{prepare}\n"
                      f"Then work in /mnt/home/workspace/jobs/{stage_event}/repo. "
                      "If preparation fails, stop and report it. Do not modify the helper.\n" + prompt)
        identity = {"baseCommit": self.base, "promptSha256": digest(prompt.encode()),
                    "instructionsSha256": digest(instructions.encode()),
                    "sourceArchiveSha256": source_digest, **(extra_identity or {})}
        prior = self.read(key, optional=True)
        if prior:
            state = json.loads(prior)
            if state["identity"] != identity:
                raise ValueError("Stage already exists with different inputs; inspect it before another run")
            if not state.get("sessionId"):
                raise RuntimeError("Session creation is uncertain; inspect the service before proceeding")
            session_id = state["sessionId"]
            current = self.client.get_session(session_id)
            if current.get("status") != "idle" or current.get("error"):
                raise RuntimeError("Existing stage session is not idle without error; no new input submitted")
            items = self.client.items(session_id)
            recorded_turn = (state.get("outcome") or {}).get("turnId")
            relevant = [item for item in items
                        if recorded_turn is None or item.get("turn_id") == recorded_turn]
            turns = {item["turn_id"] for item in relevant
                     if item.get("role") == "assistant" and item.get("phase") == "final_answer"
                     and item.get("status") == "completed"}
            if len(turns) != 1 or any(item.get("status") in {"in_progress", "queued", "incomplete"}
                                     for item in relevant):
                raise RuntimeError("Existing stage has no unambiguous completed result")
            turn_id = turns.pop()
            outcome = state.get("outcome", {"turnId": turn_id, "completionSource": "durable_items"})
            if outcome.get("turnId") != turn_id:
                raise ValueError("Durable turn identity changed")
        else:
            state = {"schemaVersion": 1, "stage": stage, "eventId": self.event,
                     "identity": identity, "status": "creating",
                     "createdAt": datetime.now(timezone.utc).isoformat()}
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(state).encode(),
                               ContentType="application/json", IfNoneMatch="*")
            if archive is not None:
                self.s3.put_object(Bucket=self.bucket, Key=f"sources/{stage_event}.tar",
                                   Body=archive, ContentType="application/x-tar", IfNoneMatch="*")
            session = self.client.create({
                "agent": {"model": os.environ["BMA_MODEL"], "instructions": instructions},
                "environment": {"type": "aws_bedrock_agentcore",
                    "runtime_arn": os.environ["BMA_RUNTIME_ARN"], "runtime_qualifier": "DEFAULT",
                    "workspace_directory": "/mnt/home/workspace"},
                "role_arn": os.environ["BMA_SESSION_ROLE_ARN"],
            })
            session_id = session["id"]
            state.update({"sessionId": session_id, "status": "submitting"})
            self.save(stage, state)
            (folder / "prompt.txt").write_text(prompt)
            print(f"BMA {stage}: {session_id}", flush=True)
            outcome = self.client.run_turn(session_id, prompt, folder / "events.jsonl")
            items = self.client.items(session_id)
        (folder / "items.json").write_text(json.dumps(items, indent=2))
        state.update({"outcome": outcome, "status": "completed"})
        self.save(stage, state)
        return state, items

    def continue_implementation(self, correction, session_id, prompt, identity):
        """At most four correction turns, on the original implementer session."""
        if type(correction) is not int or correction not in (1, 2, 3, 4):
            raise ValueError("Automatic correction is limited to four cycles")
        stage = f"correction-{correction}"
        key = self._key(stage)
        identity = {**identity, "sessionId": session_id, "promptSha256": digest(prompt.encode())}
        folder = self.directory / stage
        folder.mkdir(parents=True, exist_ok=True)
        prior = self.read(key, optional=True)
        if prior:
            state = json.loads(prior)
            if state["identity"] != identity:
                raise ValueError("Correction was already submitted with different inputs")
            current = self.client.get_session(session_id)
            if current.get("status") != "idle" or current.get("error"):
                raise RuntimeError("Correction is not idle without error; no input was resubmitted")
            items = self.client.items(session_id)
            recorded_turn = (state.get("outcome") or {}).get("turnId")
            new = [item for item in items if
                   (item.get("turn_id") == recorded_turn if recorded_turn else
                    item.get("turn_id") not in state["priorTurns"])]
            finals = [item for item in new if item.get("role") == "assistant"
                      and item.get("phase") == "final_answer" and item.get("status") == "completed"]
            turns = {item["turn_id"] for item in finals}
            if len(turns) != 1 or any(item.get("status") in {"in_progress", "queued", "incomplete"}
                                     for item in new):
                raise RuntimeError("No unambiguous completed correction to collect")
            outcome = state.get("outcome", {"turnId": turns.pop(), "completionSource": "durable_items"})
        else:
            current = self.client.get_session(session_id)
            if current.get("status") != "idle" or current.get("error"):
                raise RuntimeError("Implementer must be idle before a correction")
            previous = sorted({item["turn_id"] for item in self.client.items(session_id)
                               if item.get("turn_id")})
            state = {"schemaVersion": 1, "stage": stage, "eventId": self.event,
                     "sessionId": session_id, "identity": identity, "priorTurns": previous,
                     "status": "submitting", "createdAt": datetime.now(timezone.utc).isoformat()}
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(state).encode(),
                               ContentType="application/json", IfNoneMatch="*")
            self.save(stage, state)
            (folder / "prompt.txt").write_text(prompt)
            print(f"BMA {stage} on original session: {session_id}", flush=True)
            outcome = self.client.run_turn(session_id, prompt, folder / "events.jsonl")
            items = self.client.items(session_id)
        (folder / "items.json").write_text(json.dumps(items, indent=2))
        state.update({"outcome": outcome, "status": "completed"})
        self.save(stage, state)
        return state, items


def source_archive(base):
    return subprocess.check_output(["git", "archive", "--format=tar", base])


def acceptance_prompt(prd, plan, contract):
    return (
        "You are the independent acceptance author, before implementation exists. "
        "Inspect the pinned baseline and existing tests. Write one Playwright acceptance "
        "spec for the PRD using the exact UI contract below. Do not implement the feature, "
        "edit source, or execute generated tests. Their first execution belongs to the "
        "independent controller. Return ONLY JSON with schemaVersion=1, spec (a JavaScript "
        "source string), and cases (array of {title, acceptanceIds, purpose}). "
        "Write at least 10 test cases, each isolated by beforeEach reset, with its relevant "
        "[AC-N] tags in the title. Use only the @playwright/test import. No skip/fixme/only, "
        "retries, expected failures, arbitrary sleeps, source inspection, request/fetch, "
        "filesystem/process access or conditional early-return escape paths. Use actual "
        "visible UI assertions and explicit fixtures. Scope task title lookups to the "
        "visible board card or workload row; hidden panels may contain duplicate task text. "
        "For errors, read the input's aria-describedby ID and assert the associated error "
        "element, not a global text locator that also matches the live announcement. "
        "For view switching use [data-view='board'] and [data-view='workload'] "
        "locators. Never use fuzzy accessible-name matching for Board: it also "
        "matches onboarding, keyboard, and Reset demo board. Include an explicit "
        "selected-view-only visibility test checking both #board and #workload. "
        "Clearing aria-invalid may remove the attribute or set it false: assert that it "
        "is no longer true. Do not impose stricter semantics than the UI contract. "
        "Group repeated numeric invalid "
        "input cases within a case only if each is asserted. Every cases title must "
        "exactly match an actual test title. The controller pins the result before "
        "implementation; the implementer cannot modify it.\n"
        "PRD:\n" + prd + "\nPLANNED REQUIREMENTS:\n" +
        json.dumps(plan["requirements"]) + "\nUI CONTRACT:\n" + contract
    )


def validate_acceptance(value):
    if value.get("schemaVersion") != 1:
        raise ValueError("Invalid acceptance schema")
    spec, cases = value.get("spec"), value.get("cases")
    if not isinstance(spec, str) or not 1000 <= len(spec.encode()) <= 80_000:
        raise ValueError("Acceptance spec must be bounded, substantive source")
    if not isinstance(cases, list) or not 10 <= len(cases) <= 30:
        raise ValueError("Acceptance bundle needs 10-30 described cases")
    blocked = (r"\btest\s*\.\s*(skip|fixme|only|fail|slow|setTimeout)\b",
               r"\b(?:describe|test)\s*\.\s*configure\b", r"\bretries\s*:",
               r"\b(?:require|fetch|eval|Function)\s*\(", r"\bimport\s*\(",
               r"\bprocess\s*\.", r"\b(?:readFile|writeFile|execSync|spawn|waitForTimeout)\s*\(",
               r"\b(?:route|request)\s*\.", r"\bpage\s*\.\s*route\s*\(")
    if any(re.search(pattern, spec) for pattern in blocked):
        raise ValueError("Acceptance spec uses a forbidden execution or skip mechanism")
    if re.search(r"name\s*:\s*['\"]Board['\"]", spec):
        raise ValueError("Use the stable data-view hook for Board; fuzzy name matching is ambiguous")
    imports = re.findall(r"\bfrom\s*['\"]([^'\"]+)['\"]", spec)
    if imports != ["@playwright/test"]:
        raise ValueError("Acceptance spec may import only @playwright/test")
    titles = set()
    covered = set()
    for case in cases:
        title = case.get("title", "")
        refs = case.get("acceptanceIds", [])
        if (not isinstance(title, str) or len(title) > 250 or title in titles
                or not isinstance(refs, list) or not refs
                or not set(refs) <= {f"AC-{n}" for n in range(1, 8)}
                or any(f"[{ref}]" not in title for ref in refs)
                or not case.get("purpose")):
            raise ValueError("Invalid acceptance case identity")
        titles.add(title)
        covered.update(refs)
    if covered != {f"AC-{n}" for n in range(1, 8)}:
        raise ValueError("Acceptance bundle omits PRD criteria")
    return value
