"""Install resolved skills into agent-specific target layouts.

A :class:`SkillTarget` knows where to write each skill on disk and which
files it manages (so we can remove orphans without touching unrelated
files). Each target writes a small ``.sic-manifest.json`` marker so we
only ever delete files we previously installed.

Built-in targets:

- ``claude`` → ``~/.claude/skills/<name>/SKILL.md`` plus any assets.
- ``copilot`` → ``<project>/.github/prompts/<name>.skill.md`` (single file).
- ``generic`` → ``<project>/.ai/skills/<name>/`` (verbatim copy).

Writes are idempotent: files are only rewritten when their bytes change,
so timestamps stay stable for unaffected skills.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from sic.skills.resolver import SkillSet
from sic.skills.schema import Skill

_MANIFEST_FILENAME = ".sic-manifest.json"


@dataclass
class InstallReport:
    """Summary of a single ``install`` run."""

    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)

    def merge(self, other: "InstallReport") -> None:
        self.written.extend(other.written)
        self.unchanged.extend(other.unchanged)
        self.removed.extend(other.removed)


def _write_if_changed(path: Path, data: bytes, report: InstallReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        report.unchanged.append(path)
        return
    path.write_bytes(data)
    report.written.append(path)


def _read_manifest(root: Path) -> dict[str, list[str]]:
    """Return previously-installed files keyed by skill name."""
    f = root / _MANIFEST_FILENAME
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, list):
            out[k] = [str(x) for x in v]
    return out


def _write_manifest(root: Path, files_by_skill: dict[str, list[Path]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        name: sorted(str(p.relative_to(root)) for p in paths)
        for name, paths in files_by_skill.items()
    }
    (root / _MANIFEST_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )


class SkillTarget(ABC):
    """Adapter that maps a resolved skill set onto a specific agent layout."""

    name: str  # short identifier used in config (e.g. "claude")

    @abstractmethod
    def root(self, *, project_root: Path | None) -> Path:
        """Directory we own — used for the manifest + orphan sweep."""

    @abstractmethod
    def files_for(self, skill: Skill, root: Path) -> list[tuple[Path, bytes]]:
        """Return (path, bytes) pairs to write for this skill."""


class ClaudeTarget(SkillTarget):
    """Writes ``~/.claude/skills/<name>/SKILL.md`` + assets."""

    name = "claude"

    def __init__(self, home: Path | None = None) -> None:
        self._home = home or Path.home()

    def root(self, *, project_root: Path | None = None) -> Path:
        return self._home / ".claude" / "skills"

    def files_for(self, skill: Skill, root: Path) -> list[tuple[Path, bytes]]:
        dest = root / skill.name
        out: list[tuple[Path, bytes]] = [
            (dest / "SKILL.md", skill.to_skill_md().encode("utf-8"))
        ]
        for asset in skill.assets:
            rel = asset.relative_to(skill.source_dir)
            out.append((dest / rel, asset.read_bytes()))
        return out


class CopilotTarget(SkillTarget):
    """Writes ``<project>/.github/prompts/<name>.skill.md`` (single file)."""

    name = "copilot"

    def root(self, *, project_root: Path | None) -> Path:
        if project_root is None:
            raise ValueError("CopilotTarget requires project_root")
        return project_root / ".github" / "prompts"

    def files_for(self, skill: Skill, root: Path) -> list[tuple[Path, bytes]]:
        return [(root / f"{skill.name}.skill.md", skill.to_skill_md().encode("utf-8"))]


class GenericTarget(SkillTarget):
    """Writes ``<project>/.ai/skills/<name>/`` verbatim (SKILL.md + assets)."""

    name = "generic"

    def root(self, *, project_root: Path | None) -> Path:
        if project_root is None:
            raise ValueError("GenericTarget requires project_root")
        return project_root / ".ai" / "skills"

    def files_for(self, skill: Skill, root: Path) -> list[tuple[Path, bytes]]:
        dest = root / skill.name
        out: list[tuple[Path, bytes]] = [
            (dest / "SKILL.md", skill.to_skill_md().encode("utf-8"))
        ]
        for asset in skill.assets:
            rel = asset.relative_to(skill.source_dir)
            out.append((dest / rel, asset.read_bytes()))
        return out


_BUILTIN: dict[str, type[SkillTarget]] = {
    "claude": ClaudeTarget,
    "copilot": CopilotTarget,
    "generic": GenericTarget,
}


def build_target(name: str, *, home: Path | None = None) -> SkillTarget:
    """Construct a built-in target by short name."""
    try:
        cls = _BUILTIN[name]
    except KeyError as e:
        raise ValueError(
            f"unknown skill target {name!r}; known: {sorted(_BUILTIN)}"
        ) from e
    if cls is ClaudeTarget:
        return ClaudeTarget(home=home)
    return cls()


def install(
    skill_set: SkillSet,
    target: SkillTarget,
    *,
    project_root: Path | None = None,
) -> InstallReport:
    """Install a resolved ``SkillSet`` into a single ``target``.

    Idempotent: rewrites only files whose bytes changed. Removes files that
    were installed by a previous run for skills no longer present
    (orphan sweep), as recorded in ``.sic-manifest.json`` under ``target.root``.
    """
    root = target.root(project_root=project_root)
    report = InstallReport()

    previous = _read_manifest(root)
    new_files: dict[str, list[Path]] = {}

    for inst in skill_set.installed:
        pairs = target.files_for(inst.skill, root)
        new_files[inst.name] = [p for p, _ in pairs]
        for path, data in pairs:
            _write_if_changed(path, data, report)

    # Orphan sweep: drop files for skills no longer installed, plus any
    # previously-tracked file that we did not just rewrite (e.g. an asset
    # was removed from the skill).
    new_paths = {p for paths in new_files.values() for p in paths}
    for prev_skill, rel_paths in previous.items():
        keep = new_skill_paths(new_files, prev_skill)
        for rel in rel_paths:
            abs_path = root / rel
            if abs_path in keep or abs_path in new_paths:
                continue
            if abs_path.is_file():
                abs_path.unlink()
                report.removed.append(abs_path)
                _prune_empty_parents(abs_path.parent, stop=root)

    _write_manifest(root, new_files)
    return report


def new_skill_paths(new_files: dict[str, list[Path]], name: str) -> set[Path]:
    return set(new_files.get(name, []))


def _prune_empty_parents(path: Path, *, stop: Path) -> None:
    """Remove empty parent dirs up to (but not including) ``stop``."""
    cur = path
    while cur != stop and cur.is_dir() and not any(cur.iterdir()):
        try:
            cur.rmdir()
        except OSError:
            return
        cur = cur.parent


def install_all(
    skill_set: SkillSet,
    targets: list[SkillTarget],
    *,
    project_root: Path | None = None,
) -> InstallReport:
    """Install across every target, returning a merged report."""
    combined = InstallReport()
    for t in targets:
        combined.merge(install(skill_set, t, project_root=project_root))
    return combined


__all__ = [
    "ClaudeTarget",
    "CopilotTarget",
    "GenericTarget",
    "InstallReport",
    "SkillTarget",
    "build_target",
    "install",
    "install_all",
]
