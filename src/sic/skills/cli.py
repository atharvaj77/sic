"""`sic skills ...` typer subcommand group.

Commands:
    sic skills list                    — show all resolved skills + scope/version
    sic skills show NAME               — print one rendered SKILL.md
    sic skills validate [PATHS...]     — lint manifests
    sic skills new NAME                — scaffold a personal skill
    sic skills install                 — write skills to configured targets
    sic skills diff                    — show team vs personal differences
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from sic.skills import (
    SkillLoadError,
    build_target,
    discover_skills,
    install_all,
    load_skill,
    resolve_skills,
)
from sic.workspace import Config, Workspace

console = Console()
err_console = Console(stderr=True)


app = typer.Typer(
    name="skills",
    help="Manage shared team skills (SKILL.md bundles).",
    no_args_is_help=True,
    add_completion=False,
)


_NEW_SKILL_TEMPLATE = """\
---
name: {name}
version: 0.1.0
description: >
  TODO: one-sentence description used by agents for routing.
applies_to: []
owner: {owner}
tags: []
---
# {name}

TODO: write the skill body. Anything an agent should know about this task
goes here — when to invoke, how to invoke, gotchas, examples.
"""


def _load_workspace_skills(ws: Workspace, cfg: Config) -> tuple[list, list]:
    team_skills_dir = ws.team_skills_dir(cfg.team_skills_path)
    team_skills = discover_skills(team_skills_dir)
    personal_skills = discover_skills(ws.personal_skills_dir)
    return team_skills, personal_skills


# ---------- list ----------

@app.command("list")
def list_skills() -> None:
    """List all skills resolved from the team repo + personal overlay."""
    ws = Workspace.current()
    if not ws.config_file.exists():
        err_console.print(f"[red]no sic workspace at {ws.root}; run `sic init` first.[/red]")
        raise typer.Exit(code=1)
    cfg = Config.load(ws.config_file)
    team_skills, personal_skills = _load_workspace_skills(ws, cfg)
    result = resolve_skills(team_skills, personal_skills)

    if not result.installed:
        console.print("[yellow]no skills found[/yellow]")
        return

    table = Table(title="resolved skills")
    table.add_column("name", style="bold")
    table.add_column("version")
    table.add_column("scope")
    table.add_column("lock")
    table.add_column("description", overflow="fold")
    for inst in result.installed:
        m = inst.skill.manifest
        lock = "🔒" if m.lock else ""
        table.add_row(m.name, m.version, inst.scope.value, lock, m.description.strip())
    console.print(table)

    if result.overrides:
        console.print(
            f"[yellow]{len(result.overrides)} override(s):[/yellow] "
            + ", ".join(o.name for o in result.overrides)
        )
    if result.conflicts:
        console.print(
            f"[red]{len(result.conflicts)} conflict(s):[/red] "
            + ", ".join(c.name for c in result.conflicts)
        )
        raise typer.Exit(code=3)


# ---------- show ----------

@app.command()
def show(name: str = typer.Argument(..., help="Skill name.")) -> None:
    """Print the rendered SKILL.md for one skill."""
    ws = Workspace.current()
    if not ws.config_file.exists():
        err_console.print(f"[red]no sic workspace at {ws.root}; run `sic init` first.[/red]")
        raise typer.Exit(code=1)
    cfg = Config.load(ws.config_file)
    team_skills, personal_skills = _load_workspace_skills(ws, cfg)
    result = resolve_skills(team_skills, personal_skills)

    match = result.by_name().get(name)
    if match is None:
        err_console.print(f"[red]no skill named {name!r}[/red]")
        raise typer.Exit(code=1)
    console.print(match.skill.to_skill_md(), end="", highlight=False)


# ---------- validate ----------

@app.command()
def validate(
    paths: list[Path] = typer.Argument(
        None,
        help="Skill directories or roots to validate. Default: workspace team + personal skills.",
    ),
) -> None:
    """Validate skill manifests under the given paths (or the workspace)."""
    targets: list[Path] = []
    if paths:
        targets.extend(paths)
    else:
        ws = Workspace.current()
        if not ws.config_file.exists():
            err_console.print(
                f"[red]no sic workspace at {ws.root}; run `sic init` first.[/red]"
            )
            raise typer.Exit(code=1)
        cfg = Config.load(ws.config_file)
        targets = [ws.team_skills_dir(cfg.team_skills_path), ws.personal_skills_dir]

    failures = 0
    total = 0
    for target in targets:
        if not target.exists():
            continue
        # If the target itself is a skill dir, load directly; otherwise treat
        # as a roots dir of skill subdirs.
        if (target / "SKILL.md").is_file():
            cases = [target]
        else:
            cases = [p for p in sorted(target.iterdir()) if p.is_dir() and (p / "SKILL.md").is_file()]
        for d in cases:
            total += 1
            try:
                skill = load_skill(d)
                console.print(
                    f"[green]OK[/green]   {d} ({skill.manifest.name}@{skill.manifest.version})"
                )
            except SkillLoadError as e:
                err_console.print(f"[red]FAIL[/red] {e}")
                failures += 1

    if total == 0:
        console.print("[yellow]no skills found[/yellow]")
    if failures:
        raise typer.Exit(code=1)


# ---------- new ----------

@app.command()
def new(
    name: str = typer.Argument(..., help="Skill name (lowercase, digits, hyphens)."),
    owner: str = typer.Option(None, "--owner", help="Owner field (defaults to personal profile owner)."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing skill directory."),
) -> None:
    """Scaffold a personal skill directory under ~/.sic/personal-skills/<name>/."""
    from sic.skills.schema import SKILL_NAME_RE
    from sic.loader import load_profile

    if not SKILL_NAME_RE.match(name):
        err_console.print(
            f"[red]invalid skill name {name!r} — must match {SKILL_NAME_RE.pattern}[/red]"
        )
        raise typer.Exit(code=1)

    ws = Workspace.current()
    dest = ws.personal_skills_dir / name
    if dest.exists() and not force:
        err_console.print(f"[red]{dest} already exists; pass --force to overwrite.[/red]")
        raise typer.Exit(code=1)

    if owner is None:
        if ws.personal_file.exists():
            try:
                owner = load_profile(ws.personal_file).profile.owner
            except Exception:  # noqa: BLE001
                owner = "me"
        else:
            owner = "me"

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(_NEW_SKILL_TEMPLATE.format(name=name, owner=owner))
    console.print(f"[green]created[/green] {dest / 'SKILL.md'}")


# ---------- install ----------

@app.command()
def install(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    target: list[str] = typer.Option(
        None,
        "--target",
        help="Override configured targets (repeatable). Choices: claude, copilot, generic.",
    ),
) -> None:
    """Materialize resolved skills into configured agent targets."""
    ws = Workspace.current()
    if not ws.config_file.exists():
        err_console.print(f"[red]no sic workspace at {ws.root}; run `sic init` first.[/red]")
        raise typer.Exit(code=1)
    cfg = Config.load(ws.config_file)
    team_skills, personal_skills = _load_workspace_skills(ws, cfg)
    result = resolve_skills(team_skills, personal_skills)

    target_names = target or cfg.skill_targets
    if not target_names:
        err_console.print(
            "[yellow]no skill targets configured; "
            "set `skill_targets` in ~/.sic/config.json or pass --target.[/yellow]"
        )
        raise typer.Exit(code=1)

    targets = [build_target(name) for name in target_names]
    report = install_all(result, targets, project_root=project_root)

    console.print(
        f"[green]installed[/green] {len(result.installed)} skill(s) into "
        f"{len(target_names)} target(s) "
        f"(written={len(report.written)}, unchanged={len(report.unchanged)}, "
        f"removed={len(report.removed)})"
    )
    if result.conflicts:
        for c in result.conflicts:
            err_console.print(
                f"[red]conflict[/red] {c.name}: team@{c.team_version} locked, "
                f"personal@{c.personal_version} ignored"
            )
        raise typer.Exit(code=3)


# ---------- diff ----------

@app.command()
def diff() -> None:
    """Show team vs personal skill differences (overrides + conflicts)."""
    ws = Workspace.current()
    if not ws.config_file.exists():
        err_console.print(f"[red]no sic workspace at {ws.root}; run `sic init` first.[/red]")
        raise typer.Exit(code=1)
    cfg = Config.load(ws.config_file)
    team_skills, personal_skills = _load_workspace_skills(ws, cfg)
    result = resolve_skills(team_skills, personal_skills)

    if not result.overrides and not result.conflicts:
        console.print("[green]no skill differences[/green]")
        return

    table = Table(title="skill differences")
    table.add_column("name", style="bold")
    table.add_column("team")
    table.add_column("personal")
    table.add_column("outcome")
    for ov in result.overrides:
        table.add_row(ov.name, ov.team_version, ov.personal_version, "personal wins")
    for c in result.conflicts:
        table.add_row(c.name, c.team_version, c.personal_version, "[red]locked: team wins[/red]")
    console.print(table)
    if result.conflicts:
        raise typer.Exit(code=3)
