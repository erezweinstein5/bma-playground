"""Exercise the real repair adapter on historical source without publishing a PR."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    baseline = os.environ.get("REHEARSAL_BASE", "090e13fc68d58bf4daf9cdb2acb3006ca5f73a5a")
    if not re.fullmatch(r"[0-9a-f]{40}", baseline):
        raise ValueError("Rehearsal source must be a full repository commit SHA")
    subprocess.run(["git", "cat-file", "-e", baseline + "^{commit}"], check=True)
    event = "probe-browser-" + os.environ.get("GITHUB_RUN_ID", os.environ.get("REHEARSAL_ID", "local"))
    if not re.fullmatch(r"probe-[0-9a-z-]+", event):
        raise ValueError("Invalid rehearsal identity")
    directory = Path("artifacts") / event
    directory.mkdir(parents=True, exist_ok=True)
    assignment = {
        "repository": "erezweinstein5/bma-playground", "issueNumber": 0, "eventId": event,
        "baseCommit": baseline, "branch": "codex/rehearsal-" + event, "rehearsal": True,
        "title": "Rehearsal: task cannot move to Done",
        "request": "Start by DRAGGING Build the onboarding flow from In progress to the Done column. "
                   "It stays in the old column and progress remains 2 of 6 / 33%. "
                   "The before video must show this drag failing, not just the status menu. "
                   "Read the announcement and progress after release. "
                   "Repair the status menu and drag-and-drop so the task moves to Done, "
                   "progress becomes 3 of 6 / 50%, and the change survives refresh. "
                   "Start the after video with the identical drag, then verify refresh and the status menu. "
                   "Keep the remaining board interactions working.",
    }
    path = directory / "assignment.json"
    path.write_text(json.dumps(assignment, indent=2))
    subprocess.run([sys.executable, "pipeline/repair.py", str(path)], check=True)
    result = json.loads((directory / "result.json").read_text())
    report = result["browserEvidence"]["url"] + "/index.html"
    print("Recorded BMA repair rehearsal succeeded: " + report, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"## Recorded browser repair rehearsal\n\n[Watch the before/after evidence]({report}).\n\n"
                          f"Historical source: `{baseline}`. This run created no PR and changed no production application files.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
