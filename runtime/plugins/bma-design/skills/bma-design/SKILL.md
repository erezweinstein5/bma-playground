---
name: bma-design
description: Turn an assigned PRD and pinned application source into a visual feature proposal and implementation plan for the BMA planning stage.
---

# BMA design

Read the prepared repository's AGENTS.md, application structure, styles, persisted
data and tests before designing. Produce a proposal grounded in the existing
product. This stage does not implement code, install dependencies, run a browser
or publish artifacts. The controller supplies the PRD, preparation command,
output schema and permitted scope for this assignment.

## Product decisions

Preserve the PRD's explicit acceptance IDs and meaning. When the PRD uses prose,
derive independently testable criteria in prose order. Make assumptions explicit
without introducing unrelated requirements. Link implementation decisions,
screens and verification scenarios to those criteria.

Choose screens from the feature's real flow: entry, results, empty, detail,
validation or mobile states when they help explain the proposed experience.
Match the application's visual language and use source-grounded fixture data.
Show the product itself, not placeholder prose or another design-tool shell.
Each frame must fit its declared dimensions without scrolling. Describe the
meaningful before/after change so a PM can understand the proposal visually.

Address persistence, migration, derived data, accessibility and focus when the
PRD requires them. Keep existing controls and behavior unless the feature
requires a change. Distinguish a static design proposal from a working feature.

## Handoff

Keep stable selectors, accessible labels, fixtures and expected visible results
consistent between proposed markup, the UI contract and the browser journey.
The native browser journey demonstrates an honest primary visual scenario;
independent acceptance tests cover every criterion, including scenarios beyond
the recorder's supported actions. Do not weaken a criterion to fit the recording.

Record material product ambiguities and required changes outside the assignment's
scope as questions naming the affected criteria. Do not invent a UI-only
substitute for a required backend capability. Follow the controller's schema for
blocked proposals rather than fabricating executable actions.

Return only the assigned structured proposal. Do not claim implementation,
passing tests, browser verification, PR creation or deployment.
