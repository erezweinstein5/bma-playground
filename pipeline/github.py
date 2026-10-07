"""GitHub API calls used only in trusted Actions steps."""
import os
import requests


def api(method, path, body=None):
    with requests.request(method, "https://api.github.com" + path,
                          headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                                   "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28"},
                          json=body, timeout=30) as response:
        if not response.ok:
            raise RuntimeError(f"GitHub {method} {path}: {response.status_code} {response.text[:2000]}")
        return response.json() if response.content else None
