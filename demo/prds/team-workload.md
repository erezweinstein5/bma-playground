# Help launch leads balance team workload

## Problem and outcome

Our board shows delivery progress, but a launch lead cannot see when one person
has too much active work. Add a Workload view where I can see capacity, reassign
tasks, and immediately understand the effect on the team and the existing board.

## Product rules

- Use the existing teammates, Maya and Erez, with capacity 8 points each.
- Seed effort by task ID: welcome=8, mobile=4, shortcuts=2, onboarding=2,
  tokens=3, repo=3. The initial active totals are Maya 12/8 and Erez 4/8.
- Moving the 4-point "Give mobile some love" task to Erez balances both at 8/8.
- Sum only To do and In progress effort. Done contributes zero. At capacity is
  not overloaded; strictly above capacity is. Overload is a warning, not a block.
- Let people edit effort as a whole number from 1 to 8. Reject invalid input
  with an accessible explanation and preserve the previous valid value.
- Search filters visible cards without changing workload totals or warnings.
- Persist valid owners, effort, and status across reload. Migrate existing saved
  boards without losing valid saved statuses or owners. Fill missing effort
  from the task defaults. Document fallback behavior for invalid stored fields.
- Support pointer and keyboard interaction. Changes must be announced to
  assistive technology. Preserve the board's visual language and behavior.

## Acceptance criteria

- **AC-1:** Workload view shows both teammates, capacity, numeric active load,
  and a visually clear overload state.
- **AC-2:** Active effort excludes Done tasks. Exactly 8/8 is not overloaded.
- **AC-3:** Reassigning a task updates both teammates and the board immediately.
  Reassignment works with pointer and keyboard.
- **AC-4:** Completing and reopening a task removes and restores its effort.
- **AC-5:** Searching never changes canonical workload totals or warnings.
- **AC-6:** Owner, effort, and status persist after reload. Migration preserves
  valid existing owner/status choices and supplies missing effort defaults.
- **AC-7:** Invalid effort is rejected with an accessible error. Existing board,
  reset, search, drag, status menu, and progress behavior remain working.

## Visual planning deliverable

Before implementation, present the proposed experience on a design canvas:
brief, user flow, wireframe, polished overloaded view, balanced view after
reassignment, compact/mobile frame, and validation/keyboard notes. Annotate
frames with acceptance IDs. This is a design proposal, not a working product
or a claim of passing acceptance.

## Non-goals

No backend, authentication, cross-device sync, teammate administration, dates,
automatic scheduling, new dependencies, or changes to CI/infrastructure.
The feature is bounded to this demo board.
