"""Image-owned MCP tools: Codex drives AgentCore Browser; Actions publishes evidence.

Only the trusted preview descriptor in sources/ supplies navigation URLs. No
arbitrary JavaScript, shell execution, or production navigation is exposed.
STDIO is newline-delimited JSON-RPC; diagnostics belong on stderr.
"""
import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import time
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from bedrock_agentcore.tools.browser_client import BrowserClient
from playwright.async_api import async_playwright

EVENT_PATTERN = r"(issue|comment|probe)-[0-9a-z-]+"
WIDTH, HEIGHT = 1440, 1000
MAX_FRAMES, MAX_BYTES = 4000, 100 * 1024 * 1024


def validate_descriptor(value, event, phase, origin):
    if not re.fullmatch(EVENT_PATTERN, event) or phase not in ("before", "after"):
        raise ValueError("Invalid event or phase")
    expected = f"{origin.rstrip('/')}/previews/{event}/{phase}/index.html"
    if value.get("eventId") != event or value.get("phase") != phase or value.get("url") != expected:
        raise ValueError("Preview descriptor does not match the requested event and phase")
    if not re.fullmatch(r"[0-9a-f]{64}", value.get("sourceSha256", "")):
        raise ValueError("Preview descriptor has no source digest")
    if urlsplit(expected).scheme != "https":
        raise ValueError("Preview requires HTTPS")
    return expected


