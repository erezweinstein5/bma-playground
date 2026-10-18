"""Validate a BMA planning result and render isolated, model-authored frames."""
import hashlib
import html
import json
from pathlib import Path
import re

from feature_contract import plan_contract, requirement_ids, validate_journey

MAX_PLAN = 240_000
KINDS = {"wireframe", "overloaded", "balanced", "mobile", "validation"}
AC_IDS = {f"AC-{number}" for number in range(1, 8)}
FRAME_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
             "font-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'")


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _text(value, label, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Invalid {label}")
    return value


def _rows(value, label, minimum=1, maximum=30):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"Invalid {label}")
    return value


def _objects(value, label, minimum=1, maximum=30):
    rows = _rows(value, label, minimum, maximum)
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid {label} entry")
    return rows


def _ac_refs(value, required_ids):
    if (not isinstance(value, list) or not value
            or any(not isinstance(ref, str) for ref in value)
            or len(value) != len(set(value)) or not set(value) <= required_ids):
        raise ValueError("Unknown or missing acceptance criterion reference")


def validate_plan(plan):
    """Schema and traceability checks, not a product/design quality judgment."""
    if not isinstance(plan, dict) or len(canonical_json(plan)) > MAX_PLAN:
        raise ValueError("Invalid or oversized plan")
    version = plan.get("schemaVersion")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unknown plan schema")
    if version == 1 and any(key in plan for key in ("uiContract", "browserJourney")):
        raise ValueError("Legacy plan cannot carry a schema 2 contract or journey")
    for key in ("title", "problem", "approach"):
        _text(plan.get(key), key)
    for key in ("assumptions", "questions", "nonGoals"):
        for entry in _rows(plan.get(key), key, minimum=0):
            _text(entry, key)
    requirements = _objects(plan.get("requirements"), "requirements",
                            minimum=7 if version == 1 else 1,
                            maximum=7 if version == 1 else 50)
    required_ids = set(requirement_ids(plan))
    for entry in requirements:
        for key in ("behavior", "verification"):
            _text(entry.get(key), key)
    for task in _objects(plan.get("tasks"), "tasks"):
        for key in ("title", "detail"):
            _text(task.get(key), key)
        _ac_refs(task.get("acceptanceIds"), required_ids)
        for path in _rows(task.get("paths"), "paths"):
            _text(path, "path", 200)
            prefixes = ("src/", "tests/feature/", "docs/features/") if version == 1 else ("src/",)
            if (not path.startswith(prefixes)
                    or any(part in ("", ".", "..") for part in path.split("/"))
                    or not re.fullmatch(r"[a-zA-Z0-9_./*-]+", path)):
                raise ValueError("Plan requests a path outside the feature policy")
    for step in _objects(plan.get("flow"), "flow", maximum=8):
        _text(step.get("title"), "flow title", 120)
        _text(step.get("detail"), "flow detail", 1000)
    screens = _objects(plan.get("screens"), "screens",
                       minimum=5 if version == 1 else 1,
                       maximum=5 if version == 1 else 8)
    kinds = [screen.get("kind") for screen in screens]
    if version == 1:
        if any(not isinstance(kind, str) for kind in kinds) or set(kinds) != KINDS:
            raise ValueError("Design must include all five required states")
    elif (any(not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", kind)
              for kind in kinds) or len(set(kinds)) != len(kinds)):
        # Kinds are used in parent DOM attributes, IDs and navigation.
        raise ValueError("Design screen kinds must be unique safe identifiers")
    for screen in screens:
        _text(screen.get("title"), "screen title", 120)
        _text(screen.get("caption"), "screen caption", 2000)
        _text(screen.get("html"), "screen HTML", 32_000)
        _text(screen.get("css"), "screen CSS", 12_000)
        _ac_refs(screen.get("acceptanceIds"), required_ids)
        if (type(screen.get("width")) is not int or not 320 <= screen["width"] <= 1440
                or type(screen.get("height")) is not int or not 400 <= screen["height"] <= 1100):
            raise ValueError("Invalid design frame dimensions")
    if version == 2:
        # A proposal blocked on material questions may honestly have no executable
        # contract yet. Validate every supplied contract/journey; only explicit
        # empty values are allowed, and downstream gates refuse question plans.
        if not plan["questions"] or plan.get("uiContract") != "":
            plan_contract(plan)
        if not plan["questions"] or plan.get("browserJourney") != []:
            validate_journey(plan.get("browserJourney"), required_ids)
        # The recording illustrates the primary visual scenario and may cover
        # only a subset of requirements. Independent Playwright acceptance owns
        # complete coverage, including keyboard and other non-recorded behavior.
    return plan


def final_plan(items, turn_id):
    """Use only the completed assistant final answer of the correlated turn."""
    finals = [item for item in items if item.get("turn_id") == turn_id
              and item.get("type") == "message" and item.get("role") == "assistant"
              and item.get("phase") == "final_answer" and item.get("status") == "completed"]
    if len(finals) != 1:
        raise ValueError("Expected exactly one completed planning final answer")
    content = finals[0].get("content", [])
    text = "".join(part.get("text", "") for part in content
                   if part.get("type") in ("output_text", "text"))
    if len(text.encode()) > MAX_PLAN:
        raise ValueError("Oversized planning response")
    # One outer markdown fence is tolerated. Prose or multiple JSON objects are not.
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    return validate_plan(json.loads(text))


