# BMA Playground

Demos and experiments with Amazon Bedrock Managed Agents, the OpenAI Codex
harness, and Amazon Bedrock AgentCore.

The first demo follows a visible application repair:

**GitHub issue → BMA / Codex → PR → checks → automatic merge → deploy.**

## Run locally

Use Node 24, then:

```sh
npm ci
npx playwright install chromium
npm run dev
```

Open the local URL printed by Vite. The app is a responsive task board, with
drag and drop, accessible status menus, search, completion progress, and local
storage. It has no external fonts, services, or image requests.

## The demo defect

Reset the board, then move **Build the onboarding flow** from **In progress**
to **Done**. It stays in its old column and progress stays at **2 of 6 / 33%**.
The repair should make the move work, update progress to **3 of 6 / 50%**, and
preserve that change after refreshing. The status menu must work too.

```sh
npm test
npm run build
npm run test:ui
npm run test:repair
```

The first three commands verify the baseline. **`test:repair` deliberately
fails on the broken baseline.** It is a real regression test, not a simulated
failure. All PRs must pass this acceptance check once the workflow is installed
on `main`; the initial baseline is committed directly to `main` during setup.
The PR check loads its acceptance test and browser configuration from the base
revision. Repairs are restricted to application files.

## Current implementation

| Component | Status |
| --- | --- |
| Task board and reproducible defect | Implemented |
| Unit tests, browser checks, and repair acceptance test | Implemented |
| GitHub issue form and event normalization | Implemented |
| BMA session/input request construction | Implemented; not submitted to AWS |
| GitHub App installation and Actions credentials | Configured; repository-scoped token verified |
| AgentCore Runtime and persistent workspace | Target verified: default profile, us-east-1; provisioning pending |
| Source transfer, durable issue/session mapping, PR publication | Pending |
| Required branch checks, auto-merge, deployment, live smoke | Pending |

The `Prepare BMA repair` workflow handles labeled issues and `/codex` follow-up
comments from configured operators. It uploads an assignment artifact and,
when configured, BMA request payloads. **It does not invoke BMA or create a PR
yet.** A prepared assignment is not a successful repair.

The manually triggered **Verify GitHub App** workflow checks App authentication
and repository scope from an Actions runner.

## Next implementation checkpoint

Provision the Runtime and storage in the selected AWS account/Region, prove a
real BMA session can edit a file, and connect one issue to one PR. See
[docs/setup.md](docs/setup.md) for required inputs and [docs/pipeline.md](docs/pipeline.md)
for the remaining integration.
