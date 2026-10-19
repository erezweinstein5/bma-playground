"""Small SigV4 BMA client. Mutations are never retried automatically."""
import json
import time
from urllib.parse import quote, urlencode
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

PREFIX = "/openai/v1/agents/sessions"
TERMINAL = {"agent.session.turn.completed", "agent.session.turn.failed",
            "agent.session.turn.cancelled"}


def wait_for_session_idle(client, session_id, runtime_arn, *, timeout=45, poll_interval=1):
    """Read-only settling after turn completion; never start or resubmit a turn."""
    deadline = time.monotonic() + timeout
    while True:
        current = client.get_session(session_id)
        status = current.get("status")
        if current.get("environment", {}).get("runtime_arn") != runtime_arn:
            raise RuntimeError("Browser session Runtime identity changed; no retry submitted")
        if current.get("error") or status in {"failed", "cancelled", "deleted"}:
            raise RuntimeError(f"Browser session failed ({status}); no retry submitted")
        if status == "idle":
            return current
        if status not in {"running", "in_progress", "queued", "starting"}:
            raise RuntimeError(f"Unexpected browser session status {status!r}; no retry submitted")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"Browser session is still {status} after {timeout}s. "
                "Its checkpoint is preserved; rerun to collect the same turn. No retry submitted")
        time.sleep(min(poll_interval, remaining))


def sse_events(lines):
    data = []
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if not line:
            if data:
                payload = "\n".join(data)
                data = []
                if payload != "[DONE]":
                    yield json.loads(payload)
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))


class BmaClient:
    def __init__(self, session, region):
        self.session, self.region = session, region
        self.endpoint = f"https://bedrock-mantle.{region}.api.aws"

    def request(self, method, path, body=None, stream=False):
        content = json.dumps(body).encode() if body is not None else b""
        headers = {"Content-Type": "application/json",
                   "Accept": "text/event-stream" if stream else "application/json"}
        request = AWSRequest(method=method, url=self.endpoint + path,
                             data=content, headers=headers)
        SigV4Auth(self.session.get_credentials().get_frozen_credentials(),
                  "bedrock-mantle", self.region).add_auth(request)
        response = requests.request(method, request.url, headers=dict(request.headers),
                                    data=content, stream=stream, timeout=(20, 120))
        if not response.ok:
            detail = response.text[:4000]
            response.close()
            raise RuntimeError(f"BMA {method} {path}: HTTP {response.status_code}: {detail}")
        return response

    def get_session(self, session_id):
        with self.request("GET", PREFIX + "/" + quote(session_id, safe="")) as response:
            return response.json()

    def create(self, configuration):
        with self.request("POST", PREFIX, {**configuration, "stream": False}) as response:
            return response.json()

    def items(self, session_id):
        result, after = [], None
        while True:
            query = {"limit": 100, "order": "asc"}
            if after:
                query["after"] = after
            with self.request("GET", PREFIX + "/" + quote(session_id, safe="") +
                              "/items?" + urlencode(query)) as response:
                page = response.json()
            result.extend(page["data"])
            if not page.get("has_more"):
                return result
            after = page["last_id"]

    def reconcile_turn(self, session_id, previous_turns, deadline, transcript):
        """Recover a disconnected stream without submitting the message again."""
        while time.monotonic() < deadline:
            current = self.get_session(session_id)
            if current.get("error") or current["status"] == "failed":
                raise RuntimeError("BMA session failed while reconciling the stream")
            if current["status"] == "idle":
                items = self.items(session_id)
                new = [item for item in items
                       if item.get("turn_id") and item["turn_id"] not in previous_turns]
                turns = {item["turn_id"] for item in new}
                finals = [item for item in new if item.get("type") == "message"
                          and item.get("role") == "assistant"
                          and item.get("phase") == "final_answer"
                          and item.get("status") == "completed"]
                if len(turns) != 1 or not finals or any(
                        item.get("status") in {"in_progress", "queued", "incomplete"}
                        for item in new):
                    raise RuntimeError("Disconnected stream has no unambiguous completed turn in durable items")
                result = {"turnId": turns.pop(), "completionSource": "durable_items",
                          "finalMessageId": finals[-1]["id"]}
                with open(transcript, "a") as output:
                    output.write(json.dumps({"type": "client.turn.reconciled", **result}) + "\n")
                print(f"Reconciled completed turn {result['turnId']} from durable items.", flush=True)
                return result
            time.sleep(5)
        raise TimeoutError("Turn exceeded the pipeline deadline during stream reconciliation")

    def run_turn(self, session_id, prompt, transcript):
        """Subscribe before sending. Correlate completion with a new turn ID."""
        path = PREFIX + "/" + quote(session_id, safe="") + "/events"
        previous_turns = {item.get("turn_id") for item in self.items(session_id)}
        deadline = time.monotonic() + 25 * 60
        with self.request("GET", path + "?stream=true", stream=True) as stream:
            if "text/event-stream" not in stream.headers.get("content-type", ""):
                raise RuntimeError("BMA did not open an SSE stream")
            with self.request("POST", path, {"events": [{
                "type": "agent.session.input.message",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            }]}):
                pass
            print("Input accepted; waiting for a completed turn.", flush=True)
            with open(transcript, "w") as output:
                try:
                    for event in sse_events(stream.iter_lines(chunk_size=1)):
                        output.write(json.dumps(event) + "\n")
                        output.flush()
                        kind = event.get("type", "")
                        if time.monotonic() > deadline:
                            raise TimeoutError("Turn exceeded the pipeline deadline; inspect before retrying")
                        if kind == "error" or kind.endswith("environment.failed"):
                            raise RuntimeError("BMA execution failed: " + json.dumps(event)[:2000])
                        if kind in TERMINAL:
                            turn_id = (event.get("turn") or {}).get("id") or event.get("turn_id")
                            if not turn_id or turn_id in previous_turns:
                                continue
                            if kind != "agent.session.turn.completed":
                                raise RuntimeError("BMA turn did not complete: " + json.dumps(event)[:2000])
                            print(f"Completed turn {turn_id}", flush=True)
                            return {"turnId": turn_id, "terminalEvent": kind}
                except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
                    print("Event stream disconnected; checking durable results without resubmitting.", flush=True)
        return self.reconcile_turn(session_id, previous_turns, deadline, transcript)
