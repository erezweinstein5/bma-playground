"""Versioned product contracts shared by PRD planning and independent gates.

Business rules belong to a hashed plan. The controller validates structure,
traceability and supported browser operations; it never chooses the feature.
"""
from pathlib import Path
import hashlib
import json
import re

LEGACY_KIND = "team-workload-v1"
GENERIC_KIND = "prd-feature-v1"
LEGACY_AC_IDS = frozenset(f"AC-{n}" for n in range(1, 8))
AC_PATTERN = re.compile(r"AC-[1-9][0-9]{0,3}\Z")
MAX_REQUIREMENTS = 50


def validate_acceptance_ids(value):
    if (not isinstance(value, (list, tuple, set, frozenset))
            or not 1 <= len(value) <= MAX_REQUIREMENTS
            or any(not isinstance(item, str) or not AC_PATTERN.fullmatch(item) for item in value)
            or len(set(value)) != len(value)):
        raise ValueError("Invalid or duplicate acceptance criterion IDs")
    return frozenset(value)


def requirement_ids(plan):
    if not isinstance(plan, dict) or type(plan.get("schemaVersion")) is not int:
        raise ValueError("Missing versioned feature plan")
    if plan["schemaVersion"] not in (1, 2):
        raise ValueError("Unsupported feature plan version")
    rows = plan.get("requirements")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Missing plan requirements")
    ids = validate_acceptance_ids([row.get("id") for row in rows])
    if plan["schemaVersion"] == 1 and ids != LEGACY_AC_IDS:
        raise ValueError("Legacy plan must preserve its seven acceptance criteria")
    return ids


def validate_prd_requirements(plan, prd):
    ids = requirement_ids(plan)
    if not isinstance(prd, str):
        raise ValueError("PRD must be text")
    explicit = set(re.findall(r"\bAC-[1-9][0-9]*\b", prd))
    if explicit and ids != explicit:
        raise ValueError("Plan must preserve every explicit PRD acceptance ID exactly")
    return plan


def validate_planning_identity(plan, prd, provenance, event):
    """Bind a stage to the exact versioned plan, literal PRD and authorized run."""
    validate_prd_requirements(plan, prd)
    version = plan["schemaVersion"]
    if (not isinstance(provenance, dict) or provenance.get("eventId") != event
            or type(provenance.get("schemaVersion")) is not int
            or provenance["schemaVersion"] != version
            or provenance.get("planSchemaVersion", 1) != version
            or (version == 2 and type(provenance.get("planSchemaVersion")) is not int)
            or provenance.get("status") != "planned" or plan.get("questions")):
        raise ValueError("Planning event, version or readiness differs from the requested stage")
    encoded = json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    if (hashlib.sha256(encoded).hexdigest() != provenance.get("planSha256")
            or hashlib.sha256(prd.encode()).hexdigest() != provenance.get("prdSha256")):
        raise ValueError("Planning input differs from its pinned hashes")
    return plan


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise ValueError(f"Invalid browser journey {name}")


def validate_journey(journey, acceptance_ids):
    """Validate an inert, bounded program for the existing native browser tools."""
    ids = validate_acceptance_ids(acceptance_ids)
    if not isinstance(journey, list) or not 3 <= len(journey) <= 60:
        raise ValueError("Browser journey needs 3-60 ordered steps")
    fields = {
        "click": {"selector"}, "fill": {"selector", "value"},
        "select": {"selector", "value"}, "drag": {"selector", "target"},
        "reload": set(), "assert_text": {"selector", "expected"},
    }
    assertions = actions = 0
    for step in journey:
        if (not isinstance(step, dict) or not isinstance(step.get("action"), str)
                or step["action"] not in fields):
            raise ValueError("Unsupported browser journey action")
        action = step["action"]
        required = {"action", "description", "acceptanceIds"} | fields[action]
        if set(step) != required:
            raise ValueError("Browser journey step has missing or unexpected fields")
        _text(step["description"], "description", 600)
        refs = validate_acceptance_ids(step["acceptanceIds"])
        if not refs <= ids:
            raise ValueError("Browser journey references unknown acceptance criteria")
        for name in fields[action]:
            # Empty fill text is a real operation (clear a search field).
            if name == "value" and action == "fill" and step[name] == "":
                continue
            _text(step[name], name, 2000 if name in {"value", "expected"} else 600)
        assertions += action == "assert_text"
        actions += action != "assert_text"
    if assertions < 2 or actions < 1:
        raise ValueError("Browser journey must include actions and before/after text assertions")
    return journey


def plan_contract(plan):
    requirement_ids(plan)
    if plan["schemaVersion"] == 1:
        return (Path(__file__).resolve().parent.parent / "demo/contracts/team-workload-ui.md").read_text()
    contract = plan.get("uiContract")
    if not isinstance(contract, str) or not 50 <= len(contract.encode()) <= 80_000:
        raise ValueError("Generic plan requires a bounded observable UI contract")
    return contract


def plan_journey(plan):
    ids = requirement_ids(plan)
    if plan["schemaVersion"] == 2:
        return validate_journey(plan.get("browserJourney"), ids)
    # This is only the compatibility path for already published Workload plans.
    def step(action, description, refs, **args):
        return {"action": action, "description": description, "acceptanceIds": refs, **args}
    loads = {owner: f'[data-workload-owner="{owner}"] [data-workload-load]'
             for owner in ("Maya", "Erez")}
    return [
        step("click", "Reset the demo board", ["AC-7"], selector="#reset"),
        step("click", "Open Workload", ["AC-1"], selector='[data-view="workload"]'),
        step("assert_text", "Verify Maya's initial load", ["AC-1"], selector=loads["Maya"], expected="12 / 8 points"),
        step("assert_text", "Verify Erez's initial load", ["AC-1"], selector=loads["Erez"], expected="4 / 8 points"),
        step("select", "Reassign the mobile task", ["AC-3"], selector='#workload select[data-owner-for="mobile"]', value="Erez"),
        step("assert_text", "Verify balanced Maya", ["AC-3"], selector=loads["Maya"], expected="8 / 8 points"),
        step("assert_text", "Verify balanced Erez", ["AC-3"], selector=loads["Erez"], expected="8 / 8 points"),
        step("click", "Open Board", ["AC-3"], selector='[data-view="board"]'),
        step("assert_text", "Verify Board avatar", ["AC-3"], selector='[data-task-id="mobile"] .avatar', expected="EW"),
        step("reload", "Reload the saved board", ["AC-6"]),
        step("click", "Reopen Workload", ["AC-6"], selector='[data-view="workload"]'),
        step("assert_text", "Verify saved Maya load", ["AC-6"], selector=loads["Maya"], expected="8 / 8 points"),
        step("assert_text", "Verify saved Erez load", ["AC-6"], selector=loads["Erez"], expected="8 / 8 points"),
    ]
