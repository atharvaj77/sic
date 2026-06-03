"""`sic` command-line entrypoint.

Phase 2 wires up the typer app and implements `validate` end-to-end.
The remaining commands are intentional stubs that exit with a clear
"not implemented yet" message so users can discover the surface area
and shell completions work; later phases will fill them in.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .loader import ProfileLoadError, dump_profile_str, load_profile
from .merger import ConflictReason, merge_profiles
from .renderer import RenderContext, render_claude_md, resolve_target_path, write_claude_md
from .skills.cli import app as skills_app
from .workspace import (
    Config,
    GitError,
    Workspace,
    git_clone,
    git_head_sha,
    git_pull,
    write_personal_stub,
)

app = typer.Typer(
    name="sic",
    help="sharing-is-caring — shared team memory profiles for Claude Code.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(skills_app, name="skills")

console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"sic {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """Root callback; only used to host global options."""


# ---------- implemented in Phase 2 ----------

@app.command()
def validate(
    paths: list[Path] = typer.Argument(..., exists=True, readable=True, help="Profile YAML file(s)."),
    strict: bool = typer.Option(False, "--strict", help="Reserved for future strict mode."),
) -> None:
    """Validate one or more profile YAML files against the schema."""
    del strict  # accepted now so CI can pin the flag; behavior added later
    failures = 0
    for path in paths:
        try:
            profile = load_profile(path)
        except ProfileLoadError as e:
            err_console.print(f"[red]FAIL[/red] {e}")
            failures += 1
            continue
        console.print(
            f"[green]OK[/green]   {path} "
            f"({profile.profile.scope.value}, {len(profile.entries)} entries)"
        )
    if failures:
        raise typer.Exit(code=1)


@app.command()
def init(
    team_repo: str = typer.Option(..., "--team-repo", help="Git URL of the team profile repo."),
    owner: str = typer.Option(..., "--owner", help="Your identifier (used in the personal profile)."),
    team_profile_path: str = typer.Option(
        "team.yaml", "--team-profile-path", help="Path to the team profile inside the cloned repo."
    ),
    force: bool = typer.Option(False, "--force", help="Re-clone if the team directory already exists."),
) -> None:
    """Initialize a local sic workspace: clone the team repo + scaffold personal.yaml."""
    from datetime import date
    import shutil

    ws = Workspace.current()
    ws.root.mkdir(parents=True, exist_ok=True)

    if ws.team_dir.exists():
        if not force:
            err_console.print(
                f"[yellow]team directory already exists at {ws.team_dir}; "
                f"use --force to re-clone.[/yellow]"
            )
            raise typer.Exit(code=1)
        shutil.rmtree(ws.team_dir)

    try:
        git_clone(team_repo, ws.team_dir)
    except GitError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    Config(team_repo=team_repo, team_profile_path=team_profile_path).dump(ws.config_file)
    write_personal_stub(ws.personal_file, owner=owner, today=date.today().isoformat())

    console.print(f"[green]initialized[/green] {ws.root}")
    console.print(f"  team    : {ws.team_profile_path(team_profile_path)}")
    console.print(f"  personal: {ws.personal_file}")


@app.command()
def sync(
    render_target: str = typer.Option(
        None, "--render", help="Also re-render CLAUDE.md to this target (user|project) after sync."
    ),
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    no_timestamp: bool = typer.Option(False, "--no-timestamp"),
    skills: bool = typer.Option(
        True, "--skills/--no-skills", help="Also install skills to configured targets."
    ),
) -> None:
    """Pull team profile updates and re-merge (optionally re-render CLAUDE.md)."""
    from datetime import datetime, timezone

    from .skills import discover_skills, install_all, resolve_skills
    from .skills.installer import build_target

    ws = Workspace.current()
    if not ws.config_file.exists():
        err_console.print(
            f"[red]no sic workspace at {ws.root}; run `sic init` first.[/red]"
        )
        raise typer.Exit(code=1)

    cfg = Config.load(ws.config_file)
    try:
        git_pull(ws.team_dir)
        sha = git_head_sha(ws.team_dir)
    except GitError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    team_path = ws.team_profile_path(cfg.team_profile_path)
    if not team_path.exists():
        err_console.print(f"[red]team profile not found at {team_path}[/red]")
        raise typer.Exit(code=1)

    team_profile = load_profile(team_path)
    personal_profile = load_profile(ws.personal_file)
    result = merge_profiles(team_profile, personal_profile)

    console.print(
        f"[green]synced[/green] team@{sha} → {len(result.resolved)} entries, "
        f"{len(result.conflicts)} conflict(s)"
    )

    if render_target is not None:
        ctx = RenderContext(
            team_owner=team_profile.profile.owner,
            personal_owner=personal_profile.profile.owner,
            generated_at=None if no_timestamp else datetime.now(timezone.utc),
        )
        text = render_claude_md(result, ctx)
        path = resolve_target_path(render_target, project_root)
        write_claude_md(text, path)
        console.print(f"[green]rendered[/green] {path}")

    skill_conflicts = 0
    if skills and cfg.skill_targets:
        team_skills = discover_skills(ws.team_skills_dir(cfg.team_skills_path))
        personal_skills = discover_skills(ws.personal_skills_dir)
        skill_set = resolve_skills(team_skills, personal_skills)
        targets = [build_target(name) for name in cfg.skill_targets]
        report = install_all(skill_set, targets, project_root=project_root)
        console.print(
            f"[green]skills[/green] {len(skill_set.installed)} resolved → "
            f"{len(cfg.skill_targets)} target(s) "
            f"(written={len(report.written)}, unchanged={len(report.unchanged)}, "
            f"removed={len(report.removed)})"
        )
        skill_conflicts = len(skill_set.conflicts)
        for c in skill_set.conflicts:
            err_console.print(
                f"[red]skill conflict[/red] {c.name}: team@{c.team_version} locked, "
                f"personal@{c.personal_version} ignored"
            )

    if result.conflicts or skill_conflicts:
        raise typer.Exit(code=3)


@app.command()
def merge(
    team: Path = typer.Argument(..., exists=True, readable=True),
    personal: Path = typer.Argument(..., exists=True, readable=True),
    output: Path = typer.Option(None, "--output", "-o", help="Write resolved YAML to a file (default: stdout)."),
) -> None:
    """Merge a team profile with a personal profile and print the resolved YAML."""
    team_profile = load_profile(team)
    personal_profile = load_profile(personal)
    result = merge_profiles(team_profile, personal_profile)

    resolved_profile = personal_profile.model_copy(update={"entries": result.resolved})
    text = dump_profile_str(resolved_profile)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
        console.print(f"[green]wrote[/green] {output} ({len(result.resolved)} entries)")
    else:
        console.print(text, end="", highlight=False)

    if result.conflicts:
        err_console.print(
            f"[yellow]warning:[/yellow] {len(result.conflicts)} unresolved conflict(s); "
            f"run `sic conflicts {team} {personal}` for details."
        )
        raise typer.Exit(code=3)


@app.command()
def render(
    team: Path = typer.Argument(..., exists=True, readable=True, help="Team profile YAML."),
    personal: Path = typer.Argument(..., exists=True, readable=True, help="Personal profile YAML."),
    target: str = typer.Option("project", "--target", help="Where to write CLAUDE.md: user|project|-"),
    project_root: Path = typer.Option(Path.cwd(), "--project-root", help="Project root for --target=project."),
    output: Path = typer.Option(None, "--output", "-o", help="Explicit output path (overrides --target)."),
    no_timestamp: bool = typer.Option(False, "--no-timestamp", help="Omit generated-at timestamp (deterministic)."),
) -> None:
    """Render the resolved profile into CLAUDE.md."""
    from datetime import datetime, timezone

    team_profile = load_profile(team)
    personal_profile = load_profile(personal)
    result = merge_profiles(team_profile, personal_profile)

    ctx = RenderContext(
        team_owner=team_profile.profile.owner,
        personal_owner=personal_profile.profile.owner,
        generated_at=None if no_timestamp else datetime.now(timezone.utc),
    )
    text = render_claude_md(result, ctx)

    if target == "-" and output is None:
        console.print(text, end="", highlight=False)
    else:
        path = output if output is not None else resolve_target_path(target, project_root)
        write_claude_md(text, path)
        console.print(f"[green]wrote[/green] {path} ({len(result.resolved)} entries)")

    if result.conflicts:
        err_console.print(
            f"[yellow]warning:[/yellow] rendered with {len(result.conflicts)} unresolved conflict(s)."
        )
        raise typer.Exit(code=3)


@app.command()
def diff(
    a: Path = typer.Argument(..., exists=True, readable=True),
    b: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Show a human-readable diff between two profiles."""
    pa = load_profile(a)
    pb = load_profile(b)
    a_idx = {(e.section.value, e.key): e for e in pa.entries}
    b_idx = {(e.section.value, e.key): e for e in pb.entries}
    keys = sorted(set(a_idx) | set(b_idx))

    table = Table(title=f"diff: {a.name}  vs  {b.name}")
    table.add_column("section.key")
    table.add_column(a.name, overflow="fold")
    table.add_column(b.name, overflow="fold")
    for ident in keys:
        ae = a_idx.get(ident)
        be = b_idx.get(ident)
        av = "—" if ae is None else repr(ae.value)
        bv = "—" if be is None else repr(be.value)
        if av == bv:
            continue
        table.add_row(f"{ident[0]}.{ident[1]}", av, bv)
    console.print(table)


