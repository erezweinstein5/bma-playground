import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from design_board import canonical_json, final_plan, render_board, save_board, validate_plan, MAX_PLAN, FRAME_CSP
from plan_feature import validate_preparation


def example_plan():
    return {
        "schemaVersion": 1, "title": "Team workload", "problem": "Overload is hidden",
        "approach": "Make active load visible", "assumptions": [], "questions": [], "nonGoals": [],
        "requirements": [{"id": f"AC-{n}", "behavior": "Behavior", "verification": "Fixture"}
                         for n in range(1, 8)],
        "tasks": [{"title": "Derived totals", "detail": "Use canonical tasks",
                   "paths": ["src/board.js"], "acceptanceIds": ["AC-1"]}],
        "flow": [{"title": "Reassign", "detail": "Move active effort"}],
        "screens": [{"kind": kind, "title": kind.title(), "caption": "Proposed state",
                     "width": 1120, "height": 740, "html": "<main>Maya 12 / 8</main>",
                     "css": "body { color: green }", "acceptanceIds": ["AC-1"]}
                    for kind in ("wireframe", "overloaded", "balanced", "mobile", "validation")],
    }


def search_plan():
    return {
        "schemaVersion": 2, "title": "Search task titles", "problem": "Tasks are hard to locate",
        "approach": "Filter the visible task list by title without modifying saved tasks",
        "assumptions": ["Search is case insensitive"], "questions": [], "nonGoals": [],
        "requirements": [
            {"id": "AC-1", "behavior": "Entering a title substring shows matching tasks",
             "verification": "Enter deploy; Deploy preview is the only visible task"},
            {"id": "AC-2", "behavior": "An unmatched query shows an empty state",
             "verification": "Enter unmatched; the results show No matching tasks"},
            {"id": "AC-3", "behavior": "Clearing search restores the full task list",
             "verification": "Clear search; all three original tasks are visible"},
        ],
        "tasks": [{"title": "Filter visible titles", "detail": "Add query state and an empty state",
                   "paths": ["src/main.js"], "acceptanceIds": ["AC-1", "AC-2", "AC-3"]}],
        "flow": [{"title": "Find a task", "detail": "Enter a query, inspect results, clear it"}],
        "screens": [
            {"kind": kind, "title": title, "caption": "Proposed search state",
             "width": 900, "height": 600,
             "html": '<main><label>Search tasks <input id="task-search"></label>'
                     f'<p id="result-count">{count}</p><div id="task-results">{result}</div></main>',
             "css": "body { color: #24332e; padding: 24px }", "acceptanceIds": refs}
            for kind, title, count, result, refs in (
                ("results", "Matching tasks", "1 task", "Deploy preview", ["AC-1", "AC-3"]),
                ("empty", "No matches", "0 tasks", "No matching tasks", ["AC-2"]),
            )
        ],
        "uiContract": (
            "## Search contract\nThe initial fixture has three tasks including Deploy preview.\n"
            "AC-1: #task-search is a labeled Search tasks input. Filling deploy filters titles "
            "case insensitively; #task-results shows Deploy preview and #result-count says 1 task.\n"
            "AC-2: Filling unmatched shows No matching tasks in #task-results.\n"
            "AC-3: Clearing the input restores all tasks and #result-count says 3 tasks."
        ),
        "browserJourney": [
            {"action": "assert_text", "selector": "#result-count", "expected": "3 tasks",
             "description": "Verify initial task count", "acceptanceIds": ["AC-1", "AC-3"]},
            {"action": "fill", "selector": "#task-search", "value": "deploy",
             "description": "Search for a title", "acceptanceIds": ["AC-1"]},
            {"action": "assert_text", "selector": "#task-results", "expected": "Deploy preview",
             "description": "Verify matching title", "acceptanceIds": ["AC-1"]},
            {"action": "fill", "selector": "#task-search", "value": "unmatched",
             "description": "Search for an absent title", "acceptanceIds": ["AC-2"]},
            {"action": "assert_text", "selector": "#task-results", "expected": "No matching tasks",
             "description": "Verify empty state", "acceptanceIds": ["AC-2"]},
            {"action": "fill", "selector": "#task-search", "value": "",
             "description": "Clear the query", "acceptanceIds": ["AC-3"]},
            {"action": "assert_text", "selector": "#result-count", "expected": "3 tasks",
             "description": "Verify all tasks return", "acceptanceIds": ["AC-3"]},
        ],
    }


