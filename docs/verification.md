# Verification evidence

2026-10-07, account `135808937215`, `us-east-1`.

- GitHub App scoped authentication passed in Actions run `37634022412`.
- Integration build/unit/UI and 12 pipeline tests passed in Actions run `37656869308`
  at implementation commit `5abcf68b165ca5d0b4670476d729699774872afd`.
- CloudFront deployment and live desktop/mobile board checks passed in run
  `37656944864`, at implementation commit `5abcf68b165ca5d0b4670476d729699774872afd`.
- Both CloudFormation stacks are deployed. Runtime: `bma_playground_codex-TRcPbn7JNG`.
- cfn-lint, CloudFormation Guard, and reviewed change-set validations passed.
- GitHub OIDC trust was corrected to the repository's immutable subject, verified
  in GitHub settings and CloudTrail. No AWS keys are stored in GitHub.
- Twelve local pipeline/archive/SSE tests passed under Python 3.12.
- Real BMA command probe session: `sess_aeawsylevjo4m2vvyepb7wgwtvti2b4ehcgd6`.
  Initial turn `turn_4jmk2nqzgcl5fpv2xtuv` ran Node 24.14.0, Git 2.50.1,
  Python 3.12.13, and wrote `BMA_PLAYGROUND_PERSIST_20261007` with exit code 0.
- `StopRuntimeSession` returned HTTP 200 for
  `bma-agentcore-ef14241537034f4b9c76b6bb36e88303`. Follow-up turn
  `turn_7cectdxkcrci24xfg6am` read the original marker with exit code 0.
  CloudWatch confirms SIGTERM/exec-server shutdown in stream `035ea662-...`,
  then saved-state loading and attachment generation 2 in stream `227fd3c6-...`.
  The boot ID stayed the same; this proves process restart and persisted files,
  not a distinct new kernel boot.
- Private source/repair probe session `sess_aeawsyleqjpmm2qktyfc77keokkzyh5tcmoyc`
  produced the one-line status fix. Its generated patch passed 13 unit tests,
  build, and 3 independent browser repair checks (1 mobile drag skip) in a
  disposable copy. The probe exposed Git ownership handling on session storage;
  the helper was corrected to trust the exact per-event directory.
- Runtime version 2 image build passed: CodeBuild
  `bma-playground-runtime:e818cf2c-c078-4b6e-8426-4c716e24dc3e`.
- Fresh version 2 source/repair probe: `sess_aeawsylerz7mm2rhr4o3sa6tn5jpgaoya2kls`,
  turn `turn_qw4vlzruym5cy4peenju`. All ten commands returned exit code 0,
  including source preparation and patch export. The application-only patch
  passed 13 unit tests, build, and all 3 repair acceptance cases (1 expected skip).
- Actions OIDC -> repair caller role -> BMA -> Codex command passed in
  **Verify BMA Runtime** run `37657809438`. The workflow deleted its own smoke
  session afterward.
- Four GitHub Actions checks are required; automatic squash merge is enabled.
- Full GitHub issue -> PR -> automatic merge -> repair deployment has not been
  exercised. No repair issue or repair PR was opened. The live board deliberately
  retains the defect for the first user-triggered demonstration.
# Recorded AgentCore Browser verification — 2026-10-11

Implemented and exercised the new browser flow against historical broken source
`090e13fc68d58bf4daf9cdb2acb3006ca5f73a5a`. Production stayed repaired.

- Custom browser: `bma_playground_browser-5PiWuaeYxw`, recording enabled, READY.
- Runtime version 3; image digest
  `sha256:4e4cf3b2e81c24baf26123ead07147ba8e18685e658c45e8ef565ef99982a7bc`.
- Native ARM64 CodeBuild:
  `bma-playground-runtime:b6240fc9-fb36-4dfb-9bd4-2cd21a726015`, SUCCEEDED.
- BMA session: `sess_aeawsylekvimw2usw7p6m7m2xeyu42oz574py`.
- Repair turn: `turn_4cpy5c2hzzgzmr3bqrhw`.
- Verification turn: `turn_ddcvaz6volkijkix7ald`.
- Before browser: `01M4N34MYSCV5Q9TEN528YP0K6`, 55 frames, 14.4764 seconds.
  Codex selected Done and observed 33% / 2 of 6; reload remained broken.
- After browser: `01M4N36MAVYEYY1Z2WNDYM5MEM`, 121 frames, 24.5701 seconds.
  Codex verified menu completion, 50% / 3 of 6, refresh persistence, and drag
  completion after a reset.
- BMA durable MCP results were matched to the preview URL, browser session,
  capture digest, source digest, event, phase, and outcome for both recordings.
- The adapter produced MP4s, PNGs, a GIF, a JSON report, and a watch page:
  https://dvf0au2tge8qd.cloudfront.net/evidence/probe-browser-20261011a/a404d0417c04f287/index.html

This was a new Codex repair rehearsal, not a recording of issue #1's original
execution. It created no repair PR and did not merge or deploy application
changes. The new PR publisher embeds the evidence for subsequent issue repairs.