@app.command()
def conflicts(
    team: Path = typer.Argument(..., exists=True, readable=True),
    personal: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """List unresolved conflicts between a team and a personal profile."""
    result = merge_profiles(load_profile(team), load_profile(personal))
    if not result.conflicts:
        console.print("[green]no conflicts[/green]")
        return

    for c in result.conflicts:
        reason_label = {
            ConflictReason.LOCKED_BY_TEAM: "locked by team",
            ConflictReason.STRATEGY_MISMATCH: "merge strategy mismatch",
        }[c.reason]
        console.print(f"[bold]{c.identifier}[/bold]  [red]({reason_label})[/red]")
        console.print(f"  team    : {c.team_entry.value!r}")
        console.print(f"  personal: {c.personal_entry.value!r}")
        console.print("  options : accept-team | accept-personal | open-llm-merge")
    raise typer.Exit(code=3)


@app.command("import-chatgpt")
def import_chatgpt(
    export: Path = typer.Argument(..., exists=True, readable=True, help="ChatGPT memory export JSON."),
    as_: str = typer.Option(..., "--as", help="Target profile owner identifier."),
    output: Path = typer.Option(None, "--output", "-o", help="Where to write the draft (default: stdout)."),
    use_llm: bool = typer.Option(False, "--use-llm", help="Use Claude for classification (requires ANTHROPIC_API_KEY)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the draft without writing it anywhere."),
) -> None:
    """Import a ChatGPT memory export into a draft personal profile."""
    from .importer import ImportError_, import_chatgpt as _import

    try:
        draft = _import(export, owner=as_, use_llm=use_llm)
    except ImportError_ as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    text = dump_profile_str(draft)
    if dry_run or (output is None):
        console.print(text, end="", highlight=False)
        if dry_run:
            err_console.print(
                f"[yellow]dry-run:[/yellow] would have imported {len(draft.entries)} entries."
            )
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    console.print(f"[green]wrote[/green] {output} ({len(draft.entries)} entries)")


if __name__ == "__main__":
    app()
