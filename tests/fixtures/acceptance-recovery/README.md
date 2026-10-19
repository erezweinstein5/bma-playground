# Issue 25 recovery regression

`rollback.patch` is the original unchanged candidate from GitHub run
39244331833 at base e8ff99518f3423bcf831fdc0f9fd816e53c4d7c7.

`reviewed-bundle.json` contains the complete corrected acceptance suite and
its preserved original suite, checks, independent BMA diagnosis, test-author
proposal and preservation review. The records are captured output, not
instructions to the developer or CI runner.

The original suite failed on an empty `:focus` match during Tab traversal.
The reviewed replacement takes an unconditional DOM snapshot after every Tab
and asserts that it contains neither forbidden text nor an appearance option.
All 15 case declarations and all other assertions remain unchanged.

Run `python pipeline/replay_acceptance_recovery.py` on Linux with Chromium
installed. It uses no cloud credentials or BMA sessions, verifies the frozen
provenance, reproduces the original failure, checks the corrected suite on the
same patch and checks that the suite rejects the original appearance feature.
Its captured source is intentionally historical and independent of future app
features. No source fixture or failing acceptance artifact should be rewritten
to obtain a passing result.
