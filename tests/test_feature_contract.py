import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from feature_contract import (plan_contract, plan_journey, requirement_ids,
                              validate_acceptance_ids, validate_journey,
                              validate_planning_identity, validate_prd_requirements)
from test_design_board import example_plan, preferences_plan, search_plan


class FeatureContractTests(unittest.TestCase):
    def test_planning_handoff_binds_event_schema_and_literal_prd(self):
        from design_board import canonical_json
        from feature_stages import digest
        plan, prd = preferences_plan(), "AC-4: Change the display. AC-12: Save it."
        provenance = {"schemaVersion": 2, "planSchemaVersion": 2, "eventId": "probe-contract",
                      "status": "planned", "planSha256": digest(canonical_json(plan)),
                      "prdSha256": digest(prd.encode())}
        validate_planning_identity(plan, prd, provenance, "probe-contract")
        for changed in ({**provenance, "eventId": "probe-other"},
                        {**provenance, "schemaVersion": 1},
                        {**provenance, "planSchemaVersion": 1},
                        {**provenance, "status": "questions"},
                        {**provenance, "prdSha256": "a" * 64}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_planning_identity(plan, prd, changed, "probe-contract")
        bad_prd = "AC-1: Renumbered. AC-2: Changed."
        with self.assertRaises(ValueError):
            validate_planning_identity(plan, bad_prd,
                                       {**provenance, "prdSha256": digest(bad_prd.encode())},
                                       "probe-contract")

    def test_unrelated_plans_keep_their_own_contracts_and_journeys(self):
        search, display = search_plan(), preferences_plan()
        self.assertNotEqual(requirement_ids(search), requirement_ids(display))
        self.assertNotEqual(plan_contract(search), plan_contract(display))
        self.assertNotEqual(plan_journey(search), plan_journey(display))
        for plan in (search, display):
            self.assertEqual(plan_contract(plan), plan["uiContract"])
            self.assertEqual(plan_journey(plan), plan["browserJourney"])
        self.assertEqual(requirement_ids(example_plan()), {f"AC-{n}" for n in range(1, 8)})
        self.assertIn("Workload", plan_contract(example_plan()))

    def test_explicit_ids_are_preserved_and_prose_is_supported(self):
        plan = preferences_plan()
        validate_prd_requirements(plan, "AC-4: Toggle display. AC-12: Persist display.")
        validate_prd_requirements(plan, "Toggle the display and keep the preference on reload.")
        for prd in ("AC-4 only", "AC-4 AC-12 AC-13", "AC-1 AC-2"):
            with self.assertRaises(ValueError):
                validate_prd_requirements(plan, prd)

    def test_invalid_ids_and_duplicate_requirements_fail_closed(self):
        for ids in ([], ["AC-1", "AC-1"], ["AC-0"], ["AC-01"], ["AC-10000"],
                    "AC-1", [None], [1], [["AC-1"]]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_acceptance_ids(ids)

    def test_journey_cannot_request_arbitrary_tools_or_unknown_criteria(self):
        plan = search_plan()
        journey, ids = plan_journey(plan), requirement_ids(plan)
        for mutation in (
            lambda step: step.update(action="evaluate", script="document.body"),
            lambda step: step.update(url="https://other.example"),
            lambda step: step.update(acceptanceIds=["AC-999"]),
            lambda step: step.update(action=[]),
            lambda step: step.pop("description"),
        ):
            changed = copy.deepcopy(journey)
            mutation(changed[0])
            with self.assertRaises(ValueError):
                validate_journey(changed, ids)
        with self.assertRaises(ValueError):
            validate_journey([row for row in journey if row["action"] != "assert_text"], ids)


if __name__ == "__main__":
    unittest.main()
