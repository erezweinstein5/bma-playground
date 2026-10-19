"""Offline durable claim and immutable proposal restoration checks."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from feature_intake import PlanningClaim, read_object, restore_design, validate_state
from design_board import save_board
from test_design_board import provenance, search_plan


class MemoryS3:
    """A conditional object store, with inspection hooks and closed-stream checks."""
    def __init__(self):
        self.objects, self.streams = {}, []
        self.get_object = Mock(side_effect=self.get)
        self.put_object = Mock(side_effect=self.put)

    def put(self, Bucket, Key, Body, **kwargs):
        key = (Bucket, Key)
        previous = self.objects.get(key)
        if ((kwargs.get("IfNoneMatch") == "*" and previous is not None)
                or ("IfMatch" in kwargs and (
                    previous is None or kwargs["IfMatch"] != previous[1]))):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        etag = '"' + hashlib.sha256(Body).hexdigest() + '"'
        self.objects[key] = (Body, etag)
        return {"ETag": etag}

    def get(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body, etag = self.objects[(Bucket, Key)]
        stream = io.BytesIO(body)
        self.streams.append(stream)
        return {"Body": stream, "ETag": etag}


class PlanningClaimTests(unittest.TestCase):
    def test_missing_claim_is_distinct_from_access_denied_and_malformed_data(self):
        s3 = MemoryS3()
        claim = PlanningClaim(s3, "bucket", "probe-search")
        self.assertIsNone(claim.read())
        s3.get_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
        with self.assertRaises(ClientError):
            claim.read()
        s3.get_object.side_effect = s3.get
        s3.put(Bucket="bucket", Key=claim.key, Body=b"invalid")
        with self.assertRaises(json.JSONDecodeError):
            claim.read()
        self.assertTrue(all(stream.closed for stream in s3.streams))

    def test_claim_is_create_only_and_updates_cannot_overwrite_a_competing_writer(self):
        s3 = MemoryS3()
        first, second = (PlanningClaim(s3, "bucket", "probe-search") for _ in range(2))
        first.write({"status": "claiming"}, create=True)
        with self.assertRaises(ClientError):
            second.write({"status": "claiming"}, create=True)
        self.assertEqual(second.read(), {"status": "claiming"})
        first.write({"status": "submitting"})
        with self.assertRaises(ClientError):
            second.write({"status": "planned"})
        self.assertEqual(first.read(), {"status": "submitting"})

    def test_bounded_reads_close_streams(self):
        s3 = MemoryS3()
        s3.put(Bucket="bucket", Key="state", Body=b"abcdef")
        with self.assertRaisesRegex(ValueError, "Oversized"):
            read_object(s3, "bucket", "state", 5)
        self.assertTrue(s3.streams[0].closed)

    def test_checkpoint_schema_and_identity_are_validated_before_recovery(self):
        state = {"schemaVersion": 2, "planSchemaVersion": 2, "eventId": "probe-search",
                 "baseCommit": "a" * 40, "prdSha256": "b" * 64,
                 "sourceArchiveSha256": "c" * 64, "status": "claiming"}
        validate_state(state, "probe-search", "b" * 64)
        for key, value in (("schemaVersion", True), ("schemaVersion", 3),
                           ("planSchemaVersion", 1), ("eventId", "probe-other"),
                           ("prdSha256", "d" * 64), ("baseCommit", "main"),
                           ("sessionId", "../bad"), ("status", "failed"),
                           ("status", "planned"), ("sourceArchiveSha256", None)):
            changed = {**state, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_state(changed, "probe-search", "b" * 64)


class RestoreDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = search_plan()
        self.prd = b"Find matching tasks by title. " * 8
        self.state = save_board(self.plan, {
            **provenance(), "schemaVersion": 2, "planSchemaVersion": 2,
            "status": "planned", "prdSha256": hashlib.sha256(self.prd).hexdigest(),
            "terminalEvent": "agent.session.turn.completed",
        }, self.root / "original")
        self.s3 = MemoryS3()
        original = self.root / "original"
        (original / "prd.md").write_bytes(self.prd)
        self.prefix = (f'evidence/{self.state["eventId"]}/design-{self.state["planSha256"][:16]}-'
                       + hashlib.sha256((original / "index.html").read_bytes()).hexdigest()[:12])
        self.state["designUrl"] = f"https://example.invalid/{self.prefix}/index.html"
        for path in original.iterdir():
            self.s3.put(Bucket="evidence", Key=f"{self.prefix}/{path.name}", Body=path.read_bytes())
        self.env = patch.dict(os.environ, {"BMA_EVIDENCE_BUCKET": "evidence",
                                          "BMA_PREVIEW_ORIGIN": "https://example.invalid"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_restores_exact_bytes_without_rewriting_provenance_or_publishing(self):
        original = copy.deepcopy(self.state)
        restore_design(self.s3, self.state, self.plan, self.prd, self.root / "restored")
        self.assertEqual(self.state, original)
        for path in (self.root / "original").iterdir():
            self.assertEqual(path.read_bytes(), (self.root / "restored" / path.name).read_bytes())
        self.s3.put_object.assert_not_called()
        self.assertTrue(all(stream.closed for stream in self.s3.streams))

    def test_tampered_artifacts_and_changed_origin_cannot_be_restored(self):
        for name in ("plan.json", "prd.md", "provenance.json", "index.html"):
            key = ("evidence", f"{self.prefix}/{name}")
            original = self.s3.objects[key]
            raw = original[0]
            changed = (json.dumps({**json.loads(raw), "unexpected": True}).encode()
                       if name.endswith(".json") else raw + b"changed")
            self.s3.put(Bucket=key[0], Key=key[1], Body=changed)
            with self.subTest(name=name), self.assertRaises(ValueError):
                restore_design(self.s3, self.state, self.plan, self.prd, self.root / "restored")
            self.s3.objects[key] = original
        with patch.dict(os.environ, {"BMA_PREVIEW_ORIGIN": "https://other.invalid"}), \
                self.assertRaisesRegex(ValueError, "origin"):
            restore_design(self.s3, self.state, self.plan, self.prd, self.root / "restored")
        self.assertFalse((self.root / "restored").exists())


if __name__ == "__main__":
    unittest.main()
