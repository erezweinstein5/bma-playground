# PRD-driven feature delivery

The feature workflow derives its requirements, visual proposal, observable UI
contract, independent acceptance suite and recorded browser journey from the
issue PRD. Team Workload was the first feature; its seven criteria and five
screen states apply only to legacy schema 1 evidence.

## Try a new PRD manually

1. Create an issue with a feature title and the PRD in its body. Describe the
   user problem, expected behavior, acceptance criteria and non-goals.
   Numbered `AC-N` IDs are optional: the planner preserves explicit IDs or
   derives them from prose. The combined title/body must be 100–32000 UTF-8 bytes.
2. Apply `agent-feature` when ready. Only configured `DEMO_OPERATORS` can trigger
   delivery. One issue identity represents one frozen run.
3. Open **Actions → BMA feature delivery → the run → Summary**. Once planning
   completes, **Open the design proposal** leads to generated screens,
   requirements, implementation tasks, assumptions, questions and the proposed
   browser journey. The planning step also prints the design URL in its logs.
4. The proposal is a static HTML/CSS design board, published at an immutable
   evidence URL. It is not a new application environment. Implementation
   continues automatically when there are no material questions; viewing the
   design is not a required human approval step.
5. Follow the separately named acceptance, implementation/review, browser and
   publication steps. The resulting PR contains design and evidence links.
   Required checks rerun the frozen feature suite on the exact PR revision.
6. Successful required checks allow ordinary auto-merge and deployment. The
   deployment gate verifies the served revision and reruns the suite on the
   live app before closing the issue.

The user supplies product requirements, not selectors or test code. Those are
derived during planning and acceptance authoring. Do not add the repair label:
`agent-feature` selects this workflow.

## What “any PRD” means

Business rules are no longer baked into the controller. New plans can have
different criterion IDs, counts, screen types, UI hooks and browser actions.
The current authorized implementation scope remains the existing front-end
application under `src/`, with its installed dependencies and browser storage.
A requirement for a new backend, dependency, authentication system, or
infrastructure changes needs an explicit scope extension. The planner must
surface that need as a material question rather than invent a UI-only substitute.

The independent Playwright suite covers every planned criterion, including
keyboard behavior and persistence where requested. The shorter recorded native
browser journey illustrates the primary visual scenario; it may cover a subset
of criteria. Supported recording actions are click, fill, select, drag, reload
and visible-text assertions. The controller supplies the preview URL and
validates the exact ordered tool calls and observed results.

## Evidence and execution boundaries

- The trusted main revision, original issue text and authorized actor are pinned
  before BMA starts. PRD text is data, never shell code or permission to change
  the controller.
- Separate BMA sessions perform planning, acceptance authoring, implementation,
  review and browser verification. Acceptance is frozen before implementation.
- New schema 2 plans carry their own `uiContract` and `browserJourney`. The
  acceptance manifest pins the complete criteria, exact case titles, journey
  and content hashes. Candidate, browser evidence and PR metadata must match.
- The controller runs every frozen case with no skips, retries or expected
  failures. Existing regression suites also remain gates. At most four
  corrections return to the original implementation session.
- Generated design frames run in opaque-origin sandboxed iframes with scripts
  and network access disabled. The viewer labels them as proposals.
- Browser verification uses a candidate preview built from the pinned source
  and approved patch. This is separate from the earlier static design board.
- Source-only patch validation, trusted-base CI, exact-revision deployment
  checks and ordinary branch protection remain in force.

Existing schema 1 Workload evidence remains supported without being rewritten.
The historical `workload.acceptance.spec.js` filename is retained as a transport
path; validated schema and frozen metadata select the business contract.

## Interrupted runs and planning questions

Private S3 checkpoints prevent blind resubmission. A duplicate label event does
not create another run for an existing issue. Editing an issue does not mutate
the frozen PRD or automatically resume planning. After a material question,
clarify the PRD and create a new issue for a fresh run, or have the operator
inspect and explicitly reconcile the existing run. Reconciliation retrieves
the original result; it does not apply edited requirements.

For a planning-only operator probe, use the existing repository environment
variables (`AWS_REGION`, `BMA_MODEL`, artifact/evidence bucket names, runtime and
session-role ARNs, and `BMA_PREVIEW_ORIGIN`):

```sh
python pipeline/plan_feature.py --prd PATH_TO_PRD \
  --base FULL_SOURCE_SHA --event probe-feature-plan-unique-id --publish
```

Repeated submission is refused. Use `--reconcile` only to collect the existing
session after inspecting its state. Preserve failed attempts and exact model,
plan and source identities. A plan or authored test suite alone is not proof of
implementation, passing tests, or deployment.
