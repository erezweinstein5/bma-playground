"""Bounded durable planning claims and restoration of published design bytes."""
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from botocore.exceptions import ClientError

from design_board import canonical_json, validate_plan
from feature_contract import validate_prd_requirements


CONFLICTS = {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}


def read_object(s3, bucket, key, limit):
    response = s3.get_object(Bucket=bucket, Key=key)
    with response["Body"] as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"Oversized planning artifact: {key}")
    return raw, response["ETag"]


def validate_state(state, event, prd_digest):
    if not isinstance(state, dict):
        raise ValueError("Planning checkpoint must be an object")
    version = state.get("schemaVersion")
    plan_version = state.get("planSchemaVersion", 1)
    if (type(version) is not int or version not in (1, 2)
            or type(plan_version) is not int or plan_version != version):
        raise ValueError("Unsupported planning checkpoint schema")
    if (state.get("eventId") != event or state.get("prdSha256") != prd_digest
            or not isinstance(state.get("baseCommit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", state["baseCommit"])):
        raise ValueError("Existing planning claim has a different identity or PRD; "
                         "use a new issue for changed requirements")
    if state.get("status") not in (
            None, "claiming", "creating_session", "submitting", "planned", "questions"):
        raise ValueError("Planning claim has an unsupported or failed status; inspect the original run")
    if version == 2 and (state.get("status") is None or not re.fullmatch(
            r"[0-9a-f]{64}", str(state.get("sourceArchiveSha256", "")))):
        raise ValueError("Incomplete planning checkpoint identity; inspect the original run")
    for name in ("sessionId", "turnId"):
        if name in state and (not isinstance(state[name], str)
                              or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", state[name])):
            raise ValueError(f"Invalid planning {name}")
    if "assignment" in state and not isinstance(state["assignment"], dict):
        raise ValueError("Planning claim has a malformed original assignment")
    if state.get("status") in {"planned", "questions"} and (
            not state.get("sessionId") or not state.get("turnId")
            or not re.fullmatch(r"[0-9a-f]{64}", str(state.get("planSha256", "")))):
        raise ValueError("Completed planning checkpoint is missing its pinned result")
    return state


class PlanningClaim:
    def __init__(self, s3, bucket, event):
        self.s3, self.bucket = s3, bucket
        self.key = f"state/features/{event}/planning.json"
        self.etag = None

    def read(self):
        try:
            raw, self.etag = read_object(self.s3, self.bucket, self.key, 100_000)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                return None
            raise
        return json.loads(raw)

    def write(self, state, *, create=False):
        if not create and not self.etag:
            raise RuntimeError("No durable planning claim to update; inspect the original run")
        condition = {"IfNoneMatch": "*"} if create else {"IfMatch": self.etag}
        response = self.s3.put_object(
            Bucket=self.bucket, Key=self.key, Body=json.dumps(state).encode(),
            ContentType="application/json", **condition)
        self.etag = response["ETag"]


def restore_design(s3, state, plan, prd_bytes, directory):
    """Restore a published proposal exactly; a newer renderer must not repin it."""
    parsed = urlsplit(state["designUrl"])
    origin = os.environ["BMA_PREVIEW_ORIGIN"].rstrip("/")
    prefix = (f'evidence/{state["eventId"]}/design-'
              f'{state["planSha256"][:16]}-')
    if (parsed.scheme != "https" or parsed.netloc != urlsplit(origin).netloc
            or origin != f"https://{parsed.netloc}" or parsed.query or parsed.fragment
            or not re.fullmatch(re.escape("/" + prefix) + r"[0-9a-f]{12}/index.html", parsed.path)):
        raise ValueError("Published design URL differs from the pinned origin or identity")
    key_prefix = parsed.path.lstrip("/").rsplit("/", 1)[0]
    bodies = {}
    for name, limit in (("plan.json", 100_000), ("provenance.json", 100_000),
                        ("prd.md", 32_000), ("index.html", 2_000_000)):
        bodies[name], _ = read_object(s3, os.environ["BMA_EVIDENCE_BUCKET"],
                                     f"{key_prefix}/{name}", limit)
    restored = validate_plan(json.loads(bodies["plan.json"]))
    validate_prd_requirements(restored, prd_bytes.decode())
    expected_provenance = {key: value for key, value in state.items() if key != "designUrl"}
    if (restored != plan or bodies["plan.json"] != canonical_json(plan)
            or hashlib.sha256(bodies["plan.json"]).hexdigest() != state["planSha256"]
            or bodies["prd.md"] != prd_bytes
            or json.loads(bodies["provenance.json"]) != expected_provenance
            or hashlib.sha256(bodies["index.html"]).hexdigest()[:12] != key_prefix[-12:]):
        raise ValueError("Published design differs from pinned planning provenance or hashes")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in bodies.items():
        (directory / name).write_bytes(raw)


def report_result(status, state=None, message=""):
    """Stable Actions interface; skipped claims never advertise readiness."""
    ready = status in {"planned", "restored"} and state["status"] == "planned"
    fields = {"status": status, "ready": str(ready).lower()}
    if state:
        fields["base_commit"] = state["baseCommit"]
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("".join(f"{key}={value}\n" for key, value in fields.items()))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
            output.write(f"## BMA feature planning: {status}\n\n{message}\n\n")
            if state:
                output.write(f"Event: `{state['eventId']}`. Original source: "
                             f"`{state['baseCommit']}`. Planning state: `{state.get('status', 'legacy')}`.\n\n")
    print(message, flush=True)
