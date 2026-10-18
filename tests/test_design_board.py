import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from design_board import final_plan, render_board, validate_plan, FRAME_CSP
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


if __name__ == "__main__":
    unittest.main()
