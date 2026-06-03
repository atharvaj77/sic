"""Local sic workspace layout + git operations.

Layout (under `~/.sic/` by default; override via `$SIC_HOME` for tests):

    ~/.sic/
    ├── config.json          # team_repo URL, team_profile_path (relative)
    ├── team/                # git clone of the team profile repo
    │   └── team.yaml        # the team profile, by convention
    └── personal.yaml        # the local user's personal overlay

`sic init --team-repo <url>` populates this layout. `sic sync` does
`git pull` in `team/`, runs the merger, and re-renders CLAUDE.md.

Git is shelled out via `subprocess` — no GitPython dependency. The
wrapper functions raise `GitError` (subclass of RuntimeError) with the
stderr captured, so the CLI can show a clean message.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


# ---------- paths ----------

def sic_home() -> Path:
    """Root of the local sic workspace. Honours `$SIC_HOME` for tests."""
    override = os.environ.get("SIC_HOME")
    return Path(override) if override else Path.home() / ".sic"


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def team_dir(self) -> Path:
        return self.root / "team"

    @property
    def personal_file(self) -> Path:
        return self.root / "personal.yaml"

    def team_profile_path(self, relative: str) -> Path:
        return self.team_dir / relative

    @classmethod
    def current(cls) -> "Workspace":
        return cls(sic_home())


# ---------- config ----------

@dataclass
class Config:
    team_repo: str
    team_profile_path: str = "team.yaml"  # relative to the cloned repo

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = json.loads(path.read_text())
        return cls(**data)


# ---------- git ----------

def _git(args: list[str], cwd: Path | None = None) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise GitError("git executable not found on PATH") from e
    except subprocess.CalledProcessError as e:
        raise GitError(f"git {' '.join(args)} failed: {e.stderr.strip()}") from e
    return out.stdout.strip()


def git_clone(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise GitError(f"clone target already exists: {dest}")
    _git(["clone", "--depth", "1", url, str(dest)])


def git_pull(repo: Path) -> str:
    if not (repo / ".git").exists():
        raise GitError(f"not a git repository: {repo}")
    return _git(["pull", "--ff-only"], cwd=repo)


def git_head_sha(repo: Path) -> str:
    return _git(["rev-parse", "--short", "HEAD"], cwd=repo)


# ---------- personal profile scaffold ----------

PERSONAL_STUB = """\
# Personal overlay for sharing-is-caring. Edit freely; never commit this
# file to the team repo (it's git-ignored by default).

schema_version: 1
profile:
  scope: personal
  owner: {owner}
  updated_at: {today}

entries: []
"""


def write_personal_stub(path: Path, owner: str, today: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PERSONAL_STUB.format(owner=owner, today=today))
