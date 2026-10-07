"""Publish an already validated patch, then request check-gated auto-merge."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from github import api
from patch_boundary import canonical_patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    result = json.loads(args.result.read_text())
    repo, branch, base = result["repository"], result["branch"], result["baseCommit"]
    if repo != os.environ["GITHUB_REPOSITORY"] or not re.fullmatch(r"codex/issue-\d+-(issue|comment)-\d+", branch):
        raise ValueError("Unexpected repository or branch")
    # Validate again at the publication boundary, before adding GitHub authentication.
    patch = canonical_patch(Path.cwd(), base, args.result.with_name("verified.patch").read_bytes())
    if api("GET", f"/repos/{repo}/git/ref/heads/main")["object"]["sha"] != base:
        raise RuntimeError("Main changed during the repair; submit a new /codex request from current main")
    subprocess.run(["git", "checkout", "-b", branch, base], check=True)
    subprocess.run(["git", "apply", "--index", "-"], input=patch, check=True)
    subprocess.run(["git", "-c", "user.name=bma-playground[bot]",
                    "-c", "user.email=bma-playground[bot]@users.noreply.github.com",
                    "commit", "-m", f'Repair application for issue #{result["issueNumber"]}'], check=True)
    token = base64.b64encode(("x-access-token:" + os.environ["GH_TOKEN"]).encode()).decode()
    environment = {**os.environ, "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                   "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + token}
    subprocess.run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], env=environment, check=True)
    metadata = {"issue": result["issueNumber"], "session": result["sessionId"],
                "event": result["eventId"], "base": base}
    body = (
        f'Refs #{result["issueNumber"]}.\n\n'
        "BMA repaired the assigned application behavior using the OpenAI Codex harness "
        "and AgentCore Runtime. The trusted adapter validated that the patch changes only "
        "regular files under `src/`.\n\n"
        "Build, unit, browser, and independent repair acceptance checks must pass before "
        "automatic merge. Deployment verifies the live revision and browser behavior "
        "before closing the issue.\n\n"
        f'BMA session: `{result["sessionId"]}`\n'
        f'Turn: `{result["turnId"]}`\n'
        f'<!-- bma-playground:{json.dumps(metadata, separators=(",", ":"))} -->'
    )
    pr = api("POST", f"/repos/{repo}/pulls", {
        "title": f'Repair application for issue #{result["issueNumber"]}',
        "head": branch, "base": "main", "body": body})
    result["pullRequest"] = pr["html_url"]
    args.result.write_text(json.dumps(result, indent=2))
    response = api("POST", "/graphql", {
        "query": "mutation($id:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$id,mergeMethod:SQUASH}){pullRequest{number autoMergeRequest{enabledAt}}}}",
        "variables": {"id": pr["node_id"]}})
    if response.get("errors"):
        raise RuntimeError("PR created, but auto-merge was not enabled: " + json.dumps(response["errors"]))
    print(pr["html_url"], flush=True)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(f'BMA session `{result["sessionId"]}` created [PR #{pr["number"]}]({pr["html_url"]}). '
                      "Auto-merge is waiting for the required checks.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