class BrowserTools:
    def __init__(self):
        self.region = os.environ.get("AWS_REGION", "us-east-1")
        self.bucket = os.environ["BMA_ARTIFACT_BUCKET"]
        self.origin = os.environ["BMA_PREVIEW_ORIGIN"].rstrip("/")
        self.identifier = os.environ["BMA_BROWSER_ID"]
        self.s3 = boto3.Session(region_name=self.region).client(
            "s3", config=Config(retries={"mode": "standard", "max_attempts": 4}))
        self.client = self.playwright = self.browser = self.page = self.cdp = None
        self.frames, self.actions = [], []
        self.frame_bytes = 0
        self.recording_error = None
        self.started = 0
        self.directory = None

    def put_json(self, name, value):
        self.s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}/{name}",
                          Body=json.dumps(value).encode(), ContentType="application/json")

    def read_descriptor(self, event, phase):
        response = self.s3.get_object(Bucket=self.bucket, Key=f"sources/{event}/browser-{phase}.json")
        with response["Body"] as body:
            return json.loads(body.read(64 * 1024))

    def add_action(self, kind, **details):
        action = {"seconds": round(time.monotonic() - self.started, 3), "action": kind, **details}
        self.actions.append(action)
        return action

    async def snapshot(self):
        if not self.page:
            raise ValueError("Open a preview first")
        # Read-only DOM data lets the agent choose locators from observed content.
        controls = await self.page.locator(
            "button,select,input,[draggable=true],[data-column]").evaluate_all(
            """els => els.map(e => ({tag:e.tagName.toLowerCase(), id:e.id,
              label:e.getAttribute('aria-label'), text:(e.innerText||'').slice(0,160),
              task:e.getAttribute('data-task-id'), column:e.getAttribute('data-column'),
              options:e.tagName==='SELECT' ? [...e.options].map(o=>({value:o.value,text:o.text})) : undefined
            }))""")
        return {"url": self.page.url, "snapshot": await self.page.locator("body").aria_snapshot(),
                "controls": controls}

    async def frame(self, event):
        try:
            if len(self.frames) >= MAX_FRAMES or self.frame_bytes >= MAX_BYTES:
                self.recording_error = "Recording exceeded the bounded capture size"
                return
            data = base64.b64decode(event["data"], validate=True)
            name = f"frame-{len(self.frames):06d}.jpg"
            (self.directory / name).write_bytes(data)
            self.frames.append({"name": name, "seconds": round(time.monotonic() - self.started, 4)})
            self.frame_bytes += len(data)
        except Exception as error:
            self.recording_error = str(error)
        finally:
            if self.cdp:
                await self.cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    async def open(self, event, phase):
        if self.client:
            raise ValueError("Finish the current recording before opening another")
        self.descriptor = await asyncio.to_thread(self.read_descriptor, event, phase)
        self.url = validate_descriptor(self.descriptor, event, phase, self.origin)
        self.prefix = f"repairs/{event}/browser/{phase}"
        self.directory = Path("/mnt/home/workspace/browser-evidence") / event / phase
        if self.directory.exists():
            raise ValueError("A recording already exists for this event/phase; do not overwrite evidence")
        self.directory.mkdir(parents=True)
        self.frames, self.actions = [], []
        self.frame_bytes, self.recording_error = 0, None
        self.event, self.phase = event, phase
        try:
            self.client = BrowserClient(region=self.region)
            await asyncio.to_thread(self.client.start, identifier=self.identifier,
                                    name=f"{event}-{phase}", session_timeout_seconds=900,
                                    viewport={"width": WIDTH, "height": HEIGHT})
            ws_url, headers = self.client.generate_ws_headers()
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.connect_over_cdp(ws_url, headers=headers)
            self.context = self.browser.contexts[0]
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.page.set_default_timeout(15000)
            await self.page.set_viewport_size({"width": WIDTH, "height": HEIGHT})
            preview_prefix = self.url.rsplit("/", 1)[0] + "/"

            async def restrict(route):
                if route.request.url.startswith(preview_prefix):
                    await route.continue_()
                else:
                    await route.abort()

            await self.context.route("**/*", restrict)
            self.started = time.monotonic()
            self.cdp = await self.context.new_cdp_session(self.page)
            self.cdp.on("Page.screencastFrame", self.frame)
            await self.cdp.send("Page.startScreencast", {
                "format": "jpeg", "quality": 80, "maxWidth": WIDTH, "maxHeight": HEIGHT})
            live = {"eventId": event, "phase": phase, "browserId": self.identifier,
                    "browserSessionId": self.client.session_id, "region": self.region}
            await asyncio.to_thread(self.put_json, "live.json", live)
            response = await self.page.goto(self.url, wait_until="networkidle")
            if not response or response.status != 200:
                raise RuntimeError("Preview did not return HTTP 200")
            await self.caption()
            self.add_action("open", **live, previewUrl=self.url)
            return {**live, **await self.snapshot(), "recording": "started"}
        except Exception:
            await self.close()
            raise

    async def caption(self):
        await self.page.evaluate("""phase => {
          document.querySelector('#bma-evidence-caption')?.remove();
          const e=document.createElement('div'); e.id='bma-evidence-caption';
          e.textContent=phase==='before' ? 'BEFORE  ·  Codex reproducing the issue' : 'AFTER  ·  Codex verifying the repair';
          e.style.cssText='position:fixed;bottom:12px;left:50%;transform:translateX(-50%);z-index:2147483647;background:#101828;color:white;padding:10px 24px;border-radius:999px;font:600 16px system-ui;pointer-events:none';
          document.body.appendChild(e);
        }""", self.phase)

    async def highlight(self, locator):
        await locator.wait_for(state="visible")
        box = await locator.bounding_box()
        if box:
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            await self.page.mouse.move(x, y, steps=8)
            await locator.evaluate("""e => {
              e.animate([{outline:'4px solid #f79009',outlineOffset:'4px'},
                         {outline:'4px solid transparent',outlineOffset:'8px'}],
                        {duration:900});
            }""")
            await self.page.wait_for_timeout(250)

    async def interact(self, action, selector="", value="", target=""):
        if not self.page:
            raise ValueError("Open a preview first")
        if action == "reload":
            await self.page.reload(wait_until="networkidle")
            await self.caption()
        else:
            locator = self.page.locator(selector)
            if await locator.count() != 1:
                raise ValueError("Selector must identify exactly one observed element")
            await self.highlight(locator)
            if action == "click":
                await locator.click()
            elif action == "select":
                await locator.select_option(value)
            elif action == "fill":
                await locator.fill(value)
            elif action == "drag":
                destination = self.page.locator(target)
                if await destination.count() != 1:
                    raise ValueError("Drag target must identify exactly one element")
                await locator.drag_to(destination, target_position={"x": 30, "y": 45})
            else:
                raise ValueError("Unknown browser action")
        self.add_action(action, selector=selector, value=value, target=target)
        await self.page.wait_for_timeout(400)
        return await self.snapshot()

    async def assert_text(self, selector, expected):
        actual = await self.page.locator(selector).inner_text()
        result = self.add_action("assert_text", selector=selector, expected=expected,
                                 actual=actual, passed=actual.strip() == expected)
        return result

    async def screenshot(self):
        data = await self.page.screenshot(type="png")
        return {"type": "image", "mimeType": "image/png", "data": base64.b64encode(data).decode()}

    async def finish(self, outcome, summary):
        if outcome not in ("reproduced", "not_reproduced", "passed", "failed"):
            raise ValueError("Invalid observed outcome")
        if not self.page:
            raise ValueError("No browser recording is active")
        try:
            self.add_action("finish", outcome=outcome, summary=summary[:4000])
            await self.page.wait_for_timeout(1500)
            screenshot = self.directory / "result.png"
            await self.page.screenshot(path=str(screenshot), type="png")
            await self.cdp.send("Page.stopScreencast")
            await asyncio.sleep(0.2)
            duration = round(time.monotonic() - self.started, 4)
            if self.recording_error or len(self.frames) < 2:
                raise RuntimeError(self.recording_error or "Browser did not produce a usable recording")
            index = {"frames": self.frames, "duration": duration}
            (self.directory / "frames.json").write_text(json.dumps(index))
            archive = self.directory / "capture.tar"
            with tarfile.open(archive, "w") as tar:
                for name in ["frames.json", *(frame["name"] for frame in self.frames)]:
                    tar.add(self.directory / name, arcname=name, recursive=False)
            capture_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            await asyncio.to_thread(self.s3.upload_file, str(archive), self.bucket,
                                    f"{self.prefix}/capture.tar", ExtraArgs={"ContentType": "application/x-tar"})
            await asyncio.to_thread(self.s3.upload_file, str(screenshot), self.bucket,
                                    f"{self.prefix}/result.png", ExtraArgs={"ContentType": "image/png"})
            manifest = {"schemaVersion": 1, "eventId": self.event, "phase": self.phase,
                        "browserId": self.identifier, "browserSessionId": self.client.session_id,
                        "sourceSha256": self.descriptor["sourceSha256"], "previewUrl": self.url,
                        "outcome": outcome, "summary": summary[:4000], "actions": self.actions,
                        "captureSha256": capture_sha, "duration": duration,
                        "frameCount": len(self.frames), "screenshotSha256": hashlib.sha256(screenshot.read_bytes()).hexdigest()}
            await asyncio.to_thread(self.put_json, "manifest.json", manifest)
            return manifest
        finally:
            await self.close()

    async def close(self):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            try:
                if self.playwright:
                    await self.playwright.stop()
            finally:
                if self.client:
                    await asyncio.to_thread(self.client.stop)
                self.client = self.playwright = self.browser = self.page = self.cdp = None


