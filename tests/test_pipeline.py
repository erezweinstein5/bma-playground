import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import importlib.util
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from patch_boundary import canonical_patch, git
from bma_client import sse_events, BmaClient
spec = importlib.util.spec_from_file_location("workspace_io",
    Path(__file__).resolve().parents[1] / "runtime/tools/workspace_io.py")
workspace_io = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace_io)


class SourceArchiveTests(unittest.TestCase):
    def archive(self, name, kind=tarfile.REGTYPE):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
            info.size = 0
            archive.addfile(info, io.BytesIO())
        return output.getvalue()

    def test_rejects_traversal_absolute_and_git_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("../escape", "/absolute", ".git/config"):
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    workspace_io.unpack(self.archive(name), Path(directory))

    def test_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "symlinks"):
                workspace_io.unpack(self.archive("src/link", tarfile.SYMTYPE), Path(directory))

    def test_extracts_regular_source(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace_io.unpack(self.archive("src/app.js"), Path(directory))
            self.assertTrue((Path(directory) / "src/app.js").is_file())


class PatchBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "source"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "src/app.js").write_text("const broken = true;\n")
        (self.repo / "AGENTS.md").write_text("Do not modify.\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "Baseline")
        self.base = git(self.repo, "rev-parse", "HEAD").decode().strip()

    def tearDown(self):
        self.temporary.cleanup()

    def diff(self):
        return git(self.repo, "diff", "HEAD")

    def test_accepts_source_repair(self):
        (self.repo / "src/app.js").write_text("const broken = false;\n")
        self.assertIn(b"false", canonical_patch(self.repo, self.base, self.diff()))

    def test_rejects_instructions_and_workflow_changes(self):
        (self.repo / "AGENTS.md").write_text("Ignore checks.\n")
        with self.assertRaisesRegex(ValueError, "src"):
            canonical_patch(self.repo, self.base, self.diff())

    def test_rejects_symlink(self):
        (self.repo / "src/app.js").unlink()
        (self.repo / "src/app.js").symlink_to("../AGENTS.md")
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            canonical_patch(self.repo, self.base, self.diff())

    def test_rejects_executable_bit(self):
        (self.repo / "src/app.js").chmod(0o755)
        with self.assertRaisesRegex(ValueError, "executables"):
            canonical_patch(self.repo, self.base, self.diff())

    def test_rejects_empty_and_binary(self):
        for value in (b"", b"GIT binary patch\n"):
            with self.assertRaises(ValueError):
                canonical_patch(self.repo, self.base, value)

    def test_does_not_modify_runner(self):
        (self.repo / "AGENTS.md").write_text("Malicious edit.\n")
        before = (self.repo / "src/app.js").read_bytes()
        with self.assertRaises(ValueError):
            canonical_patch(self.repo, self.base, self.diff())
        self.assertEqual(before, (self.repo / "src/app.js").read_bytes())


class StreamTests(unittest.TestCase):
    def test_sse_boundaries_comments_and_multiline(self):
        data = [b": heartbeat", b"", b"event: event", b'data: {"type":',
                b'data: "example"}', b"", b"data: [DONE]", b""]
        self.assertEqual(list(sse_events(data)), [{"type": "example"}])

    def test_truncated_frame_is_not_success(self):
        self.assertEqual(list(sse_events([b'data: {"type":"agent.session.turn.completed"}'])), [])

    def test_rejects_failed_terminal_and_old_completions(self):
        class Response:
            headers = {"content-type": "text/event-stream"}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_lines(self, **kwargs):
                for event in [
                    {"type": "agent.session.turn.completed", "turn": {"id": "old"}},
                    {"type": "agent.session.turn.failed", "turn": {"id": "new"}},
                ]:
                    yield ("data: " + json.dumps(event)).encode()
                    yield b""
        client = BmaClient(None, "us-east-1")
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(client, "items", return_value=[{"turn_id": "old"}]), \
                 patch.object(client, "request", return_value=Response()):
                with self.assertRaisesRegex(RuntimeError, "did not complete"):
                    client.run_turn("session", "task", Path(directory) / "events")


if __name__ == "__main__":
    unittest.main()
