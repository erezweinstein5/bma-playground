"""Identify a merged BMA change and close its issue after live verification."""
import json
import os
from pathlib import Path
import re
import sys
from github import api


def main():
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["DEPLOY_SHA"]
    path = Path("artifacts/deployment.json")
    if sys.argv[1] == "identify":
        pulls = api("GET", f"/repos/{repo}/commits/{sha}/pulls")
        candidates = [pr for pr in pulls if pr.get("merged_at") and pr["merge_commit_sha"] == sha
                      and pr["base"]["ref"] == "main"
                      and pr["user"]["login"] == "bma-playground[bot]"
                      and pr["head"]["ref"].startswith("codex/issue-")]
        metadata = None
        if candidates:
            if len(candidates) != 1:
                raise ValueError("Ambiguous repair PR")
            match = re.search(r"<!-- bma-playground:(\{[^\n]*\}) -->", candidates[0].get("body") or "")
            if not match:
                raise ValueError("Repair PR lacks trusted pipeline metadata")
            metadata = json.loads(match[1])
            if not isinstance(metadata.get("issue"), int) or metadata["issue"] <= 0:
                raise ValueError("Invalid repair issue")
            metadata["pr"] = candidates[0]["number"]
            if metadata.get("kind") == "feature":
                from feature_delivery import pr_context
                if pr_context(candidates[0], repo) is None:
                    raise ValueError("Missing feature PR identity")
                metadata.update(repository=repo, deploySha=sha, pullRequest=candidates[0])
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(metadata))
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"repair={'true' if metadata else 'false'}\n")
            output.write(f"kind={metadata.get('kind', 'repair') if metadata else 'none'}\n")
            if metadata and metadata.get("kind") == "feature":
                output.write(f"base={metadata['base']}\n")
    else:
        metadata = json.loads(path.read_text())
        if not metadata:
            return 0
        feature = metadata.get("kind") == "feature"
        if feature:
            verified = json.loads(Path("artifacts/feature-delivery-live/result.json").read_text())
            if (verified.get("passed") is not True or verified.get("mode") != "live"
                    or verified.get("revision") != sha
                    or verified.get("eventId") != metadata.get("event")
                    or verified.get("patchSha256") != metadata.get("patchSha256")):
                raise ValueError("Feature issue cannot close without matching live acceptance")
        issue = api("GET", f'/repos/{repo}/issues/{metadata["issue"]}')
        if issue["state"] != "closed":
            api("POST", f'/repos/{repo}/issues/{metadata["issue"]}/comments', {
                "body": f'{"Feature" if feature else "Repair"} PR #{metadata["pr"]} passed its required checks and was deployed.\n\n'
                        f'Live {"feature" if feature else "browser"} acceptance passed at {os.environ["APP_URL"]}.\n'
                        f'Verified revision: `{sha}`.\nBMA session: `{metadata["session"]}`.'})
            api("PATCH", f'/repos/{repo}/issues/{metadata["issue"]}', {"state": "closed", "state_reason": "completed"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
