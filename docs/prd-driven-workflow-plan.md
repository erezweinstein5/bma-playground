# PRD-driven feature workflow implementation

The first feature pipeline encoded Team Workload business rules in the controller.
Replace those rules with a versioned, PRD-derived contract while preserving the
existing source-only boundary, immutable artifacts, separate BMA sessions, exact
candidate checks, and ordinary required-check auto-merge.

## Interfaces

- Existing plan schema 1 and `team-workload-v1` publications remain readable.
- New intake emits assignment schema 2, kind `prd-feature-v1`. It accepts a bounded
  prose PRD; numbered ACs are optional. Existing authorization and pinning remain.
- New plans use schema 2, variable AC-N requirements and relevant screen kinds,
  plus `uiContract` (bounded Markdown of observable UI hooks/behavior) and
  `browserJourney` (ordered browser steps). Explicit PRD AC IDs must be preserved;
  otherwise planning derives numbered criteria from the prose.
- Every journey step is an object with `action`, `description`, `acceptanceIds`.
  Actions: click(selector), fill/select(selector,value), drag(selector,target),
  reload(), assert_text(selector,expected). No URLs, scripts, arbitrary tools or
  navigation are generated. Runtime still controls the preview origin and open.
- Parent-owned `feature_contract.py` provides `requirement_ids(plan)`,
  `validate_journey(journey, acceptance_ids)`, `validate_prd_requirements(plan, prd)`,
  `plan_contract(plan)` and `plan_journey(plan)`. Legacy plans use the saved
  Workload contract/journey. New plans must carry their own contract and journey.
- Acceptance schema 2 includes `acceptanceIds` (all plan IDs), `cases`, `spec`.
  Its persisted manifest also includes `browserJourney` and its canonical SHA256.
  Each manifest case must have an exact executable title and cover only known IDs;
  all plan IDs must be covered. Old v1 limits/checks remain unchanged.
- For compatibility the on-disk `workload.acceptance.spec.js` transport filename
  can remain; it does not choose business rules. Generic policies are selected
  only by validated schema/manifest, never inferred from a filename.
- `validate_acceptance(value, required_ids=None)` validates either schema.
  `validate_review(..., required_ids=None)` uses the pinned IDs when supplied.
- `check_candidate(..., required_ids=None)` and report checking use the pinned IDs
  when provided; the full exact frozen suite must pass once without skips/retries.
- Browser validation accepts a pinned journey for new plans; legacy no-argument
  calls retain their exact Workload checks. Browser report includes the journey
  and canonical SHA256, which publication/CI bind to the frozen manifest/plan.
- New markers remain metadata schema 2 with featureKind `prd-feature-v1` and
  `browserJourneySha256`, `acceptanceManifestSha256`, `browserReportSha256` and
  the full criterion IDs; legacy metadata continues unchanged. Never permit
  schema downgrades or a manifest's criteria/journey to diverge from the plan.

## Validation

Cover multiple unrelated PRDs with different criterion counts and browser journeys,
wrong-feature evidence rejection, missing criteria, legacy compatibility and immutable
identity failures. Run the pipeline tests and existing application tests/build/UI checks.
Use an isolated local/new-feature fixture for validation; the user will create and
trigger the real teammate-filter issue manually. Do not implement that feature here.
