"""Trusted preview builds and bounded media publication for BMA browser runs."""
import hashlib
import html
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import shutil
import tarfile
import tempfile
from urllib.parse import urlsplit

from PIL import Image

MAX_CAPTURE = 110 * 1024 * 1024


def browser_tool_configuration():
    return [{
        "type": "mcp", "server_label": "agentcore_browser",
        "transport": {
            "type": "stdio", "command": "/opt/bma/.venv/bin/python",
            "args": ["/opt/bma/tools/browser_mcp.py"], "cwd": "/opt/bma",
            "env": {key: os.environ[key] for key in
                    ("BMA_BROWSER_ID", "BMA_ARTIFACT_BUCKET", "BMA_PREVIEW_ORIGIN", "AWS_REGION")},
            "env_vars": ["AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
                         "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"],
        },
        "allowed_tools": ["browser_open", "browser_snapshot", "browser_action",
                          "browser_assert_text", "browser_screenshot", "browser_finish"],
    }]


def source_digest(repo):
    digest = hashlib.sha256()
    for path in sorted((repo / "src").rglob("*")):
        if path.is_symlink():
            raise ValueError("Source digest rejects symlinks")
        if path.is_file():
            digest.update(path.relative_to(repo).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def preview(s3, assignment, phase, patch=None):
    """Build pinned source in a disposable directory, without cloud credentials."""
    event, base = assignment["eventId"], assignment["baseCommit"]
    if not re.fullmatch(r"(issue|comment|probe)-[0-9a-z-]+", event) or phase not in ("before", "after"):
        raise ValueError("Invalid preview identity")
    origin = os.environ["BMA_PREVIEW_ORIGIN"].rstrip("/")
    if urlsplit(origin).scheme != "https" or urlsplit(origin).path:
        raise ValueError("Preview origin must be an HTTPS origin")
    prefix = f"previews/{event}/{phase}"
    with tempfile.TemporaryDirectory(prefix="bma-preview-") as temporary:
        root = Path(temporary)
        repo = root / "repo"
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(Path.cwd()), str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", base], check=True)
        if patch:
            subprocess.run(["git", "-C", str(repo), "apply", "-"], input=patch, check=True)
        digest = source_digest(repo)
        build_home = root / "home"
        build_home.mkdir()
        env = {"PATH": os.environ["PATH"], "HOME": str(build_home),
               "npm_config_cache": str(root / "npm-cache"), "CI": "true"}
        subprocess.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=repo, env=env, check=True)
        subprocess.run(["npm", "run", "build", "--", f"--base=/{prefix}/"], cwd=repo, env=env, check=True)
        for path in sorted((repo / "dist").rglob("*")):
            if path.is_symlink():
                raise ValueError("Preview output contains a symlink")
            if path.is_file():
                key = prefix + "/" + path.relative_to(repo / "dist").as_posix()
                s3.upload_file(str(path), os.environ["BMA_EVIDENCE_BUCKET"], key, ExtraArgs={
                    "ContentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    "CacheControl": "public,max-age=31536000,immutable"})
    value = {"eventId": event, "phase": phase, "baseCommit": base, "sourceSha256": digest,
             "url": f"{origin}/{prefix}/index.html"}
    s3.put_object(Bucket=os.environ["BMA_ARTIFACT_BUCKET"], Key=f"sources/{event}/browser-{phase}.json",
                  Body=json.dumps(value).encode(), ContentType="application/json")
    print(f"{phase.title()} browser preview: {value['url']}", flush=True)
    return value


def load_json(s3, bucket, key):
    with s3.get_object(Bucket=bucket, Key=key)["Body"] as body:
        raw = body.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Oversized browser metadata")
    return json.loads(raw)


def validate_manifest(value, descriptor, phase):
    for field in ("eventId", "phase", "sourceSha256"):
        if value.get(field) != descriptor[field]:
            raise ValueError(f"Browser evidence does not match preview {field}")
    if value.get("previewUrl") != descriptor["url"]:
        raise ValueError("Browser evidence used a different preview URL")
    expected = "reproduced" if phase == "before" else "passed"
    if value.get("outcome") != expected:
        raise ValueError(f"Browser {phase} outcome was not {expected}; inspect the recording")
    actions = value.get("actions", [])
    if not any(action.get("action") in ("click", "select", "drag", "fill") for action in actions):
        raise ValueError("Browser recording contains no real interaction")
    if not any(action.get("action") == "assert_text" and action.get("passed") is True for action in actions):
        raise ValueError("Browser evidence contains no successful observed-text assertion")
    for field in ("captureSha256", "screenshotSha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", value.get(field, "")):
            raise ValueError("Missing browser artifact digest")


def validate_browser_calls(items, turn_id, descriptor, manifest, first_action=None):
    """Require service-recorded tool results, not just files an agent could write."""
    observed = {}
    interactions = []
    for item in items:
        if (item.get("type") != "mcp_call" or item.get("server_label") != "agentcore_browser"
                or item.get("turn_id") != turn_id or item.get("status") != "completed"):
            continue
        output = item.get("output") or {}
        if item.get("name") == "browser_action":
            arguments = item.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            interactions.append(arguments.get("action"))
        for content in output.get("content", []):
            try:
                value = json.loads(content.get("text", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                observed.setdefault(item["name"], []).append(value)
    if first_action and (not interactions or interactions[0] != first_action):
        raise ValueError(f"Browser recording must begin with a real {first_action} interaction")
    if not any(value.get("browserSessionId") == manifest["browserSessionId"]
               and value.get("url") == descriptor["url"]
               for value in observed.get("browser_open", [])):
        raise ValueError("BMA did not record opening this browser preview")
    if not any(value.get("url") == descriptor["url"] for value in observed.get("browser_action", [])):
        raise ValueError("BMA did not record browser interaction on this preview")
    if not any(value.get("passed") is True for value in observed.get("browser_assert_text", [])):
        raise ValueError("BMA did not record a successful visible-text assertion")
    if not any(all(value.get(key) == manifest[key] for key in
                   ("browserSessionId", "captureSha256", "sourceSha256", "eventId", "phase", "outcome"))
               for value in observed.get("browser_finish", [])):
        raise ValueError("BMA did not record finishing this exact capture")


def unpack_capture(archive, destination):
    """Never extract arbitrary paths, links, or an ffmpeg script from the agent."""
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or len(names) > 4001:
            raise ValueError("Duplicate or excessive capture members")
        if sum(member.size for member in members) > MAX_CAPTURE:
            raise ValueError("Capture exceeds the uncompressed limit")
        for member in members:
            if not member.isfile() or not re.fullmatch(r"(frames\.json|frame-[0-9]{6}\.jpg)", member.name):
                raise ValueError("Unsafe capture member")
            if member.size > 5 * 1024 * 1024:
                raise ValueError("Oversized capture frame")
            with tar.extractfile(member) as source:
                (destination / member.name).write_bytes(source.read())
    index = json.loads((destination / "frames.json").read_text())
    frames, duration = index["frames"], index["duration"]
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 900:
        raise ValueError("Invalid capture duration")
    if not 2 <= len(frames) <= 4000 or set(names) != {"frames.json", *(f["name"] for f in frames)}:
        raise ValueError("Capture index does not match the archive")
    prior = -1
    for number, frame in enumerate(frames):
        stamp = frame["seconds"]
        if frame["name"] != f"frame-{number:06d}.jpg" or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            raise ValueError("Invalid frame index")
        if not 0 <= stamp <= duration or stamp < prior:
            raise ValueError("Non-monotonic frame timestamps")
        prior = stamp
        with Image.open(destination / frame["name"]) as image:
            if image.format != "JPEG" or image.width > 1440 or image.height > 1000:
                raise ValueError("Unexpected capture image")
            image.verify()
    return index


def ffmpeg(*args):
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
                   check=True, timeout=300)


def encode_video(directory, index, output):
    lines = []
    frames = index["frames"]
    for number, frame in enumerate(frames):
        end = frames[number + 1]["seconds"] if number + 1 < len(frames) else index["duration"]
        lines += [f"file '{frame['name']}'", f"duration {max(end - frame['seconds'], 0.04):.4f}"]
    lines.append(f"file '{frames[-1]['name']}'")
    playlist = directory / "frames.txt"
    playlist.write_text("\n".join(lines) + "\n")
    ffmpeg("-f", "concat", "-safe", "1", "-i", str(playlist),
           "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-r", "25",
           "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(output))


def collect_phase(s3, descriptor, destination):
    event, phase = descriptor["eventId"], descriptor["phase"]
    bucket = os.environ["BMA_ARTIFACT_BUCKET"]
    prefix = f"repairs/{event}/browser/{phase}"
    manifest = load_json(s3, bucket, prefix + "/manifest.json")
    validate_manifest(manifest, descriptor, phase)
    destination.mkdir()
    archive = destination / "capture.tar"
    metadata = s3.head_object(Bucket=bucket, Key=prefix + "/capture.tar")
    if metadata["ContentLength"] > MAX_CAPTURE:
        raise ValueError("Oversized browser capture")
    s3.download_file(bucket, prefix + "/capture.tar", str(archive))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["captureSha256"]:
        raise ValueError("Browser capture digest mismatch")
    index = unpack_capture(archive, destination)
    if len(index["frames"]) != manifest["frameCount"]:
        raise ValueError("Frame count does not match evidence manifest")
    screenshot = destination.parent / f"{phase}.png"
    with s3.get_object(Bucket=bucket, Key=prefix + "/result.png")["Body"] as body:
        data = body.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != manifest["screenshotSha256"]:
        raise ValueError("Browser screenshot digest or size mismatch")
    screenshot.write_bytes(data)
    with Image.open(screenshot) as image:
        if image.format != "PNG" or image.width > 1440 or image.height > 1000:
            raise ValueError("Unexpected screenshot")
        image.verify()
    encode_video(destination, index, destination.parent / f"{phase}.mp4")
    return manifest


def publish_evidence(s3, result, before, after, directory):
    destination = directory / "browser-evidence"
    destination.mkdir(exist_ok=False)
    manifests = {phase: collect_phase(s3, descriptor, destination / phase)
                 for phase, descriptor in (("before", before), ("after", after))}
    report = {"schemaVersion": 1, "eventId": result["eventId"], "baseCommit": result["baseCommit"],
              "patchSha256": result["patchSha256"], "bmaSessionId": result["sessionId"],
              "repairTurnId": result["turnId"], "verificationTurnId": result["verificationTurnId"],
              "capture": "Actual Codex MCP interactions in AgentCore Browser",
              "videoTiming": "MP4s preserve capture timing, including the paced native mouse drag.",
              "phases": manifests}
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    prefix = f"evidence/{result['eventId']}/{result['patchSha256'][:16]}"
    origin = os.environ["BMA_PREVIEW_ORIGIN"].rstrip("/")
    report_url = f"{origin}/{prefix}"
    cards = "".join(
        f'<section><h2>{phase.title()}</h2><video controls preload="metadata" poster="{phase}.png" src="{phase}.mp4"></video>'
        f'<p>{html.escape(manifests[phase]["summary"])}</p><a href="{phase}.mp4" download>Download {phase} video</a></section>'
        for phase in ("before", "after"))
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>BMA repair evidence</title><style>body{{font:16px system-ui;background:#101828;color:#f9fafb;max-width:1400px;margin:40px auto;padding:24px}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:24px}}video,img{{width:100%;border-radius:12px}}a{{color:#84caff}}code{{overflow-wrap:anywhere}}</style>
<h1>Codex browser verification</h1><p>Actual AgentCore Browser interactions for <code>{html.escape(result["eventId"])}</code>.</p>
<main>{cards}</main><p><a href="report.json">Verification report and artifact identity</a></p>
<p>Base revision: <code>{result["baseCommit"]}</code><br>Patch SHA-256: <code>{result["patchSha256"]}</code></p></html>"""
    (destination / "index.html").write_text(page)
    for name in ("before.mp4", "after.mp4", "before.png", "after.png", "report.json", "index.html"):
        path = destination / name
        s3.upload_file(str(path), os.environ["BMA_EVIDENCE_BUCKET"], f"{prefix}/{name}", ExtraArgs={
            "ContentType": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "CacheControl": "public,max-age=31536000,immutable"})
    # Raw frame bundles are already durable in private S3, not GitHub artifacts.
    for phase in ("before", "after"):
        shutil.rmtree(destination / phase)
    return {"url": report_url, "before": manifests["before"]["summary"], "after": manifests["after"]["summary"],
            "browserSessions": {phase: value["browserSessionId"] for phase, value in manifests.items()}}


def evidence_markdown(evidence):
    url = evidence["url"]
    return (
        "\n\n## Browser evidence\n\n"
        "Codex reproduced the issue and verified the repair using AgentCore Browser. "
        "These recordings show the agent's actual browser interactions. "
        "Independent CI checks remain the merge gate.\n\n"
        f"[Watch or download the full recordings]({url}/index.html) · [Verification report]({url}/report.json)\n\n"
        "| Before | After |\n| --- | --- |\n"
        f"| ![Before]({url}/before.png) | ![After]({url}/after.png) |\n"
        f"| [Download before MP4]({url}/before.mp4) | [Download after MP4]({url}/after.mp4) |\n\n"
        + "\n".join(f"- {phase.title()} browser session: `{session}`"
                    for phase, session in evidence["browserSessions"].items())
    )