def preferences_plan():
    plan = search_plan()
    plan.update({
        "title": "Remember display preference", "problem": "People need a comfortable display",
        "approach": "Offer a persistent display preference", "assumptions": [],
        "requirements": [
            {"id": "AC-4", "behavior": "Toggle between light and dark display",
             "verification": "Toggle from Light to Dark and inspect the visible status"},
            {"id": "AC-12", "behavior": "Keep the chosen display after reload",
             "verification": "Reload after selecting Dark; the status still shows Dark"},
        ],
        "tasks": [{"title": "Remember theme", "detail": "Persist the chosen display preference",
                   "paths": ["src/main.js"], "acceptanceIds": ["AC-4", "AC-12"]}],
        "flow": [{"title": "Choose a display", "detail": "Toggle the theme and reload"}],
        "screens": [{"kind": "settings", "title": "Display preference", "caption": "Dark selected",
                     "width": 900, "height": 600,
                     "html": '<main><button id="theme-toggle">Toggle theme</button>'
                             '<p id="theme-status">Dark</p></main>',
                     "css": "body { background: #222; color: white }",
                     "acceptanceIds": ["AC-4", "AC-12"]}],
        "uiContract": (
            "## Display contract\nAC-4: Start with an unset preference and visible Light in "
            "#theme-status. The labeled #theme-toggle button switches to Dark.\n"
            "AC-12: Store the preference locally and restore Dark in #theme-status after reload."
        ),
        "browserJourney": [
            {"action": "assert_text", "selector": "#theme-status", "expected": "Light",
             "description": "Verify default display", "acceptanceIds": ["AC-4"]},
            {"action": "click", "selector": "#theme-toggle",
             "description": "Choose dark display", "acceptanceIds": ["AC-4"]},
            {"action": "assert_text", "selector": "#theme-status", "expected": "Dark",
             "description": "Verify chosen display", "acceptanceIds": ["AC-4"]},
            {"action": "reload", "description": "Reload the application", "acceptanceIds": ["AC-12"]},
            {"action": "assert_text", "selector": "#theme-status", "expected": "Dark",
             "description": "Verify saved preference", "acceptanceIds": ["AC-12"]},
        ],
    })
    return plan


def provenance():
    return {key: "test" for key in
            ("eventId", "baseCommit", "prdSha256", "planSha256", "sessionId", "turnId")}


