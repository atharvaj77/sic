"""Filesystem loader for skills.

A skill directory looks like::

    skills/
      cdp-mcp/
        SKILL.md          # required
        examples/         # optional, copied verbatim
        scripts/          # optional, copied but NEVER executed

SKILL.md format::

    ---
    name: cdp-mcp
    version: 0.3.0
    description: >
      One-line description used by agents for routing.
    applies_to: [cdp, acquia]
    ---
    # body...
"""

from __future__ import annotations

import io
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML

from sic.skills.schema import Skill, SkillManifest

_FRONT_DELIM = "---"
_SKILL_FILENAME = "SKILL.md"


class SkillLoadError(Exception):
    """Raised when a skill cannot be loaded or its frontmatter is invalid."""

    def __init__(self, path: Path, message: str, original: Exception | None = None) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.original = original


def _yaml() -> YAML:
    y = YAML(typ="safe")
    y.default_flow_style = False
    return y


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md string into (frontmatter_dict, body).

    Raises ValueError if the document does not start with a ``---`` block.
    """
    if not text.startswith(_FRONT_DELIM):
        raise ValueError("SKILL.md must start with a YAML frontmatter block ('---').")

    # Find the closing delimiter on its own line.
    lines = text.splitlines(keepends=True)
    # lines[0] is the opening "---". Scan from line 1 for the closer.
    closer_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n").strip() == _FRONT_DELIM:
            closer_idx = i
            break
    if closer_idx is None:
        raise ValueError("SKILL.md frontmatter block is not closed by a '---' line.")

    fm_text = "".join(lines[1:closer_idx])
    body = "".join(lines[closer_idx + 1 :])
    data = _yaml().load(fm_text) or {}
    if not isinstance(data, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping.")
    return data, body


def dump_frontmatter(manifest: SkillManifest) -> str:
    """Serialize a manifest back into a ``---`` ... ``---`` block (no body)."""
    buf = io.StringIO()
    buf.write(_FRONT_DELIM + "\n")
    data = manifest.model_dump(exclude_none=True, exclude_defaults=False)
    _yaml().dump(data, buf)
    buf.write(_FRONT_DELIM + "\n")
    return buf.getvalue()


def load_skill(skill_dir: str | Path) -> Skill:
    """Load a single skill directory.

    Raises SkillLoadError if the directory is missing SKILL.md, the
    frontmatter is malformed, or the manifest fails schema validation.
    """
    skill_dir = Path(skill_dir)
    md_path = skill_dir / _SKILL_FILENAME
    if not md_path.is_file():
        raise SkillLoadError(skill_dir, f"missing {_SKILL_FILENAME}")

    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError as e:
        raise SkillLoadError(md_path, str(e), e) from e

    try:
        data, body = parse_frontmatter(text)
    except Exception as e:
        raise SkillLoadError(md_path, f"frontmatter parse error: {e}", e) from e

    try:
        manifest = SkillManifest.model_validate(data)
    except ValidationError as e:
        raise SkillLoadError(md_path, f"manifest validation failed:\n{e}", e) from e

    # Directory name should match the manifest name to avoid surprises.
    if skill_dir.name != manifest.name:
        raise SkillLoadError(
            skill_dir,
            f"directory name {skill_dir.name!r} does not match manifest name "
            f"{manifest.name!r}",
        )

    assets = sorted(
        p
        for p in skill_dir.rglob("*")
        if p.is_file() and p != md_path
    )

    return Skill(
        manifest=manifest,
        body=body,
        source_dir=skill_dir,
        assets=assets,
    )


def discover_skills(root: str | Path) -> list[Skill]:
    """Load every skill directly under ``root``.

    Returns an empty list if ``root`` does not exist. Skills are returned
    sorted by name. Raises SkillLoadError on the first invalid skill.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    out: list[Skill] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not (child / _SKILL_FILENAME).is_file():
            continue
        out.append(load_skill(child))
    # Defensive: duplicates can't happen on a real filesystem, but the
    # manifest name could mismatch the dir — load_skill already caught that.
    return out
