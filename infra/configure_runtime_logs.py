"""Configure the log group AgentCore creates automatically during deployment."""
import argparse
import sys
import boto3
from botocore.config import Config


def outputs(client, stack):
    return {x["OutputKey"]: x["OutputValue"] for x in
            client.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    config = Config(retries={"mode": "standard", "max_attempts": 5})
    cloudformation = session.client("cloudformation", config=config)
    foundation = outputs(cloudformation, "bma-playground")
    runtime = outputs(cloudformation, "bma-playground-runtime")
    logs = session.client("logs", config=config)
    logs.put_retention_policy(logGroupName=runtime["RuntimeLogGroup"], retentionInDays=14)
    logs.associate_kms_key(logGroupName=runtime["RuntimeLogGroup"], kmsKeyId=foundation["LogKeyArn"])
    print("Configured encryption and 14-day retention:", runtime["RuntimeLogGroup"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