def render_board(plan, provenance):
    validate_plan(plan)
    escape = html.escape
    frame_buttons, frames = [], []
    for number, screen in enumerate(plan["screens"], 1):
        kind = screen["kind"]
        frame_buttons.append(
            f'<button class="frame-link" data-frame="{kind}"><span>0{number}</span>'
            f'{escape(screen["title"])}</button>')
        # Model HTML never enters the parent DOM. Opaque-origin sandbox and a
        # first-in-document CSP disable scripts, network requests and navigation.
        # Each generated frame is illustrative; interaction belongs to the viewer.
        child = (f'<!doctype html><html><head><meta http-equiv="Content-Security-Policy" '
                 f'content="{escape(FRAME_CSP, quote=True)}"><meta name="viewport" '
                 f'content="width=device-width, initial-scale=1"><style>'
                 f'html,body{{margin:0;min-height:100%;box-sizing:border-box}}'
                 f'{screen["css"]}</style></head><body>{screen["html"]}</body></html>')
        refs = " · ".join(screen["acceptanceIds"])
        frames.append(
            f'<section class="design-frame" id="frame-{kind}" data-kind="{kind}">'
            f'<div class="frame-heading"><span>0{number} / {escape(kind.upper())}</span>'
            f'<span>{screen["width"]} × {screen["height"]}</span></div>'
            f'<h2>{escape(screen["title"])}</h2>'
            f'<div class="screen-clip" style="--frame-width:{screen["width"]}px;'
            f'--frame-height:{screen["height"]}px">'
            f'<iframe title="{escape(screen["title"], quote=True)}" sandbox="" '
            f'referrerpolicy="no-referrer" width="{screen["width"]}" height="{screen["height"]}" '
            f'srcdoc="{escape(child, quote=True)}"></iframe></div>'
            f'<p class="frame-caption">{escape(screen["caption"])}</p>'
            f'<p class="criterion-tags">{refs}</p></section>')
    flow = "".join(f'<li><b>{escape(step["title"])}</b><p>{escape(step["detail"])}</p></li>'
                   for step in plan["flow"])
    requirements = "".join(
        f'<details><summary>{entry["id"]} <span>{escape(entry["behavior"])}</span></summary>'
        f'<p>{escape(entry["verification"])}</p></details>' for entry in plan["requirements"])
    tasks = "".join(f'<li><b>{escape(task["title"])}</b><p>{escape(task["detail"])}</p>'
                    f'<code>{escape(", ".join(task["paths"]))}</code></li>' for task in plan["tasks"])
    questions = "".join(f'<li>{escape(text)}</li>' for text in plan["questions"])
    assumptions = "".join(f'<li>{escape(text)}</li>' for text in plan["assumptions"])
    non_goals = "".join(f'<li>{escape(text)}</li>' for text in plan["nonGoals"])
    contract_section = ""
    if plan["schemaVersion"] == 2:
        journey = "".join(
            f'<li><b>{escape(step["description"])}</b>'
            f'<pre>{escape(json.dumps(step, ensure_ascii=False, indent=2))}</pre></li>'
            for step in plan["browserJourney"])
        # Contract Markdown remains escaped text; it cannot create parent markup.
        contract_section = (
            '<section id="verification" class="notes"><h2>Observable UI contract</h2>'
            f'<pre>{escape(plan["uiContract"]) if plan["uiContract"] else "Pending material questions."}</pre>'
            '<h3>Proposed browser journey</h3>'
            '<p>Planned actions and assertions; these have not been executed.</p>'
            + (f'<ol>{journey}</ol>' if journey else '<p>Pending material questions.</p>')
            + '</section>')
    meta = {key: escape(str(provenance[key]), quote=True)
            for key in ("eventId", "baseCommit", "prdSha256", "planSha256", "sessionId", "turnId")}
    values = {
        "TITLE": escape(plan["title"]), "PROBLEM": escape(plan["problem"]),
        "APPROACH": escape(plan["approach"]), "FRAME_LINKS": "".join(frame_buttons),
        "FRAMES": "".join(frames), "FLOW": flow, "REQUIREMENTS": requirements,
        "TASKS": tasks, "ASSUMPTIONS": assumptions, "NON_GOALS": non_goals,
        "CONTRACT_SECTION": contract_section,
        "QUESTIONS": questions or "<li>No material questions reported by the planner.</li>",
        **{key.upper(): value for key, value in meta.items()},
    }
    template = Path(__file__).with_name("design_board.html").read_text()
    # One substitution pass: model-authored strings cannot introduce placeholders.
    return re.sub(r"@@([A-Z0-9_]+)@@", lambda match: values[match[1]], template)


def save_board(plan, metadata, directory):
    validate_plan(plan)
    plan_bytes = canonical_json(plan)
    provenance = {**metadata, "planSha256": hashlib.sha256(plan_bytes).hexdigest(),
                  "artifactType": "design-proposal", "schemaVersion": plan["schemaVersion"]}
    rendered = render_board(plan, provenance)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plan.json").write_bytes(plan_bytes)
    (directory / "provenance.json").write_text(json.dumps(provenance, indent=2))
    (directory / "index.html").write_text(rendered)
    return provenance
