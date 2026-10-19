"""Role-scoped skills packaged in the trusted Runtime image."""

SKILLS = {
    "bma-design": "/opt/bma/plugins/bma-design/skills",
    "bma-review": "/opt/bma/plugins/bma-review/skills",
}


def skill_configuration(name):
    """Return discovery and explicit-use settings; never accept task-supplied paths."""
    directory = SKILLS[name]
    path = f"{directory}/{name}/SKILL.md"
    return {
        "capability_directories": [directory],
        "instructions": (
            f"Use the ${name} skill. Read {path} before doing this stage's work. "
            "If that image-owned skill is missing or unreadable, stop and report "
            "the runtime configuration failure; do not substitute repository skills. "
        ),
    }
