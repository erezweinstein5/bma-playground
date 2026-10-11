# Recorded browser verification

Each repair uses two real AgentCore Browser sessions controlled by Codex through
an image-owned STDIO MCP server. Browser automation runs remotely; Chromium is
not installed in the Codex Runtime.

1. Actions pins the source, builds a before preview, and writes its URL and source
   digest to a private descriptor under `sources/`.
2. Codex opens that preview, chooses real browser interactions from observed page
   controls, records a visible-text assertion, and finishes with an honest outcome.
   A failure that cannot be reproduced stops the repair.
3. Codex changes application source and exports its patch. Actions independently
   validates the patch boundary before building the after preview.
4. A second Codex turn repeats the interactions on the after preview, including
   refresh persistence where appropriate. It cannot change the already-built
   preview by editing its local working copy.
5. Actions verifies preview identities, capture hashes, observed outcomes, media
   limits, and the presence of browser actions and assertions. It encodes MP4
   recordings and a shorter GIF and publishes the presentation media.
6. The GitHub App creates the PR with embedded screenshots/GIF, video download
   links, a watch page, and a JSON report. The PR identifies the tested base,
   patch SHA-256, resulting commit, BMA turns, and browser sessions.
7. Existing independent checks still control automatic merge and deployment.

## Watching live

Open the AgentCore console in `us-east-1`, choose **Built-in tools**, and select
the browser ID from `infra/deployed.json`. During a repair, open its active
`<event>-before` or `<event>-after` session and choose **View live session**.
The browser is stopped after capture, and also has a 15-minute timeout.

The private `repairs/<event>/browser/<phase>/live.json` object identifies the live
session as soon as it starts. BMA MCP results also contain the session ID.
No signed live-view URLs or AWS credentials are published in GitHub.

## Media and retention

- The Browser service writes native replay data to the private artifact bucket
  under `browser-recordings/`.
- The Runtime writes raw capture frames, a screenshot, and an action manifest
  under `repairs/<event>/browser/<phase>/` in that same private bucket.
- Actions alone publishes preview builds and presentation media to the separate
  evidence bucket. CloudFront serves `/previews/*` and `/evidence/*` from it.
  The Runtime has no permission to publish to this bucket or to production.
- PR media URLs are stable and have no signed-URL expiry. Current S3 objects remain
  until explicitly removed. GitHub's supplemental run artifacts expire in 7 days.
- The GIF shows before then after, sped up to at most 12 seconds per phase. Full
  MP4s preserve capture timing. Both phases carry an on-screen label.

Only the public demo application appears in the presentation media. Browser
tools navigate only to the exact trusted preview and restrict network requests
to its build prefix. They expose no arbitrary JavaScript execution.

Browser observations and screenshots are agent-produced evidence, not an
independent acceptance oracle. A successful agent assertion cannot override a
failed required CI check.

## Rehearsing without breaking production

Run **Rehearse recorded browser repair** manually in Actions. It uses the known
historical broken revision, a fresh BMA session, separate preview paths, and the
same repair adapter. It publishes an evidence page but creates no repair PR,
merges nothing, and does not deploy the historical application.

This is a new recorded agent run against historical source, not footage of the
original issue repair.

When an existing issue is first upgraded to browser tools, the adapter creates a
new BMA session and preserves the previous session ID in durable state. Tool
configuration is fixed at session creation; older sessions do not acquire tools
automatically. Runtime version upgrades also reset managed session storage.
