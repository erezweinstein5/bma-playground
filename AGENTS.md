# BMA Playground

This repository contains a small task board and a GitHub-to-BMA demo integration.

- Install with `npm ci`; use the Node version in `.node-version`.
- Validate with `npm test`, `npm run build`, and `npm run test:ui`.
- `npm run test:repair` is the acceptance contract for the initial repair issue.
  It is expected to fail on the intentionally broken baseline. Fix application
  behavior when assigned that issue; do not skip or weaken the acceptance tests.
- Application code lives in `src/`. Keep assigned application repairs within
  `src/`; do not change workflows, dependencies, infrastructure, or credentials.
- Never claim a BMA session, PR, merge, or deployment happened without evidence.
- Reset board data with the visible "Reset demo board" button.
- AI-DLC is outside this demo's scope.