class ParentParser(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class PlanningTests(unittest.TestCase):
    def test_missing_criteria_or_states_are_rejected(self):
        for field in ("requirements", "screens"):
            plan = example_plan()
            plan[field].pop()
            with self.assertRaises(ValueError):
                validate_plan(plan)

    def test_rejects_protected_or_traversal_implementation_paths(self):
        for path in (".github/workflows/ci.yml", "src/../AGENTS.md", "src//a.js", "/src/a.js"):
            plan = example_plan()
            plan["tasks"][0]["paths"] = [path]
            with self.assertRaisesRegex(ValueError, "outside"):
                validate_plan(plan)

    def test_rejects_unknown_ac_and_unbounded_frames(self):
        plan = example_plan()
        plan["screens"][0]["acceptanceIds"] = ["AC-99"]
        with self.assertRaises(ValueError):
            validate_plan(plan)
        plan = example_plan()
        plan["screens"][0]["width"] = 99999
        with self.assertRaises(ValueError):
            validate_plan(plan)

    def test_final_answer_must_belong_to_exact_completed_turn(self):
        final = {"type": "message", "role": "assistant", "phase": "final_answer",
                 "status": "completed", "turn_id": "current",
                 "content": [{"type": "output_text", "text": json.dumps(example_plan())}]}
        self.assertEqual(final_plan([final], "current")["title"], "Team workload")
        for change in ({"turn_id": "old"}, {"status": "in_progress"}, {"phase": "commentary"}):
            with self.assertRaises(ValueError):
                final_plan([{**final, **change}], "current")
        with self.assertRaises(ValueError):
            final_plan([final, copy.deepcopy(final)], "current")

    def test_model_markup_cannot_escape_into_parent_or_enable_iframe_scripts(self):
        plan = example_plan()
        payload = '</iframe><script>window.top.evil=1</script><img src="https://bad.invalid/x">'
        plan["screens"][0]["html"] = payload
        plan["title"] = '"><script>evil()</script> @@FRAMES@@'
        plan["screens"][0]["css"] = '</style><script>evil()</script><style>'
        metadata = {key: "test" for key in
                    ("eventId", "baseCommit", "prdSha256", "planSha256", "sessionId", "turnId")}
        result = render_board(plan, metadata)
        parent = ParentParser(result)
        self.assertEqual(len([tag for tag, _ in parent.tags if tag == "script"]), 1)
        self.assertEqual(len([tag for tag, _ in parent.tags if tag == "img"]), 0)
        frames = [attrs for tag, attrs in parent.tags if tag == "iframe"]
        self.assertEqual(len(frames), 5)
        for frame in frames:
            self.assertEqual(frame["sandbox"], "")
            self.assertEqual(frame["referrerpolicy"], "no-referrer")
            self.assertIn("default-src &#x27;none&#x27;", frame["srcdoc"])
            self.assertNotIn("allow-scripts", frame["sandbox"])
        self.assertIn(payload, frames[0]["srcdoc"])

    def test_source_preparation_requires_successful_service_item(self):
        item = {"type": "command_execution", "turn_id": "turn", "status": "completed",
                "exit_code": 0, "output": '{"repository":"/mnt/home/workspace/jobs/probe-a/repo",'
                                        '"base":"abc","persistenceMarker":"hello"}'}
        validate_preparation([item], "turn", "probe-a", "abc")
        for change in ({"turn_id": "old"}, {"exit_code": 1}, {"output": "Claimed I read files"}):
            with self.assertRaises(ValueError):
                validate_preparation([{**item, **change}], "turn", "probe-a", "abc")


class GenericPlanningTests(unittest.TestCase):
    def test_unrelated_features_have_variable_ids_screens_and_journeys(self):
        for plan in (search_plan(), preferences_plan()):
            with self.subTest(title=plan["title"]):
                self.assertIs(validate_plan(plan), plan)
                page = render_board(plan, provenance())
                parent = ParentParser(page)
                self.assertEqual(sum(tag == "iframe" for tag, _ in parent.tags), len(plan["screens"]))
                self.assertIn("Observable UI contract", page)
                self.assertIn("Proposed browser journey", page)
                self.assertNotIn("Workload", page)
                for requirement in plan["requirements"]:
                    self.assertIn(requirement["id"], page)

    def test_missing_duplicate_malformed_or_unrelated_criteria_fail_closed(self):
        for refs in ([], ["AC-1", "AC-1"], ["AC-1", "AC-99"], [None], [["AC-1"]]):
            plan = search_plan()
            plan["tasks"][0]["acceptanceIds"] = refs
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                validate_plan(plan)
        for field in ("requirements", "tasks", "flow", "screens"):
            plan = search_plan()
            plan[field] = ["not an object"]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_plan(plan)
        for replacement in ([], [None], [{"id": "AC-1"}] * 2):
            plan = search_plan()
            plan["requirements"] = replacement
            with self.subTest(requirements=replacement), self.assertRaises(ValueError):
                validate_plan(plan)

    def test_frame_kinds_cannot_inject_parent_markup_or_collide(self):
        for kind in ('"><script>evil()</script>', "results", "two words", "", "x" * 49, None, []):
            plan = search_plan()
            plan["screens"][1]["kind"] = kind
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_plan(plan)

    def test_generic_paths_remain_source_only_and_legacy_paths_remain_readable(self):
        for path in ("tests/feature/search.spec.js", "docs/features/search.md", "package.json",
                     "backend/app.py", ".github/workflows/ci.yml", "src/../server.js"):
            plan = search_plan()
            plan["tasks"][0]["paths"] = [path]
            plan["questions"] = ["This needs scope beyond src/"]
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "outside"):
                validate_plan(plan)
        legacy = example_plan()
        legacy["tasks"][0]["paths"] += ["tests/feature/workload.spec.js", "docs/features/workload.md"]
        self.assertIs(validate_plan(legacy), legacy)

    def test_new_contract_and_journey_cannot_be_omitted_or_downgraded(self):
        for key in ("uiContract", "browserJourney"):
            plan = search_plan()
            del plan[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_plan(plan)
        for version in (True, "2", 2.0, 0, 3):
            plan = search_plan()
            plan["schemaVersion"] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_plan(plan)
        legacy = example_plan()
        legacy["uiContract"] = search_plan()["uiContract"]
        with self.assertRaisesRegex(ValueError, "Legacy"):
            validate_plan(legacy)

    def test_unknown_or_unsafe_journey_steps_fail_but_subset_coverage_is_valid(self):
        for step in (
            {"action": "open", "url": "https://elsewhere.invalid"},
            {"action": "evaluate", "script": "alert(1)"},
            {"action": "reload", "url": "https://elsewhere.invalid"},
            {"action": "click", "selector": "#task-search", "acceptanceIds": ["AC-99"]},
        ):
            plan = search_plan()
            plan["browserJourney"][1] = {
                "description": "Unsupported request", "acceptanceIds": ["AC-1"], **step}
            with self.subTest(step=step), self.assertRaises(ValueError):
                validate_plan(plan)
        plan = search_plan()
        for step in plan["browserJourney"]:
            step["acceptanceIds"] = ["AC-1"]
        self.assertIs(validate_plan(plan), plan)

    def test_keyboard_criterion_does_not_need_native_journey_steps_or_questions(self):
        plan = search_plan()
        plan["requirements"].append({
            "id": "AC-6", "behavior": "Keyboard users can search and clear while focus stays in the input",
            "verification": "In Playwright, Tab to the search input, type a query, clear it with "
                            "the keyboard, and assert focus and the restored task list",
        })
        plan["tasks"][0]["acceptanceIds"].append("AC-6")
        plan["uiContract"] += (
            "\nAC-6: #task-search is keyboard reachable; clearing it retains input focus. "
            "Independent Playwright acceptance verifies the full keyboard sequence."
        )
        self.assertNotIn("AC-6", {ref for step in plan["browserJourney"] for ref in step["acceptanceIds"]})
        self.assertIs(validate_plan(plan), plan)
        self.assertEqual(plan["questions"], [])

    def test_contract_and_journey_text_cannot_enter_parent_dom(self):
        plan = search_plan()
        payload = '</pre><script>window.evil=1</script><img src="https://bad.invalid/x"> @@FRAMES@@'
        plan["uiContract"] += payload
        plan["browserJourney"][0]["description"] = payload
        plan["browserJourney"][0]["expected"] = payload
        plan["screens"][0]["html"] = payload
        plan["screens"][0]["css"] = "</style><script>evil()</script><style>"
        parent = ParentParser(render_board(plan, provenance()))
        self.assertEqual(sum(tag == "script" for tag, _ in parent.tags), 1)
        self.assertFalse(any(tag == "img" for tag, _ in parent.tags))
        frames = [attrs for tag, attrs in parent.tags if tag == "iframe"]
        self.assertEqual(len(frames), 2)
        self.assertIn(payload, frames[0]["srcdoc"])
        for frame in frames:
            self.assertEqual(frame["sandbox"], "")
            self.assertEqual(frame["referrerpolicy"], "no-referrer")
            self.assertIn("default-src &#x27;none&#x27;", frame["srcdoc"])

    def test_material_questions_can_defer_executable_contract_without_fabrication(self):
        plan = search_plan()
        plan["questions"] = ["AC-1 needs an external search service and browser authentication. "
                             "How should the source-only scope and browser capability be resolved?"]
        plan["uiContract"], plan["browserJourney"] = "", []
        self.assertIs(validate_plan(plan), plan)
        self.assertIn("Pending material questions.", render_board(plan, provenance()))
        with tempfile.TemporaryDirectory() as directory:
            saved = save_board(plan, {**provenance(), "status": "questions"}, Path(directory))
            self.assertEqual(saved["status"], "questions")
            self.assertEqual(json.loads((Path(directory) / "plan.json").read_bytes()), plan)
        plan["questions"] = []
        with self.assertRaises(ValueError):
            validate_plan(plan)

    def test_questions_never_exempt_malformed_or_unsafe_supplied_contracts(self):
        for key, value in (
            ("uiContract", None), ("uiContract", "x" * 80_001),
            ("browserJourney", None),
            ("browserJourney", [{"action": "evaluate", "script": "alert(1)"}]),
        ):
            plan = search_plan()
            plan["questions"] = ["AC-1 needs product clarification"]
            plan[key] = value
            with self.subTest(key=key, value=str(value)[:80]), self.assertRaises(ValueError):
                validate_plan(plan)

    def test_output_contract_frame_and_response_bounds(self):
        for field, value in (("uiContract", "x" * 80_001), ("screens", []),
                             ("screens", search_plan()["screens"] * 5),
                             ("browserJourney", search_plan()["browserJourney"] * 10)):
            plan = search_plan()
            plan[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_plan(plan)
        plan = search_plan()
        plan["extra"] = "x" * MAX_PLAN
        with self.assertRaisesRegex(ValueError, "oversized"):
            validate_plan(plan)

    def test_saved_plans_keep_schema_and_bind_contract_and_journey_to_hash(self):
        for plan in (example_plan(), search_plan(), preferences_plan()):
            with self.subTest(title=plan["title"]), tempfile.TemporaryDirectory() as directory:
                metadata = save_board(plan, provenance(), Path(directory))
                saved = (Path(directory) / "plan.json").read_bytes()
                self.assertEqual(metadata["schemaVersion"], plan["schemaVersion"])
                self.assertEqual(saved, canonical_json(plan))
                self.assertEqual(metadata["planSha256"], hashlib.sha256(saved).hexdigest())
                self.assertEqual(json.loads(saved), plan)
                if plan["schemaVersion"] == 2:
                    changed = copy.deepcopy(plan)
                    changed["browserJourney"][-1]["expected"] += "changed"
                    self.assertNotEqual(metadata["planSha256"],
                                        hashlib.sha256(canonical_json(changed)).hexdigest())

    def test_invalid_plan_leaves_existing_artifacts_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_board(search_plan(), provenance(), root)
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            invalid = search_plan()
            invalid["uiContract"] = ""
            with self.assertRaises(ValueError):
                save_board(invalid, provenance(), root)
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})


if __name__ == "__main__":
    unittest.main()
