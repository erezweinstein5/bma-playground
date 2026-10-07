"""Publish assets first and HTML last, then verify the CloudFront revision."""
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
import boto3
from botocore.config import Config
import requests


def main():
    bucket, distribution = os.environ["WEB_BUCKET"], os.environ["DISTRIBUTION_ID"]
    sha = os.environ["DEPLOY_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Deploy a full commit SHA")
    session = boto3.Session(region_name=os.environ["AWS_REGION"])
    config = Config(retries={"mode": "standard", "max_attempts": 5})
    s3, cloudfront = session.client("s3", config=config), session.client("cloudfront", config=config)
    for path in sorted(Path("dist").rglob("*")):
        if not path.is_file() or path.name == "index.html":
            continue
        key = path.relative_to("dist").as_posix()
        s3.upload_file(str(path), bucket, key, ExtraArgs={
            "ContentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "CacheControl": "public,max-age=31536000,immutable" if key.startswith("assets/") else "no-cache",
        })
    s3.upload_file("dist/index.html", bucket, "index.html",
                   ExtraArgs={"ContentType": "text/html", "CacheControl": "no-cache,no-store"})
    s3.put_object(Bucket=bucket, Key="revision.json", Body=json.dumps({"commit": sha}).encode(),
                  ContentType="application/json", CacheControl="no-cache,no-store")
    invalidation = cloudfront.create_invalidation(DistributionId=distribution, InvalidationBatch={
        "CallerReference": f'{sha}-{os.environ.get("GITHUB_RUN_ID", "local")}-{os.environ.get("GITHUB_RUN_ATTEMPT", "1")}',
        "Paths": {"Quantity": 3, "Items": ["/", "/index.html", "/revision.json"]},
    })["Invalidation"]["Id"]
    cloudfront.get_waiter("invalidation_completed").wait(
        DistributionId=distribution, Id=invalidation,
        WaiterConfig={"Delay": 10, "MaxAttempts": 60})
    url = os.environ["APP_URL"].rstrip("/") + "/revision.json"
    for attempt in range(12):
        with requests.get(url, timeout=20, headers={"Cache-Control": "no-cache"}) as response:
            if response.ok and response.json().get("commit") == sha:
                print(f'Published and verified {sha} at {os.environ["APP_URL"]}', flush=True)
                return 0
        time.sleep(5)
    raise RuntimeError("CloudFront did not serve the deployed revision")


if __name__ == "__main__":
    sys.exit(main())
