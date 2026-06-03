"""ChatGPT memory export → sic personal-profile importer.

The ChatGPT data export (Settings → Data controls → Export data) ships
the "Saved memories" feature as a JSON file whose exact shape has shifted
over time. We accept three concrete shapes (in priority order) and
normalize them all to a list of bullet strings:

  1. `{"memories": [{"content": "..."}, ...]}`          (current)
  2. `{"saved_memories": ["...", "..."]}`               (older)
  3. `["...", "..."]`                                   (raw list)

Classification — turning each bullet into a (section, key, value) entry —
runs in one of two modes:

  - **Rule mode** (default, no network): a hand-tuned keyword router.
    Good enough for ~80% of typical bullets; everything else lands in
    `free_form`. No external dependency.
  - **LLM mode** (`--use-llm`): the Anthropic SDK is imported lazily and
    asked to classify each bullet. Falls back to rule mode if the SDK
    isn't installed or `ANTHROPIC_API_KEY` is missing.

The output is always a *draft* personal profile with `merge=replace`,
`priority=30`, and the original bullet kept as the `rationale`, so the
user can review and edit before saving.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .schema import Entry, MergeStrategy, Profile, ProfileMeta, Scope, Section


class ImportError_(Exception):
    """Raised when the export file is unparseable or has no recognised shape."""


# ---------- normalize ChatGPT export shapes ----------

def parse_export(path: Path) -> list[str]:
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ImportError_(f"{path}: not valid JSON ({e})") from e

    bullets: list[str] = []
    if isinstance(raw, dict) and isinstance(raw.get("memories"), list):
        for item in raw["memories"]:
            if isinstance(item, str):
                bullets.append(item)
            elif isinstance(item, dict) and isinstance(item.get("content"), str):
                bullets.append(item["content"])
    elif isinstance(raw, dict) and isinstance(raw.get("saved_memories"), list):
        bullets = [s for s in raw["saved_memories"] if isinstance(s, str)]
    elif isinstance(raw, list):
        bullets = [s for s in raw if isinstance(s, str)]
    else:
        raise ImportError_(
            f"{path}: unrecognised export shape (expected `memories`, "
            f"`saved_memories`, or a list of strings)"
        )

    # Drop empties and trim whitespace.
    return [b.strip() for b in bullets if b and b.strip()]


# ---------- classification: rule-based router ----------

_SECTION_HINTS: list[tuple[Section, tuple[str, ...]]] = [
    # Order matters — more specific buckets are checked before generic ones.
    (Section.DO_NOT, ("never ", "do not ", "don't ", "avoid ", "forbid")),
    (Section.TOOLS, ("vscode", "neovim", "vim ", "emacs", "pycharm", "jupyter", "docker", "kubernetes", "polars", "pandas", "duckdb", "postgres", "snowflake", "dbt", "airflow")),
    (Section.LANGUAGES, ("python", "rust", "typescript", "javascript", "go ", "java ", "sql ", "kotlin", "swift", "c++", "c#")),
    (Section.CODING_STYLE, ("indent", "tabs", "spaces", "type hint", "typing", "docstring", "naming", "format", "lint", "style", "prefer ")),
    (Section.WORKFLOW, ("git ", "commit", "branch", "pr ", "pull request", "ci/cd", "review", "testing", "tests")),
    (Section.DOMAIN_KNOWLEDGE, ("metric", "kpi", "definition", "glossary", "means ", "stands for")),
    (Section.IDENTITY, ("i am ", "i'm ", "my name", "i work", "my team", "my role")),
]


@dataclass(frozen=True)
class Classification:
    section: Section
    key: str
    value: Any


def _slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s[:max_len].rstrip("_")) or "note"


def classify_rule(bullet: str) -> Classification:
    """Route a bullet to a section via keyword hints. Always returns something."""
    lower = bullet.lower()
    for section, hints in _SECTION_HINTS:
        if any(hint in lower for hint in hints):
            return Classification(section=section, key=_slugify(bullet), value=bullet)
    return Classification(section=Section.FREE_FORM, key=_slugify(bullet), value=bullet)


# ---------- classification: optional LLM mode ----------

def classify_llm(bullets: list[str], model: str = "claude-sonnet-4-20250514") -> list[Classification]:
    """LLM-mediated classification. Lazy-imports the Anthropic SDK.

    Falls back to rule classification if the SDK isn't installed or no API
    key is set, so users can opt-in safely.
    """
    if "ANTHROPIC_API_KEY" not in os.environ:
        return [classify_rule(b) for b in bullets]
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        return [classify_rule(b) for b in bullets]

    client = anthropic.Anthropic()
    sections = ", ".join(s.value for s in Section)
    prompt = (
        "Classify each bullet into one of these sections: "
        f"{sections}. Reply with a JSON array of objects "
        '{"section": "...", "key": "snake_case_slug"} '
        "in the same order as the bullets. No commentary.\n\n"
        + "\n".join(f"{i + 1}. {b}" for i, b in enumerate(bullets))
    )
    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
    try:
        decisions = json.loads(text)
    except json.JSONDecodeError:
        return [classify_rule(b) for b in bullets]

    out: list[Classification] = []
    for bullet, decision in zip(bullets, decisions, strict=False):
        try:
            section = Section(decision["section"])
            key = _slugify(decision.get("key", bullet))
        except (KeyError, ValueError):
            out.append(classify_rule(bullet))
            continue
        out.append(Classification(section=section, key=key, value=bullet))
    return out


# ---------- draft profile assembly ----------

def _disambiguate_keys(classifications: list[Classification]) -> list[Classification]:
    """Ensure (section, key) is unique by appending _2, _3, ... on collisions."""
    seen: dict[tuple[str, str], int] = {}
    out: list[Classification] = []
    for c in classifications:
        ident = (c.section.value, c.key)
        if ident not in seen:
            seen[ident] = 1
            out.append(c)
        else:
            seen[ident] += 1
            new_key = f"{c.key}_{seen[ident]}"
            out.append(Classification(section=c.section, key=new_key, value=c.value))
    return out


def build_draft(
    classifications: list[Classification],
    owner: str,
    today: date | None = None,
) -> Profile:
    today = today or date.today()
    classifications = _disambiguate_keys(classifications)
    entries = [
        Entry(
            section=c.section,
            key=c.key,
            value=c.value,
            merge=MergeStrategy.REPLACE,
            priority=30,
            rationale=f"imported from ChatGPT memory: {c.value!r}",
        )
        for c in classifications
    ]
    return Profile(
        schema_version=1,
        profile=ProfileMeta(scope=Scope.PERSONAL, owner=owner, updated_at=today),
        entries=entries,
    )


def import_chatgpt(
    export_path: Path,
    owner: str,
    use_llm: bool = False,
    today: date | None = None,
) -> Profile:
    bullets = parse_export(export_path)
    if not bullets:
        raise ImportError_(f"{export_path}: no memory bullets found")
    classifications = (
        classify_llm(bullets) if use_llm else [classify_rule(b) for b in bullets]
    )
    return build_draft(classifications, owner=owner, today=today)
