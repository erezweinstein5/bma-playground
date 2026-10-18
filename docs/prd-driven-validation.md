# PRD-driven workflow validation — 2026-10-18

## Real BMA planning and independent acceptance authoring

A planning-only probe used the existing default-profile environment and
`openai.gpt-6.1-sol` against main revision
`3465d1f142169cca0e8c1d575517ca4ca9550947`.

- Event: `probe-prd-generic-20261018a`
- PRD: Comfortable or compact cards, supplied as prose without AC IDs.
- Result: schema 2, eight derived criteria, three relevant screens, a 12-step
  browser journey, and no material planning questions.
- Planner session: `sess_aeawsyleuml5k2rfwxmagblxfjc2fctzvnneg`
- Planner turn: `turn_o7o26roqpemqmh527plc`
- Plan SHA-256: `1a89e89ee6ca66f727b271650da72ddfaecfd6ddb1ef7d7163612eb87ee89dac`
- [Generated design proposal](https://dvf0au2tge8qd.cloudfront.net/evidence/probe-prd-generic-20261018a/design-1a89e89ee6ca66f7-4c150cbd900b/index.html)

A separate BMA session authored fifteen acceptance cases covering all eight
criteria, including keyboard use. The controller froze the spec, its case
manifest, the plan-derived UI contract and browser-journey hash.

- Acceptance session: `sess_aeawsylelamnk2sd7floffozdf4mfanbowwoi`
- Acceptance turn: `turn_56xn2q2yuc4loymzwzhq`
- Spec SHA-256: `095776d3be7cca57c8706c5cd63beffb9fbe94ab95363f564b267b5d1a550858`
- Journey SHA-256: `f674406696634d1cb491cdcfaac0ae42615163c8711c01b386bb09f7cd63b06b`
- Status: `authored-not-executed`.

The first collection identified a controller false positive: passive
`page.on('request', request => request.url())` observation was rejected by a
rule intended to prohibit direct HTTP requests. Schema 2 now permits a bounded
set of passive Request metadata methods while preserving the direct-request
and interception bans. The recorded output was collected from the same
completed session without resubmitting input or altering the authored spec.

The card-density feature was not implemented. Its fifteen tests were not run
against a candidate. No product issue, feature PR, merge or deployment was
created by this probe.

## Real generic check-runner probe

A separate disposable checkout used a comment-only application patch and two
operator-authored Playwright cases exercising existing search behavior. It
passed the complete runner, including existing unit, build, board and repair
checks, with non-contiguous IDs `AC-4` and `AC-12`, exact frozen case titles,
and zero retries or skips in the two generic cases.

This verifies the generalized execution path with actual browser results; it
does not substitute for implementing or releasing the card-density feature.
The source application and live deployment were unchanged.

## Regression coverage

Offline suites cover unrelated prose PRDs, explicit/non-contiguous criteria,
legacy evidence, design iframe isolation, partial recorded journeys with full
acceptance coverage, wrong-feature evidence, malformed or downgraded metadata,
restored-check revalidation and exact publication/report identities. The
workflow YAML and both embedded Python summary blocks were syntax-checked.
All 221 Python pipeline tests and 17 Node unit tests passed, and the application
build passed. Existing board and repair browser checks also passed in the real
disposable-runner probe.

The next issue-triggered feature release remains the user's manual test.