def schema(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TEXT = {"type": "string"}
TOOLS = [
    {"name": "browser_open", "description": "Open the trusted before/after preview in AgentCore Browser and start recording. Returns the observed page and browser session ID.",
     "inputSchema": schema({"event": TEXT, "phase": {"type": "string", "enum": ["before", "after"]}}, ["event", "phase"])},
    {"name": "browser_snapshot", "description": "Read the current accessibility snapshot and available controls.", "inputSchema": schema({}, [])},
    {"name": "browser_action", "description": "Perform one real browser interaction using CSS selectors from observed controls. Reload takes no selector.",
     "inputSchema": schema({"action": {"type": "string", "enum": ["click", "select", "fill", "drag", "reload"]},
                            "selector": TEXT, "value": TEXT, "target": TEXT}, ["action"])},
    {"name": "browser_assert_text", "description": "Compare visible text with the expected value and record the actual result. Does not modify the page.",
     "inputSchema": schema({"selector": TEXT, "expected": TEXT}, ["selector", "expected"])},
    {"name": "browser_screenshot", "description": "Inspect a screenshot of the current real browser page.", "inputSchema": schema({}, [])},
    {"name": "browser_finish", "description": "Record the observed outcome, save the video frames and final screenshot to private S3, and stop the browser session. Call even if the functionality fails.",
     "inputSchema": schema({"outcome": {"type": "string", "enum": ["reproduced", "not_reproduced", "passed", "failed"]},
                            "summary": TEXT}, ["outcome", "summary"])},
]


async def serve():
    tools = BrowserTools()
    methods = {"browser_open": tools.open, "browser_snapshot": tools.snapshot,
               "browser_action": tools.interact, "browser_assert_text": tools.assert_text,
               "browser_screenshot": tools.screenshot, "browser_finish": tools.finish}
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            request = json.loads(line)
            if "id" not in request:
                continue
            method = request.get("method")
            response = {"jsonrpc": "2.0", "id": request["id"]}
            if method == "initialize":
                response["result"] = {"protocolVersion": request["params"]["protocolVersion"],
                                      "capabilities": {"tools": {}},
                                      "serverInfo": {"name": "bma-agentcore-browser", "version": "1.0.0"}}
            elif method == "ping":
                response["result"] = {}
            elif method == "tools/list":
                response["result"] = {"tools": TOOLS}
            elif method == "tools/call":
                try:
                    params = request["params"]
                    value = await methods[params["name"]](**params.get("arguments", {}))
                    content = value if params["name"] == "browser_screenshot" else {"type": "text", "text": json.dumps(value)}
                    response["result"] = {"content": [content], "isError": False}
                except Exception as error:
                    response["result"] = {"content": [{"type": "text", "text": str(error)}], "isError": True}
            else:
                response["error"] = {"code": -32601, "message": "Method not found"}
            print(json.dumps(response), flush=True)
    finally:
        await tools.close()


if __name__ == "__main__":
    asyncio.run(serve())
