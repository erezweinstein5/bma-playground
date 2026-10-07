# Setup and live wiring

Repository: `erezweinstein5/bma-playground`.

AWS target: local profile `default`, account `135808937215`, Region `us-east-1`.
Verified with STS on 2026-10-07. GitHub variables `AWS_REGION`, `AWS_ACCOUNT_ID`,
and `DEMO_OPERATORS` are configured. The `agent-fix` label exists. Cloud
provisioning is still pending. There are no credentials in this repo.

## Required setup values

| Value | Purpose |
| --- | --- |
| AWS profile/account and Region | Deployment target; must support BMA preview |
| `DEMO_OPERATORS` | Comma-separated GitHub logins allowed to assign work; defaults to repository owner in the workflow |
| `AWS_REGION` | BMA regional endpoint and Runtime Region |
| `BMA_MODEL` | A BMA-supported OpenAI model available to this account |
| `BMA_RUNTIME_ARN` | Runtime running `codex exec-server` |
| `BMA_SESSION_ROLE_ARN` | Role BMA assumes for inference and Runtime operations |
| `BMA_WORKSPACE` | Defaults to `/mnt/workspace/bma-playground` |
| `GH_APP_CLIENT_ID` | Actions variable identifying the installed `bma-playground` GitHub App |
| `GH_APP_PRIVATE_KEY` | Actions secret holding the App signing key |
| Hosting target | Private S3 bucket behind CloudFront |

BMA configuration and the GitHub App Client ID are Actions **variables**. The
App private key is stored only in the `GH_APP_PRIVATE_KEY` Actions secret.
AWS access will use Actions OIDC.

The `agent-fix` label and GitHub App installation are configured. Install these
workflows on `main` before the first real issue. Auto-merge is currently disabled; enable
it only after required checks and the source-path boundary are installed.

## GitHub App authentication

The `bma-playground` App (App ID `5224686`, installation `168887498`) is installed
only on `erezweinstein5/bma-playground`. It has contents, issues, and pull-request
read/write permissions plus mandatory metadata read access. The webhook is off;
GitHub Actions receives issue events.

`GH_APP_CLIENT_ID` and `GH_APP_PRIVATE_KEY` are configured in Actions. A direct
installation-token check succeeded on 2026-10-07 and confirmed exactly one
repository. The temporary verification token was revoked immediately.

Run the **Verify GitHub App** workflow on `main` to verify authentication from
an Actions runner. It uses the official token action pinned to its reviewed
commit, requests the three write permissions explicitly, checks repository
scope, and revokes its token after the job. It does not change repository content.

## Local event preparation

The event adapter takes an actual GitHub webhook JSON file. No credentials or
network are needed:

```sh
GITHUB_EVENT_NAME=issues \
GITHUB_EVENT_PATH=/absolute/path/issue-event.json \
DEMO_OPERATORS=erezweinstein5 \
SOURCE_COMMIT="$(git rev-parse HEAD)" \
npm run prepare:repair
```

Outputs go to ignored `artifacts/<event-id>/`. The assignment includes the
issue/event identity, actor, request, starting SHA, and deterministic branch
name. When all BMA variables are supplied, `bma-requests.json` contains the
documented session-creation and input-event payloads.

This is preparation only. Session creation is a separate operation. Follow-up
assignments must retrieve and reuse an existing session instead of sending the
generated create-session payload again.

## Presenter rehearsal

1. Open the board and use **Reset demo board**.
2. Move **Build the onboarding flow** to **Done**; show that it stays put.
3. Open a repair issue with reproduction steps and expected behavior.
4. Once live wiring is complete: follow the BMA run, PR, checks, merge, and deployment.
5. Refresh the deployed app, reset its local board data, and repeat the move.
6. After a verified repair, demonstrate persistence with a Runtime restart and
   a `/codex` follow-up using the same BMA session.

The reset button restores **task data only**. To rehearse the broken code again,
redeploy the commit titled `Bootstrap BMA Playground demo and GitHub workflows`.
The following command locates that baseline once the initial commit is published:

```sh
git log --format=%H --grep="^Bootstrap BMA Playground demo and GitHub workflows$" -1
```
