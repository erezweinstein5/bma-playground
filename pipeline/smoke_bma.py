"""Verify the real Actions OIDC caller can create and execute a BMA session."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote
import uuid
import boto3
from bma_client import BmaClient, PREFIX


def main():
    region = os.environ["AWS_REGION"]
    client = BmaClient(boto3.Session(region_name=region), region)
    directory = Path("artifacts/bma-smoke")
    directory.mkdir(parents=True, exist_ok=True)
    session = client.create({
        "agent": {"model": os.environ["BMA_MODEL"],
                  "instructions": "Use commands for the requested smoke test. Never read credentials."},
        "environment": {"type": "aws_bedrock_agentcore", "runtime_arn": os.environ["BMA_RUNTIME_ARN"],
                        "runtime_qualifier": "DEFAULT", "workspace_directory": "/mnt/home/workspace"},
        "role_arn": os.environ["BMA_SESSION_ROLE_ARN"],
    })
    session_id = session["id"]
    (directory / "session.json").write_text(json.dumps({"sessionId": session_id}))
    marker = "BMA_SMOKE_" + uuid.uuid4().hex
    try:
        result = client.run_turn(session_id,
            f"Run exactly: printf '%s\\n' '{marker}' > smoke.txt && cat smoke.txt && node --version && git --version. "
            "Report the actual output. No other work is needed.",
            directory / "events.jsonl")
        commands = [item for item in client.items(session_id)
                    if item.get("type") == "command_execution" and item.get("turn_id") == result["turnId"]]
        if not commands or not any(marker in (item.get("output") or "") and item.get("exit_code") == 0
                                   for item in commands):
            raise RuntimeError("Turn completed without a successful command containing the fresh marker")
        result["sessionId"] = session_id
        (directory / "result.json").write_text(json.dumps(result, indent=2))
        print("Verified Actions OIDC -> BMA -> Codex -> Runtime command.", flush=True)
    finally:
        # This script owns only this newly created, disposable smoke session.
        with client.request("DELETE", PREFIX + "/" + quote(session_id, safe="")):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
