# Image-packaged design and review skills

The runtime image contains two versioned plugins. Each has a
`.codex-plugin/plugin.json` manifest and `skills/<name>/SKILL.md`.

| Role | Skill | Runtime discovery directory |
| --- | --- | --- |
| Planning/product design | `bma-design` | `/opt/bma/plugins/bma-design/skills` |
| Independent candidate review | `bma-review` | `/opt/bma/plugins/bma-review/skills` |

`pipeline/bma_skills.py` supplies the role-specific `capability_directories`
and explicit skill-use instructions. The planner and reviewer receive only
that role's directory. Other pipeline stages retain their previous configuration.
PRDs, schemas, source identities, permitted scope and deterministic gates remain
controller inputs. Pre-skill review checkpoints can collect an existing result
only if every original input hash matches; collection never submits a new turn.

Skills are copied into the container by `runtime/Dockerfile`, so new sessions
get the same packaged files without installing anything on session storage.
Rebuild and deploy the image before controller changes require a new skill.

## Verified deployment — 2026-10-19

- CodeBuild `bma-playground-runtime:bfe9842a-110a-4b91-a261-6150a9357407` succeeded.
- The reviewed CloudFormation change set modified only the runtime image, with
  no resource replacement. The stack reached `UPDATE_COMPLETE`; Runtime
  version `6` reached `READY`.
- Image digest: `sha256:6b411421bd90d6b0713b4e2051b39c3bf41e5b8fbc97d936c7d2e64f9990050d`.
- GitHub's `BMA_MODEL`, the foundation stack parameter, and both live probe
  session records agree on `openai.gpt-6.1-sol`.
- Both disposable probes discovered their assigned skill and executed commands
  that read its complete contents and SHA-256. The hashes matched the source
  files below. Both probe sessions were deleted afterward.

| Skill | Session | Turn | SKILL.md SHA-256 |
| --- | --- | --- | --- |
| `bma-design` | `sess_aeawsyledhbnk2spoxlv23ymvj7otoog6p2tq` | `turn_3h7u2njyqdlaxxnc24ki` | `b85bef29d8ec9f69b32d0cc2f353765d085639a8bfa8a16f51b2140cbc69b404` |
| `bma-review` | `sess_aeawsyledhbnk2sov3sbzdh6fxxjsmksdxoaw` | `turn_qiebk3566fwjon3ajd6m` | `01d46979feee4a811f0cbf102aa03a315d1756e8afe8c73bff21f6fc5944bb0c` |

Validation also passed 231 pipeline tests, 17 JavaScript unit tests, the
production build and both SKILL.md validators. The live probes establish
skill discovery and exact image contents; they do not claim a new end-to-end
feature delivery cycle.
