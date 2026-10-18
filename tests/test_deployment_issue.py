import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import deployment_issue


class DeploymentIssueTests(unittest.TestCase):
    def test_feature_closure_requires_the_matching_live_result(self):
        for failure in ("missing", "wrong-revision", "wrong-patch", "failed"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                with contextlib.chdir(temporary):
                    Path("artifacts").mkdir()
                    metadata = {"kind": "feature", "issue": 8, "pr": 9, "session": "session",
                                "event": "issue-feature-100", "patchSha256": "a" * 64}
                    Path("artifacts/deployment.json").write_text(json.dumps(metadata))
                    if failure != "missing":
                        directory = Path("artifacts/feature-delivery-live")
                        directory.mkdir()
                        result = {"passed": failure != "failed", "mode": "live",
                                  "revision": "b" * 40, "eventId": metadata["event"],
                                  "patchSha256": metadata["patchSha256"]}
                        if failure == "wrong-revision":
                            result["revision"] = "c" * 40
                        if failure == "wrong-patch":
                            result["patchSha256"] = "c" * 64
                        (directory / "result.json").write_text(json.dumps(result))
                    api = Mock()
                    with patch.object(deployment_issue, "api", api), \
                            patch.object(sys, "argv", ["deployment_issue.py", "close"]), \
                            patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo",
                                                   "DEPLOY_SHA": "b" * 40}):
                        with self.assertRaises((ValueError, FileNotFoundError)):
                            deployment_issue.main()
                    api.assert_not_called()

    def test_matching_live_feature_can_close_the_issue(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.chdir(temporary):
            Path("artifacts/feature-delivery-live").mkdir(parents=True)
            metadata = {"kind": "feature", "issue": 8, "pr": 9, "session": "session",
                        "event": "issue-feature-100", "patchSha256": "a" * 64}
            Path("artifacts/deployment.json").write_text(json.dumps(metadata))
            Path("artifacts/feature-delivery-live/result.json").write_text(json.dumps({
                "passed": True, "mode": "live", "revision": "b" * 40,
                "eventId": metadata["event"], "patchSha256": metadata["patchSha256"]}))
            api = Mock(return_value={"state": "open"})
            with patch.object(deployment_issue, "api", api), \
                    patch.object(sys, "argv", ["deployment_issue.py", "close"]), \
                    patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo",
                                           "DEPLOY_SHA": "b" * 40, "APP_URL": "https://example.invalid"}):
                self.assertEqual(deployment_issue.main(), 0)
            self.assertEqual([row.args[0] for row in api.call_args_list], ["GET", "POST", "PATCH"])
            self.assertIn("Live feature acceptance passed", api.call_args_list[1].args[2]["body"])


if __name__ == "__main__":
    unittest.main()
