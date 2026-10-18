import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from browser_evidence import unpack_capture, validate_manifest, validate_browser_calls, source_digest, evidence_markdown


class EvidenceTests(unittest.TestCase):
    def descriptor(self):
        return {"eventId": "issue-123", "phase": "before", "sourceSha256": "a" * 64,
                "url": "https://example.invalid/previews/issue-123/before/index.html"}

    def manifest(self):
        value = {**self.descriptor(), "outcome": "reproduced",
                 "captureSha256": "b" * 64, "screenshotSha256": "c" * 64,
                 "actions": [{"action": "select"}, {"action": "assert_text", "passed": True}]}
        value["previewUrl"] = value.pop("url")
        return value

    def test_missing_mismatched_or_failed_evidence_blocks_publication(self):
        for key, value in (("sourceSha256", "d" * 64), ("eventId", "issue-456"),
                           ("previewUrl", "https://example.invalid/"), ("outcome", "not_reproduced"),
                           ("actions", []), ("captureSha256", "wrong")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_manifest({**self.manifest(), key: value}, self.descriptor(), "before")
        validate_manifest(self.manifest(), self.descriptor(), "before")

    def test_after_requires_passed_observation(self):
        descriptor = {**self.descriptor(), "phase": "after"}
        manifest = {**self.manifest(), "phase": "after"}
        with self.assertRaises(ValueError):
            validate_manifest(manifest, descriptor, "after")
        validate_manifest({**manifest, "outcome": "passed"}, descriptor, "after")

    def test_source_digest_changes_with_bytes_and_names(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            (repo / "src").mkdir()
            source = repo / "src/a.js"
            source.write_text("before")
            before = source_digest(repo)
            source.write_text("after")
            self.assertNotEqual(source_digest(repo), before)
            after = source_digest(repo)
            source.rename(repo / "src/b.js")
            self.assertNotEqual(source_digest(repo), after)

    def capture(self, folder, override=None, extra=None):
        raw = io.BytesIO()
        Image.new("RGB", (4, 4), "blue").save(raw, "JPEG")
        index = {"duration": 1, "frames": [
            {"name": "frame-000000.jpg", "seconds": 0},
            {"name": "frame-000001.jpg", "seconds": 0.5},
        ]}
        if override:
            override(index)
        files = {"frames.json": json.dumps(index).encode(),
                 "frame-000000.jpg": raw.getvalue(), "frame-000001.jpg": raw.getvalue()}
        if extra:
            files.update(extra)
        archive = folder / "input.tar"
        with tarfile.open(archive, "w") as tar:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        return archive

    def test_valid_capture_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(len(unpack_capture(self.capture(root), root)["frames"]), 2)

    def test_untrusted_capture_cannot_write_paths_or_ffmpeg_scripts(self):
        for name in ("../outside", "/absolute", "frames.txt", "sub/frame-000002.jpg"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    unpack_capture(self.capture(root, extra={name: b"payload"}), root)

    def test_nonfinite_negative_and_reversed_timestamps_rejected(self):
        for stamp in (float("nan"), float("inf"), -0.1, 2):
            with self.subTest(stamp=stamp), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                archive = self.capture(root, lambda index: index["frames"][1].update(seconds=stamp))
                with self.assertRaises(ValueError):
                    unpack_capture(archive, root)

    def test_pr_contains_images_and_video_links_without_duplicate_gif(self):
        text = evidence_markdown({"url": "https://example.invalid/evidence/issue-123/hash",
                                  "browserSessions": {"before": "one", "after": "two"}})
        for expected in ("![Before]", "![After]", "before.mp4", "after.mp4", "report.json"):
            self.assertIn(expected, text)
        self.assertNotIn(".gif", text)
        self.assertNotIn("X-Amz-", text)

    def test_files_alone_cannot_prove_codex_browser_use(self):
        manifest = {**self.manifest(), "browserSessionId": "session"}
        with self.assertRaisesRegex(ValueError, "did not record opening"):
            validate_browser_calls([], "turn", self.descriptor(), manifest)
        values = {"browser_open": {"browserSessionId": "session", "url": self.descriptor()["url"]},
                  "browser_action": {"url": self.descriptor()["url"]},
                  "browser_assert_text": {"passed": True},
                  "browser_finish": manifest}
        items = [{"type": "mcp_call", "server_label": "agentcore_browser", "status": "completed",
                  "turn_id": "turn", "name": name, "output": {"content": [{"text": json.dumps(value)}]}}
                 for name, value in values.items()]
        validate_browser_calls(items, "turn", self.descriptor(), manifest)
        items[1]["arguments"] = json.dumps({"action": "select"})
        with self.assertRaisesRegex(ValueError, "must begin"):
            validate_browser_calls(items, "turn", self.descriptor(), manifest, first_action="drag")
        items[1]["arguments"] = json.dumps({"action": "drag"})
        validate_browser_calls(items, "turn", self.descriptor(), manifest, first_action="drag")
        with self.assertRaises(ValueError):
            validate_browser_calls(items, "another-turn", self.descriptor(), manifest)
        values["browser_finish"]["captureSha256"] = "d" * 64
        items[-1]["output"]["content"][0]["text"] = json.dumps(values["browser_finish"])
        with self.assertRaises(ValueError):
            validate_browser_calls(items, "turn", self.descriptor(), {**manifest, "captureSha256": "b" * 64})


if __name__ == "__main__":
    unittest.main()
