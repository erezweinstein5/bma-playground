"""Run a fresh BMA acceptance-author session and freeze its test bundle."""
import argparse
import json
from pathlib import Path
import sys

from design_board import canonical_json, validate_plan
from feature_contract import plan_contract, plan_journey, requirement_ids, validate_planning_identity
from feature_stages import (FeatureStages, acceptance_prompt, digest, final_json,
                            source_archive, validate_acceptance)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--planning", type=Path, required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--stage", default="acceptance", choices=("acceptance", "acceptance-v2", "acceptance-v3"),
                        help="An explicit new authoring version; never overwrites the frozen bundle")
    parser.add_argument("--authoring-review", type=Path,
                        help="Explicit pre-implementation review with prior bundle and corrections")
    args = parser.parse_args()
    provenance = json.loads((args.planning / "provenance.json").read_text())
    plan = validate_plan(json.loads((args.planning / "plan.json").read_text()))
    prd = (args.planning / "prd.md").read_text()
    if digest(canonical_json(plan)) != provenance["planSha256"] or digest(prd.encode()) != provenance["prdSha256"]:
        raise ValueError("Planning input does not match its pinned provenance")
    if plan["questions"]:
        raise ValueError("Material planning questions must be resolved before acceptance")
    validate_planning_identity(plan, prd, provenance, args.event)
    contract = plan_contract(plan)
    required_ids = requirement_ids(plan) if plan["schemaVersion"] == 2 else None
    directory = Path("artifacts") / args.event
    stages = FeatureStages(args.event, provenance["baseCommit"], directory / "stages")
    prompt = acceptance_prompt(prd, plan, contract)
    review_identity = {}
    if args.authoring_review:
        review_bytes = args.authoring_review.read_bytes()
        if len(review_bytes) > 100_000:
            raise ValueError("Oversized authoring review")
        review = json.loads(review_bytes)
        prompt += ("\nPRE-IMPLEMENTATION ACCEPTANCE AUTHORING REVIEW:\n" + json.dumps(review) +
                   "\nPreserve the supplied prior spec and cases except the explicitly "
                   "identified corrections. Return the complete corrected bundle in the "
                   "same schema. Do not rewrite unrelated tests or add new constraints.")
        review_identity["authoringReviewSha256"] = digest(review_bytes)
    state, items = stages.run(args.stage,
        "You independently author observable acceptance tests from the approved PRD "
        "and controller-owned UI contract. Inspect assigned source only. Do not implement "
        "the feature, modify repository files, read credentials, or access GitHub. "
        "Return the requested test bundle as JSON; never claim unexecuted tests passed.",
        prompt, archive=source_archive(provenance["baseCommit"]),
        extra_identity={"planSha256": provenance["planSha256"],
                        "prdSha256": provenance["prdSha256"], "contractSha256": digest(contract.encode()),
                        **review_identity})
    bundle = validate_acceptance(final_json(items, state["outcome"]["turnId"]), required_ids)
    destination = directory / args.stage
    destination.mkdir(parents=True, exist_ok=True)
    spec = bundle["spec"].encode()
    manifest = {"schemaVersion": 1, "eventId": args.event, "baseCommit": provenance["baseCommit"],
        "planSha256": provenance["planSha256"], "prdSha256": provenance["prdSha256"],
        "contractSha256": digest(contract.encode()), "acceptanceSha256": digest(spec),
        "sessionId": state["sessionId"], "turnId": state["outcome"]["turnId"],
        "cases": bundle["cases"], "status": "authored-not-executed", **review_identity}
    if required_ids is not None:
        journey = plan_journey(plan)
        manifest.update({"schemaVersion": 2, "acceptanceIds": sorted(required_ids),
                         "browserJourney": journey,
                         "browserJourneySha256": digest(canonical_json(journey))})
    for name, data in (("workload.acceptance.spec.js", spec),
                       ("manifest.json", json.dumps(manifest, indent=2).encode()),
                       ("ui-contract.md", contract.encode())):
        target = destination / name
        if target.exists() and target.read_bytes() != data:
            raise ValueError("Frozen acceptance output already exists with different content")
        target.write_bytes(data)
    # The runtime can read sources/* but cannot alter the controller's frozen bundle.
    stages.freeze(f"sources/{args.event}/acceptance-{manifest['acceptanceSha256']}.json",
                  json.dumps({"manifest": manifest, "spec": bundle["spec"]}).encode(),
                  "application/json")
    print(f"Frozen {len(bundle['cases'])} authored cases: {manifest['acceptanceSha256']}", flush=True)
    print("Acceptance has not run; do not treat authorship as a passing gate.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
