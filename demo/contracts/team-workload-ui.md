# Team Workload v1: observable UI contract

This contract names stable automation hooks for the approved product behavior.
It does not prescribe the implementation of derived totals or migration.

## Existing behavior

Keep the storage key `bma-playground.board.v1`. Its value is an array of the
six existing tasks. Preserve existing card IDs, titles, status controls,
`#search`, `#reset`, `#announcement`, progress selectors, and all board tests.
Initial statuses and task IDs remain unchanged. Reset returns the seeded data.
Reset also discards unsaved effort input and clears transient effort validation
errors. Visible effort inputs return to their seeded values, stale errors are
hidden or removed, `aria-invalid` is no longer true, and `aria-describedby` no
longer references stale errors. Unrelated help text may remain associated.
Persisted owners, efforts and statuses return to seed; the reset announcement
replaces any previous validation failure.

## Source boundary

`index.html` is protected and remains the original Board shell. It has no
Workload panel or Board/Workload view buttons. Create the required `#workload`
panel and view buttons from application code within `src/` before their first
use or render. Preserve the original `#board`, host controls, accessible names
and Board behavior when adding these elements. Acceptance observes the running
UI; it must not inject missing markup or require an edit to `index.html`.

## Feature hooks and behavior

- A button named `Workload` has `data-view="workload"` and a button named `Board`
  has `data-view="board"`. Only the selected view is visible; Board is the default.
  View selection may stay local to the current page, but task edits persist.
- `#workload` is the Workload panel. Each teammate has one section with
  `data-workload-owner="Maya"` or `"Erez"`, and an accessible name identifying
  that teammate. A descendant `[data-workload-load]` displays exact text
  `12 / 8 points` (or the current total). `[data-overload-warning]` is visible
  and contains `Over capacity` only when total > 8; otherwise hide or omit it.
- Active-task rows in the workload panel have `data-workload-task="TASK_ID"`.
  Done tasks do not appear in these active lists. Searching filters visible
  task rows but never changes the displayed team totals/warnings.
- Every visible active-task row has a native select
  `data-owner-for="TASK_ID"`, accessible name `Owner for TASK_TITLE`, and
  option values `Maya` and `Erez`. Selecting an owner immediately saves and
  updates both teammates' totals and the board card avatar title/initials.
  Retain keyboard focus on the changed owner control after rendering.
- Each row has an editable input `data-effort-for="TASK_ID"` with accessible
  name `Effort for TASK_TITLE`, and a button `data-save-effort-for="TASK_ID"`
  with accessible name `Save effort for TASK_TITLE`. Clicking Save or pressing
  Enter commits a valid whole number 1–8.
- Invalid values leave canonical task effort unchanged. Associate a visible
  error containing `whole number from 1 to 8` with the input using
  `aria-describedby`; set `aria-invalid="true"`. Announce the failure in
  `#announcement` and keep focus usable. A later valid save clears the error.
- `#announcement` remains a live region. Successful reassignment mentions
  the task title and new owner; successful effort edits mention the task.
- Existing board `select[data-status-for="TASK_ID"]` completes/reopens tasks.
  After a keyboard status edit, focus stays on that task's visible Board status
  select in its destination column, including a move to Done and reopening
  from Done. Board stays selected; focus must not move to a hidden Workload
  owner control or fall back to the page body. Workload reflects these status
  changes whenever opened.

## Seed and migration fixtures

Capacities: Maya=8, Erez=8. Effort defaults:
welcome=8, mobile=4, shortcuts=2, onboarding=2, tokens=3, repo=3.
Active seed totals: Maya=12, Erez=4.
Reassign mobile to Erez: Maya=8, Erez=8. No warning at exactly capacity.

Preserve each valid saved status and owner by task ID. Missing effort receives
the task's default. Invalid saved owner/effort/status fields use seeded defaults
for those fields, without trusting saved title, description, HTML, initials or
color. Malformed arrays, missing/duplicate task IDs, and wrong task counts keep
the existing full-reset behavior. Valid modern entries retain all three edits.

## Acceptance bundle requirements

Use Playwright with only `import { test, expect } from '@playwright/test'`.
Each case title contains its relevant `[AC-N]` tags. At least eight independent
cases must cover all seven criteria, including:

1. Seed loads/overload, accessible numeric text and two teammates.
2. Pointer reassignment from 12/8 + 4/8 to 8/8 + 8/8; check the board owner too.
3. Keyboard reassignment and retained focus/live announcement. Focus the native
   owner select, press `e` (native type-ahead for Erez), then Enter. Assert the
   saved owner, retained focus, announcement and updated loads. Do not assume
   ArrowDown commits an option identically on macOS and Linux. Do not replace
   the keyboard interaction with `selectOption`.
4. Completing and reopening the 4-point mobile task removes/restores its load.
5. Search matching another owner and search matching nothing leave totals intact.
6. Reload preserves owner, effort and status edits together.
7. Legacy array migration preserves a changed valid owner and status while
   filling missing effort; a saved spoofed title/initials is not rendered.
8. Invalid effort values 0, 9, 1.5 and empty cannot alter totals; validate input
   error association and subsequent successful recovery.
9. Valid effort endpoints 1 and 8, Done exclusion, and strictly-greater overload.
10. Reset returns the complete seeded board/workload state.
11. Keyboard Board status edits between active columns retain focus on the
    moved task's visible status select after each edit.
12. Keyboard completion and reopening retain that same Board status focus,
    with the correct column, progress, announcement and workload consequence.
13. Reset after a rejected effort edit clears the associated error state and
    restores seeded inputs and persisted fields, even after prior valid edits.

For the new Board focus cases, focus the status select once to begin a keyboard
interaction and use native type-ahead for the existing labels (`t` for To do,
`i` for In progress, `d` for Done), followed by Enter. Send these keys through
`page.keyboard` so a locator action cannot silently refocus a replacement
control after rendering. Assert the visible status select is focused before
any further click, focus call or view switch. Do not use `selectOption` or
programmatically dispatch changes to stand in for these keyboard actions.

Fixtures should use the app's seeded tasks captured from localStorage after
reset, then mutate explicit fields and reload. Isolate every test. Assert
visible outcomes as well as storage where needed. Do not use skips, retries,
expected failures, test.only, unconditional passing assertions, arbitrary waits,
source inspection, module imports, route mocking, network APIs, filesystem or
process access. The runner supplies baseURL, browser and server configuration.
Tests are run on desktop; existing regression suites also cover mobile.

## Selector and visual checks

Use `page.locator('[data-view="board"]')` and
`page.locator('[data-view="workload"]')` for view switching. Fuzzy accessible
name matching for Board is invalid because it also matches "onboarding",
"keyboard", and "Reset demo board". This is a test-targeting rule; do not
rename or hide valid product controls to accommodate a fuzzy selector.

Assert that only the selected panel is visually displayed. The presence of a
`hidden` attribute alone is insufficient: a CSS display rule can override it.
Check Board visible / Workload hidden initially, then the inverse after opening
Workload, and the original state after switching back.
