# Verification evidence

2026-10-07, account `135808937215`, `us-east-1`.

- GitHub App scoped authentication passed in Actions run `37634022412`.
- Integration build/unit/UI checks passed in Actions run `37640353217`.
- CloudFront deployment and live desktop/mobile board checks passed in run
  `37640468460`, attempt 2, at commit `bcbee31fbd9baafc6f51dc8ceeb1becec56f157d`.
- Both CloudFormation stacks are deployed. Runtime: `bma_playground_codex-TRcPbn7JNG`.
- cfn-lint, CloudFormation Guard, and reviewed change-set validations passed.
- GitHub OIDC trust was corrected to the repository's immutable subject, verified
  in GitHub settings and CloudTrail. No AWS keys are stored in GitHub.
- Twelve local pipeline/archive/SSE tests passed.
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
  the Runtime helper correction is being rebuilt and will be reverified.
- Four GitHub Actions checks are required; automatic squash merge is enabled.
- Full GitHub issue -> PR -> automatic merge -> repair deployment has not been
  exercised. No repair issue or repair PR was opened. The live board deliberately
  retains the defect for the first user-triggered demonstration.
