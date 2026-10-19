---
name: bma-review
description: Independently review an assigned feature candidate against its PRD, visual plan and frozen acceptance evidence in the BMA review stage.
---

# BMA review

Review the exact prepared candidate and evidence supplied by the controller.
The archive already contains the candidate changes; its baseline is recorded
in preparation metadata. Read source with file and line references. This is
a read-only review: do not edit or export code, install dependencies, access
external systems, read credentials or perform GitHub actions.

## Assess the candidate

Compare the implementation with the PRD, visual design and UI contract.
Investigate missing criteria, regressions, applicable data migration,
derived-state errors, accessible validation and keyboard focus.
Check actual visibility against the CSS cascade. Distinguish intentional
PRD changes from unintended regressions.

Use independent acceptance results as evidence, not as automatic approval.
The acceptance bundle is independently versioned outside the application patch;
do not demand that protected tests be added to that patch. The controller owns
test execution and dependency setup.

Separate product defects from test or pipeline failures. An infrastructure
failure is a pipeline limitation, not a reason to alter application code or
replace the acceptance bundle. A strict-mode locator error alone does not prove
a product defect: distinguish ambiguous substring matching from genuinely
duplicate exact accessible names. Do not recommend hiding or renaming valid
controls to accommodate an ambiguous test.

## Report the decision

Identify real actionable defects; never manufacture a finding to create a demo
correction. For each finding give a source location when available, relevant
acceptance IDs, concrete evidence, impact and a specific requested change.
Use the assignment's severity and JSON schema. A blocker or major finding
requires request_changes; use an empty findings array if no actionable defect
was found. Report unavailable evidence as a limitation and do not describe
proposed or untested behavior as observed browser success.

Echo the exact candidate and acceptance hashes supplied by the controller.
Finish inspection with git status --porcelain to confirm that the snapshot
is unchanged, then return only the structured review.
