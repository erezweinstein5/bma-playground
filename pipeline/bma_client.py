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
        raise RuntimeError("Stream disconnected without a new successful terminal event; inspect durable items")
