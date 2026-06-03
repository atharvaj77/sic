# sharing-is-caring (`sic`)

**One team brain for your AI assistants.** Agree on conventions once, and every
teammate's Claude / Copilot / Cursor session picks them up automatically.

`sic` merges a team YAML + your personal YAML into a `CLAUDE.md` that agents
read on their own. It also ships **skills** (longer playbooks) into the agent's
own skills folder.

---

## Quickstart

```bash
pip install sharing-is-caring

sic init --team-repo git@github.com:acme/team-profile.git --owner alice
$EDITOR ~/.sic/personal.yaml
sic sync --render project        # writes ./CLAUDE.md
```

Commit `CLAUDE.md` or gitignore it — your call.

---

## The 30-second mental model

```
team.yaml  ─┐
            ├─►  sic merge  ─►  CLAUDE.md   (agent reads this)
personal.yaml ─┘                    ▲
                                    │
team/skills/* + personal-skills/* ──┘   (skill bundles, installed
                                         to ~/.claude/skills/ etc.)
```

Three tiers of conflict resolution:

1. **Auto-merge** — lists concat, dicts shallow-merge, identical values pass through.
2. **Personal wins** — your overrides beat team defaults.
3. **Team locks win** — entries/skills marked `lock: true` cannot be overridden;
   conflicts are surfaced in `CLAUDE.md` and `sic` exits `3`.

---

## CLI cheat sheet

| Command | What it does |
| --- | --- |
| `sic init --team-repo URL --owner NAME` | Clone team repo, scaffold `~/.sic/personal.yaml` |
| `sic sync [--render project\|user]` | Pull team repo, merge, render, install skills |
| `sic validate FILE...` | Lint profile YAMLs |
| `sic merge TEAM PERSONAL` | Print merged profile |
| `sic render TEAM PERSONAL` | Print `CLAUDE.md` |
| `sic conflicts TEAM PERSONAL` | Show unresolved conflicts |
| `sic diff A B` | Diff two profiles |
| `sic import-chatgpt EXPORT --as NAME` | Seed personal profile from ChatGPT export |
| `sic skills {list,show,new,validate,install,diff}` | Manage skill bundles |

Exit codes: `0` ok · `1` error · `3` unresolved conflicts.

---

## Profile YAML (the 10-line version)

```yaml
schema_version: 1
profile: { scope: team, owner: acme, updated_at: '2026-06-03' }
entries:
  - { section: tools, key: dataframe_library, value: polars, merge: replace, lock: true }
  - { section: do_not, key: secrets, value: [aws_keys, prod_dsn], merge: append }
```

Sections: `identity`, `languages`, `tools`, `coding_style`, `workflow`,
`domain_knowledge`, `do_not`, `free_form`.
Merge strategies: `replace` (scalar), `append` (list), `map_merge` (dict).
Add `lock: true` on team entries you don't want personal profiles to override.

Full example: [examples/team.yaml](examples/team.yaml).

---

## Skills (the longer-form playbooks)

A skill is a folder with a `SKILL.md` — frontmatter + Markdown body — that the
agent loads on demand for a specific tool or task.

```
team-repo/skills/cdp-mcp/SKILL.md      # team-shared
~/.sic/personal-skills/my-helper/SKILL.md   # yours
```

```yaml
---
name: cdp-mcp
version: 0.3.0
description: How to drive Acquia CDP MCP.
lock: false        # true => team copy can't be overridden
---
# body — instructions for the agent
```

Enable installation by setting `skill_targets` in `~/.sic/config.json`:

| Target | Writes to |
| --- | --- |
| `claude` | `~/.claude/skills/<name>/` |
| `copilot` | `<project>/.github/prompts/<name>.skill.md` |
| `generic` | `<project>/.ai/skills/<name>/` |

Then `sic sync` installs them and lists them in `CLAUDE.md` under
**Available skills**. Re-installs are byte-diff idempotent and only touch
files `sic` itself wrote.

---

## `~/.sic/` layout

```
~/.sic/
├── config.json          # team repo URL + skill_targets
├── team/                # git clone of team repo
├── personal.yaml        # your overlay
└── personal-skills/     # your skill bundles
```

Override the root with `$SIC_HOME`.

---

## Setting up a team repo

It's just a git repo with `team.yaml` (and optionally a `skills/` folder).
A ready-made validation workflow lives in [templates/team-repo/](templates/team-repo/).

---

## Determinism

Rendered files start with a banner including a `sha256:` of the resolved
profile, so two teammates on the same `team@SHA` + personal file get the
same hash. Use `--no-timestamp` for byte-stable diffs.

---

## Development

```bash
pip install -e '.[dev]'
pytest -q          # 181 tests
```

See [PLAN.md](PLAN.md) for the design doc.
