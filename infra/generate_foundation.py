"""Generate the small, independently deployable BMA Playground foundation stack."""
import json
from pathlib import Path


def ref(name):
    return {"Ref": name}


def sub(value):
    return {"Fn::Sub": value}


def arn(name):
    return {"Fn::GetAtt": [name, "Arn"]}


def allow(actions, resources, **extra):
    return {"Effect": "Allow", "Action": actions, "Resource": resources, **extra}


def policy(statements):
    return {"Version": "2012-10-17", "Statement": statements}


def role(trust, statements):
    return {"Type": "AWS::IAM::Role", "Properties": {
        "AssumeRolePolicyDocument": policy(trust),
        "Policies": [{"PolicyName": "DemoPermissions", "PolicyDocument": policy(statements)}],
    }}


def service_trust(service, source):
    return [{
        "Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": ref("AWS::AccountId")},
                      "ArnLike": {"aws:SourceArn": sub(source)}},
    }]


def template():
    resources = {}
    account_region = "${AWS::Region}:${AWS::AccountId}"
    runtime_arn = f"arn:${{AWS::Partition}}:bedrock-agentcore:{account_region}:runtime/bma_playground_codex-*"
    runtime_endpoints = runtime_arn + "/runtime-endpoint/*"
    log_prefix = f"arn:${{AWS::Partition}}:logs:{account_region}:log-group:/aws/bedrock-agentcore/runtimes/bma_playground_codex*"
    for name in ("WebBucket", "ArtifactBucket"):
        resources[name] = {
            "Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain",
            "Properties": {
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True, "BlockPublicPolicy": True,
                    "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
                },
                "BucketEncryption": {"ServerSideEncryptionConfiguration": [
                    {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]},
                "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
                "VersioningConfiguration": {"Status": "Enabled"},
                "LifecycleConfiguration": {"Rules": [{
                    "Id": "ExpireOldVersions", "Status": "Enabled",
                    "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                }]},
            },
        }
    resources["OriginAccessControl"] = {"Type": "AWS::CloudFront::OriginAccessControl", "Properties": {
        "OriginAccessControlConfig": {"Name": sub("${AWS::StackName}-web"),
            "OriginAccessControlOriginType": "s3", "SigningBehavior": "always", "SigningProtocol": "sigv4"},
    }}
    resources["WebDistribution"] = {"Type": "AWS::CloudFront::Distribution", "Properties": {
        "DistributionConfig": {
            "Enabled": True, "Comment": "BMA Playground task board", "DefaultRootObject": "index.html",
            "HttpVersion": "http2and3", "IPV6Enabled": True, "PriceClass": "PriceClass_100",
            "Origins": [{"Id": "web", "DomainName": {"Fn::GetAtt": ["WebBucket", "RegionalDomainName"]},
                "OriginAccessControlId": ref("OriginAccessControl"), "S3OriginConfig": {"OriginAccessIdentity": ""}}],
            "DefaultCacheBehavior": {
                "TargetOriginId": "web", "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": ["GET", "HEAD"], "CachedMethods": ["GET", "HEAD"], "Compress": True,
                "ForwardedValues": {"QueryString": False, "Cookies": {"Forward": "none"}},
                "MinTTL": 0, "DefaultTTL": 0, "MaxTTL": 31536000,
                "ResponseHeadersPolicyId": "67f7725c-6f97-4210-82d7-5512b31e9d03",
            },
            "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
        },
    }}
    for name in ("WebBucket", "ArtifactBucket"):
        statements = [{"Effect": "Deny", "Principal": "*", "Action": "s3:*",
            "Resource": [arn(name), sub("${" + name + ".Arn}/*")],
            "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]
        if name == "WebBucket":
            statements.append({
                "Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"},
                "Action": "s3:GetObject", "Resource": sub("${WebBucket.Arn}/*"),
                "Condition": {"StringEquals": {"AWS:SourceArn": sub(
                    "arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${WebDistribution}")}},
            })
        resources[name + "Policy"] = {"Type": "AWS::S3::BucketPolicy",
            "Properties": {"Bucket": ref(name), "PolicyDocument": policy(statements)}}
    resources["RuntimeRepository"] = {"Type": "AWS::ECR::Repository",
        "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
            "RepositoryName": sub("${AWS::StackName}-runtime"), "ImageTagMutability": "IMMUTABLE",
            "ImageScanningConfiguration": {"ScanOnPush": True},
            "EncryptionConfiguration": {"EncryptionType": "AES256"},
        }}
    resources["LogKey"] = {"Type": "AWS::KMS::Key", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain",
        "Properties": {"Description": "BMA Playground log encryption", "EnableKeyRotation": True,
            "KeyPolicy": policy([
                allow("kms:*", "*", Principal={"AWS": sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:root")}),
                allow(["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"],
                    "*", Principal={"Service": sub("logs.${AWS::Region}.amazonaws.com")},
                    Condition={"ArnLike": {"kms:EncryptionContext:aws:logs:arn": [
                        sub(log_prefix),
                        sub(f"arn:${{AWS::Partition}}:logs:{account_region}:log-group:/aws/codebuild/${{AWS::StackName}}-runtime"),
                    ]}}),
            ])}}
    resources["BuildLogs"] = {"Type": "AWS::Logs::LogGroup",
        "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
            "LogGroupName": sub("/aws/codebuild/${AWS::StackName}-runtime"),
            "RetentionInDays": 14, "KmsKeyId": arn("LogKey"),
        }}
    resources["GitHubOidc"] = {"Type": "AWS::IAM::OIDCProvider", "Properties": {
        "Url": "https://token.actions.githubusercontent.com", "ClientIdList": ["sts.amazonaws.com"],
    }}
    github_trust = [{
        "Effect": "Allow", "Principal": {"Federated": ref("GitHubOidc")},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {"StringEquals": {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
            "token.actions.githubusercontent.com:sub": ref("GitHubOidcSubject"),
        }},
    }]
    resources["DeployRole"] = role(github_trust, [
        allow("s3:ListBucket", arn("WebBucket")),
        allow(["s3:GetObject", "s3:PutObject"], sub("${WebBucket.Arn}/*")),
        allow(["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
            sub("arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${WebDistribution}")),
    ])
    resources["RuntimeRole"] = role(service_trust("bedrock-agentcore.amazonaws.com",
        f"arn:${{AWS::Partition}}:bedrock-agentcore:{account_region}:*"), [
        allow(["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], arn("RuntimeRepository")),
        allow("ecr:GetAuthorizationToken", "*"),
        allow(["logs:CreateLogStream", "logs:PutLogEvents"], sub(log_prefix + ":*")),
        allow(["logs:DescribeLogStreams", "logs:CreateLogGroup"], sub(log_prefix)),
        allow(["bedrock-mantle:RegisterEnvironment", "bedrock-mantle:ConnectEnvironment"],
            "arn:aws:bedrock-mantle:*:*:project/*"),
        allow("s3:GetObject", sub("${ArtifactBucket.Arn}/sources/*")),
        allow("s3:PutObject", sub("${ArtifactBucket.Arn}/repairs/*")),
    ])
    resources["SessionRole"] = role(service_trust("bedrock-mantle.amazonaws.com",
        "arn:${AWS::Partition}:bedrock-mantle:*:${AWS::AccountId}:*"), [
        allow("bedrock-mantle:CreateInference", "*",
            Condition={"StringEquals": {"bedrock-mantle:Model": ref("BmaModel")}}),
        allow(["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:StopRuntimeSession"],
            [sub(runtime_arn), sub(runtime_endpoints)]),
    ])
    resources["RepairRole"] = role(github_trust, [
        allow(["bedrock-mantle:CreateAgentSession", "bedrock-mantle:GetAgentSession",
               "bedrock-mantle:ListAgentSessions", "bedrock-mantle:CreateAgentSessionEvent",
               "bedrock-mantle:ListAgentSessionEvents", "bedrock-mantle:ListAgentSessionItems",
               "bedrock-mantle:DeleteAgentSession"], "arn:aws:bedrock-mantle:*:*:project/*"),
        allow("iam:PassRole", arn("SessionRole"),
            Condition={"StringEquals": {"iam:PassedToService": "bedrock-mantle.amazonaws.com"}}),
        allow(["s3:GetObject", "s3:PutObject"], [
            sub("${ArtifactBucket.Arn}/sources/*"), sub("${ArtifactBucket.Arn}/repairs/*"),
            sub("${ArtifactBucket.Arn}/state/*"),
        ]),
        allow("s3:ListBucket", arn("ArtifactBucket"),
            Condition={"StringLike": {"s3:prefix": ["state/*", "repairs/*"]}}),
    ])
    # AWS's BMA preview documentation defines these actions, but cfn-lint's
    # service-authorization catalog does not yet list them. Keep this exception
    # scoped to the two roles containing those preview actions.
    for name in ("RuntimeRole", "RepairRole"):
        resources[name]["Metadata"] = {"cfn-lint": {"config": {"ignore_checks": ["W3037"]}}}
    resources["BuildRole"] = role(service_trust("codebuild.amazonaws.com",
        f"arn:${{AWS::Partition}}:codebuild:{account_region}:project/${{AWS::StackName}}-runtime"), [
        allow("s3:GetObject", sub("${ArtifactBucket.Arn}/builds/*")),
        allow("s3:GetBucketLocation", arn("ArtifactBucket")),
        allow(["logs:CreateLogStream", "logs:PutLogEvents"], arn("BuildLogs")),
        allow("ecr:GetAuthorizationToken", "*"),
        allow(["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
               "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
               "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], arn("RuntimeRepository")),
    ])
    resources["RuntimeBuild"] = {"Type": "AWS::CodeBuild::Project", "Properties": {
        "Name": sub("${AWS::StackName}-runtime"), "ServiceRole": arn("BuildRole"),
        "TimeoutInMinutes": 30, "QueuedTimeoutInMinutes": 10,
        "Artifacts": {"Type": "NO_ARTIFACTS"},
        "Source": {"Type": "S3", "Location": sub("${ArtifactBucket}/builds/runtime.zip"), "BuildSpec": "buildspec.yml"},
        "Environment": {"Type": "ARM_CONTAINER", "ComputeType": "BUILD_GENERAL1_SMALL",
            "Image": "aws/codebuild/amazonlinux-aarch64-standard:4.0-26.05.06", "PrivilegedMode": True,
            "EnvironmentVariables": [
                {"Name": "ECR_URI", "Value": {"Fn::GetAtt": ["RuntimeRepository", "RepositoryUri"]}},
                {"Name": "IMAGE_TAG", "Value": "initial"},
            ]},
        "LogsConfig": {"CloudWatchLogs": {"Status": "ENABLED", "GroupName": ref("BuildLogs")}},
    }}
    outputs = {
        "WebBucket": (ref("WebBucket"), "Private website bucket"),
        "ArtifactBucket": (ref("ArtifactBucket"), "Private build, source, patch, and state bucket"),
        "DistributionId": (ref("WebDistribution"), "CloudFront distribution ID"),
        "AppUrl": (sub("https://${WebDistribution.DomainName}"), "Public HTTPS board URL"),
        "DeployRoleArn": (arn("DeployRole"), "GitHub Actions website deployment role"),
        "RepairRoleArn": (arn("RepairRole"), "GitHub Actions BMA integration role"),
        "SessionRoleArn": (arn("SessionRole"), "Role assumed by BMA for inference and Runtime activation"),
        "RuntimeRoleArn": (arn("RuntimeRole"), "AgentCore execution role"),
        "RuntimeRepositoryUri": ({"Fn::GetAtt": ["RuntimeRepository", "RepositoryUri"]}, "Container image repository"),
        "BuildProject": (ref("RuntimeBuild"), "ARM64 Runtime container build"),
        "LogKeyArn": (arn("LogKey"), "Runtime and build log encryption key"),
    }
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "BMA Playground: private static hosting, GitHub OIDC roles, and Runtime build/storage foundation.",
        "Parameters": {
            "GitHubOidcSubject": {"Type": "String",
                "Default": "repo:erezweinstein5@125476602/bma-playground@1408690708:ref:refs/heads/main",
                "AllowedPattern": "repo:[A-Za-z0-9@_./:-]+:ref:refs/heads/main"},
            "BmaModel": {"Type": "String", "Default": "openai.gpt-5.6-luna",
                "AllowedPattern": "openai\\.[A-Za-z0-9.-]+"},
        },
        "Resources": resources,
        "Outputs": {key: {"Value": value, "Description": description} for key, (value, description) in outputs.items()},
    }


if __name__ == "__main__":
    destination = Path(__file__).with_name("foundation.json")
    destination.write_text(json.dumps(template(), indent=2) + "\n")
    print(destination)
