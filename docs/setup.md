# Setup and rehearsal

Repository: `erezweinstein5/bma-playground`.
AWS: profile `default`, account `135808937215`, Region `us-east-1`.
Hosting: https://dvf0au2tge8qd.cloudfront.net

## Actions configuration

| Variable | Purpose |
| --- | --- |
| `DEMO_OPERATORS` | Explicit comma-separated operator allowlist |
| `AWS_ACCOUNT_ID`, `AWS_REGION` | Expected AWS target |
| `AWS_REPAIR_ROLE_ARN` | OIDC role for session/source/patch operations |
| `AWS_DEPLOY_ROLE_ARN` | OIDC role for website publishing |
| `BMA_MODEL` | `openai.gpt-5.6-luna` |
| `BMA_RUNTIME_ARN`, `BMA_SESSION_ROLE_ARN` | Execution environment and BMA role |
| `BMA_ARTIFACT_BUCKET` | Private source, patch, and durable state bucket |
| `WEB_BUCKET`, `DISTRIBUTION_ID`, `APP_URL` | Hosting outputs |
| `GH_APP_CLIENT_ID` | Installed GitHub App identity |

The only Actions secret is `GH_APP_PRIVATE_KEY`. AWS access uses OIDC; neither
AWS access keys nor OpenAI keys are stored in GitHub. Nonsecret infrastructure
outputs are recorded in `infra/deployed.json`.

The `bma-playground` App (ID `5224686`, installation `168887498`) is installed
only on this repository. Its webhook is disabled because Actions receives issue
events. App tokens are minted only in trusted publication/issue-closing steps
and revoked by the token action afterward.

## Presenter rehearsal

1. Open the live board and select **Reset demo board**.
2. Move **Build the onboarding flow** from **In progress** to **Done**. On the
   baseline, it stays put and progress stays at 33%.
3. Open the repository's repair issue form, describing the failed move and the
   expected 50% progress. The form applies `agent-fix`.
4. Follow **BMA repair**, its PR, **Checks**, automatic merge, and **Deploy board**.
5. Once deployment passes, reload the board, reset its data, and repeat the move.
6. Add `/codex <next application request>` on that issue to continue the same
   BMA session. Closed issues also accept follow-up comments.

The baseline SHA is `270bf5061cf9c6c61fe153cc4cedc1438c2d73f6`.
The reset button restores task data only. For another broken-code rehearsal,
restore `src/board.js` from that commit in an explicitly authorized baseline
reset; this intentionally bypasses the repair acceptance contract and must not
be disguised as a successful repair. Do not roll back the integration files.

## Infrastructure

`infra/generate_foundation.py` generates `infra/foundation.json`: hosting,
artifact storage, IAM/OIDC, encrypted logs, and the native ARM64 CodeBuild project.
`runtime/` is the image build context. Its `buildspec.yml` publishes an immutable
ECR tag. `infra/runtime.json` creates the Runtime from the image URI, execution
role, and log key outputs. Validate with cfn-lint and `infra/security.guard`, then
review a CloudFormation change set and its validation events before execution.

Storage buckets, the ECR repository, and log resources are retained on stack
deletion. Cleanup therefore requires an explicit inventory and deletion of
retained resources. Never remove the shared `CDKToolkit` stack for this demo.
