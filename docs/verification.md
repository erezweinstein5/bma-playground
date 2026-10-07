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
