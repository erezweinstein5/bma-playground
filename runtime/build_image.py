"""Build natively on CodeBuild ARM64; never put registry credentials in logs."""
import base64
import os
import subprocess
import sys
import boto3
from botocore.config import Config


def main():
    ecr = boto3.Session(region_name=os.environ["AWS_REGION"]).client(
        "ecr", config=Config(retries={"mode": "standard", "max_attempts": 5}))
    auth = ecr.get_authorization_token()["authorizationData"][0]
    username, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    subprocess.run(["docker", "login", "--username", username, "--password-stdin",
                    auth["proxyEndpoint"]], input=password.encode(), check=True)
    image = f'{os.environ["ECR_URI"]}:{os.environ["IMAGE_TAG"]}'
    subprocess.run(["docker", "build", "--platform", "linux/arm64", "-t", image, "."], check=True)
    subprocess.run(["docker", "push", image], check=True)
    print(f"Published {image}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
