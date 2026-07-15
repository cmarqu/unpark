"""Forgiving parser for the small WELCOME.md convention."""

import re

from .model import Recipe, Section, Welcome


_META_BULLET = re.compile(r"^[-*]\s+([A-Za-z][\w-]*):\s*(.+?)\s*$")


def parse_welcome(text: str) -> Welcome:
    """Parse WELCOME.md; unknown keys and sections pass through."""
    lines = text.splitlines()
    index = 0
    meta = {}

    if lines and lines[0].strip() == "---":
        for end in range(1, len(lines)):
            if lines[end].strip() == "---":
                for raw in lines[1:end]:
                    raw = raw.split("#", 1)[0]
                    if ":" in raw:
                        key, value = raw.split(":", 1)
                        if key.strip():
                            meta[key.strip()] = value.strip()
                index = end + 1
                break

    sections = []
    title, body = None, []
    for line in lines[index:]:
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            if title is not None:
                sections.append(Section(title, "\n".join(body).strip()))
            title, body = match.group(1), []
        elif title is not None:
            body.append(line)
    if title is not None:
        sections.append(Section(title, "\n".join(body).strip()))

    recipes = []
    for section in sections:
        if section.title.strip().lower() == "recipes":
            recipes = parse_recipes(section.body)
    return Welcome(meta=meta, sections=sections, recipes=recipes)


def parse_recipes(body: str) -> list:
    recipes = []
    current = None
    fence = None
    description_done = False

    for line in body.splitlines():
        match = re.match(r"^###\s+(.+?)\s*$", line)
        if match and fence is None:
            current = Recipe(name=match.group(1))
            recipes.append(current)
            description_done = False
            continue
        if current is None:
            continue
        if fence is not None:
            if line.strip().startswith("```"):
                current.steps.append("\n".join(fence).strip())
                fence = None
            else:
                fence.append(line)
            continue
        if line.strip().startswith("```"):
            fence = []
            description_done = True
            continue
        metadata = _META_BULLET.match(line.strip())
        if metadata:
            current.meta[metadata.group(1)] = metadata.group(2)
            description_done = True
            continue
        if not description_done and line.strip():
            current.description = (
                current.description + " " + line.strip()
            ).strip()
    return recipes
