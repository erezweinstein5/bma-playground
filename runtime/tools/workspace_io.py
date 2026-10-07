"""Transfer source and patches. This file is image-owned, outside the workspace."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import boto3
from botocore.config import Config

ROOT = Path("/mnt/home/workspace")
LIMIT = 5 * 1024 * 1024


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def unpack(data, target):
    """Source archives contain regular files/directories only, with bounded size."""
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > 30 * 1024 * 1024:
            raise ValueError("Source archive exceeds size limit")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
                raise ValueError("Unsafe source path")
            if not (member.isfile() or member.isdir()):
                raise ValueError("Source symlinks and special files are not accepted")
        archive.extractall(target, members=members, filter="data")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "export"])
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--sha256", help="Required source archive digest for prepare")
    args = parser.parse_args()
    if not re.fullmatch(r"(issue|comment|probe)-[0-9a-z-]+", args.event):
        raise ValueError("Invalid event identity")
    if not re.fullmatch(r"[0-9a-f]{40}", args.base):
        raise ValueError("Invalid base SHA")
    repo = ROOT / "jobs" / args.event / "repo"
    client = boto3.Session(region_name=os.environ.get("AWS_REGION", "us-east-1")).client(
        "s3", config=Config(retries={"mode": "standard", "max_attempts": 5}))
    if args.action == "prepare":
        if repo.exists():
            raise ValueError("Workspace already exists; do not overwrite an earlier attempt")
        response = client.get_object(Bucket=args.bucket, Key=f"sources/{args.event}.tar")
        with response["Body"] as body:
            data = body.read(LIMIT + 1)
        if len(data) > LIMIT or hashlib.sha256(data).hexdigest() != args.sha256:
            raise ValueError("Source archive digest or size mismatch")
        repo.mkdir(parents=True)
        unpack(data, repo)
        git(repo, "init", "--initial-branch=main")
        git(repo, "config", "user.name", "BMA Playground")
        git(repo, "config", "user.email", "bma-playground@users.noreply.github.com")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "Pinned input source " + args.base)
        # This marker intentionally stays outside Git to demonstrate session storage.
        marker = ROOT / "session-marker.txt"
        if not marker.exists():
            marker.write_text(f"First assignment: {args.event}\n")
        print(json.dumps({"repository": str(repo), "base": args.base,
                          "persistenceMarker": marker.read_text().strip()}))
    else:
        git(repo, "add", "-N", "--", "src")
        patch = git(repo, "diff", "--binary", "HEAD", "--", "src")
        if not patch or len(patch) > LIMIT:
            raise ValueError("Expected a nonempty, bounded application patch")
        digest = hashlib.sha256(patch).hexdigest()
        key = f"repairs/{args.event}/repair.patch"
        client.put_object(Bucket=args.bucket, Key=key, Body=patch, ContentType="text/plain")
        manifest = {"schemaVersion": 1, "eventId": args.event, "baseCommit": args.base,
                    "patchKey": key, "patchSha256": digest,
                    "persistenceMarker": (ROOT / "session-marker.txt").read_text().strip()}
        client.put_object(Bucket=args.bucket, Key=f"repairs/{args.event}/manifest.json",
                          Body=json.dumps(manifest).encode(), ContentType="application/json")
        print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
