# Issue-to-deployment integration

```mermaid
sequenceDiagram
  actor Presenter
  participant GH as GitHub / Actions
  participant S3 as Private artifact bucket
  participant BMA
  participant Runtime as AgentCore Runtime
  participant App as CloudFront board
  Presenter->>GH: agent-fix issue or /codex comment
  GH->>S3: Claim event; pin and upload source
  GH->>BMA: Create/reuse issue session; submit request
  BMA->>Runtime: Codex commands inspect, edit, test
  Runtime->>S3: Export source patch and manifest
  GH->>S3: Read patch and validate base/digest/scope
  GH->>GH: App opens PR; required checks; auto-merge
  GH->>App: Publish exact merged revision
  GH->>App: Verify revision and browser acceptance
  GH->>GH: Close issue after successful live verification
```

## Roles and trust boundaries

- The Actions repair role can create/read BMA sessions, pass only the BMA session
  role, and access the specific source/patch/state prefixes.
- The BMA session role can call the selected model and invoke/stop this Runtime.
- The Runtime role can pull its ECR image, connect Codex to BMA, read `sources/`,
  and write `repairs/`. It cannot read Actions state or access GitHub credentials.
- The Actions deployment role can publish only the web bucket and invalidate
  only this CloudFront distribution. Both OIDC roles trust this repository's
  `main` ref and the STS audience.

Source is `git archive` at the captured SHA, without local credentials or `.git`.
The image-owned helper safely extracts it into a new per-event repository.
The adapter treats the exported manifest and patch as untrusted: it checks
identity, base SHA, digest, and size, applies the patch in an isolated clone, and
accepts only regular non-executable files below `src/`. Workflows, dependencies,
checks, symlinks, submodules, binary patches, and other paths are rejected.

The GitHub App opens a PR only after that boundary passes. The independent
`Repair scope` and `Repair acceptance` jobs load their policy from the PR base.
Required checks are `Pipeline safety`, `Repair scope`, `Build, unit, and board
checks`, and `Repair acceptance`. Auto-merge is enabled only by the trusted
publisher after it has validated a BMA result. PRs use `Refs #N`, so merge alone
does not close an issue.

## State and failure handling

S3 stores `state/issues/<number>.json` and `state/events/<event>.json`.
Conditional creation claims each event once. Duplicate completed triggers are
no-ops. An ambiguous or interrupted mutation is recorded as `needs_inspection`;
it is not automatically submitted again. Inspect the Actions transcript, BMA
session/items, and source/patch objects before posting a new `/codex` request.
No automatic model repair retries are currently configured.

The adapter opens SSE before submitting input, waits for a new successful
terminal turn event, and saves durable output items. Acknowledgement or `idle`
alone is insufficient. A missing patch, failed stream, failed check, changed
main SHA, or invalid scope stops publication. If deployment or live acceptance
fails, the issue stays open; there is no automatic rollback yet.

Deployment uploads immutable assets first, HTML last, and a no-cache revision
record, then waits for CloudFront invalidation. Live checks use the deployed URL.
Older assets are retained so cached pages continue to work. Superseded pending
main revisions are skipped; deployment jobs are serialized.

## Session persistence

BMA conversation state is mapped to the issue. AgentCore managed session storage
holds `/mnt/home`, including Codex home, connection state, repository directories,
and an uncommitted `session-marker.txt`. Follow-ups stage current main in a new
per-event repository while retaining that marker and the BMA conversation.
Session storage expires after 14 idle days and resets across Runtime versions;
it is not indefinite storage. Runtime-stop/restart persistence must be shown by
an actual marker probe, separately from same-process follow-up success.

## References

- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-bma.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-api-reference.html
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-filesystem-configurations.html
