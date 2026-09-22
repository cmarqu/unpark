---
name: unpark
tagline: Un-park side projects with a re-entry briefing, recipes, and a local demo launcher
status: active
updated: 2026-09-22
---

## What is this

Unpark briefs developers returning to a project after weeks or months away:
what it is, where work stopped, and the one command that shows it running.

It is a dependency-free Python package with terminal and localhost-dashboard
views over a human-readable `WELCOME.md`, live git facts, and managed recipes.

## Goals

- [x] Import the working prototype and full test suite
- [x] Integrate with task runners through delegated recipes
- [x] Complete the internal and public `unpark` rebrand
- [x] Publish truthful README, license, and security guidance
- [x] Add a relative-dated disposable demo project
- [x] Drop `inject`; `WELCOME.md` is the only committed project artifact
- [x] Restructure as a `src/unpark` package with focused core modules
- [x] Add Linux/macOS/Windows CI for Python 3.9–3.13
- [x] Add structural briefing lint and strict validation
- [x] Page long interactive terminal briefings through `$PAGER`
- [x] Add the Trusted Publishing workflow
- [ ] Publish v0.2.1 on PyPI through Trusted Publishing
- [x] Add the fish directory-change hook (`unpark shell fish`)

## State of things

The fish directory-change hook answers issue #1. `unpark shell fish
--install` writes a managed, version-stamped block to
`~/.config/fish/conf.d/unpark.fish` that wraps `fish_prompt` once (the
direnv pattern) and runs `unpark cd-hook DIR --from PREV` on every
directory change.

Entering a project prints its terminal briefing; entering a git repo
without one offers `unpark init`, or creates the template at the repo
root with `UNPARK_CD_AUTO_INIT=1`. Moving within one project or repo
stays silent, and the hook never fails a prompt.

`unpark shell fish` prints the generated script; `--uninstall` removes
the block again, keeping hand-added lines.

The public-release hardening pass is complete: the package, commands, skill,
state/config paths, documentation, and tests now use `unpark`. The sole
prototype installation is migrated directly instead of carrying public
compatibility code.

The project now includes a disposable `unpark demo`, a `src/` package split,
strict briefing lint, security documentation, and a 15-job OS/Python CI
matrix. Long interactive briefings page through `$PAGER`; pipes stay plain.

Global upkeep installation now lets the user choose Claude, Codex, Copilot, or
all file-based integrations; Cursor remains a deliberately manual User Rules
setup. `unpark demo` now stays in the terminal by default; `--html` explicitly
opens its disposable dashboard. Its command help spells out when generated
files are deleted or retained.

The README distinguishes the disposable `unpark demo --html` flow from daily
`unpark html` use inside a real repository. It includes a dashboard rendering
for the former and a fake fifteen-project portfolio for the latter.

The terminal `unpark projects` overview now pages long lists and can open a
chosen registered project's briefing through `--pick` or `unpark project NAME`.
The release workflow builds only version-matching `v*` tags and publishes the
artifact through the configured PyPI Trusted Publisher and `pypi` Environment.

The package is published on PyPI through Trusted Publishing. Its full
Linux/macOS/Windows matrix passes on Python 3.9–3.13; local dashboard and
manual servers avoid the reverse-DNS lookup that stalled macOS runners.

Known issues:
- The dashboard/manual implementation remains the largest module and can be
  split further when a concrete maintenance need appears.
- A dashboard screenshot remains optional release polish.
- bash/zsh hooks and the git branch-change trigger (also from issue #1) are
  still open; `~/.config/unparkrc` root directories remain a possible
  refinement of the auto-init scope.

Next step: cut the release with the fish hook, then port the hook to bash
and zsh through their native `chpwd`/`PROMPT_COMMAND` hooks.

## Recipes

### test

Run the complete unit suite in the project environment.

```sh
uv run python -W error::ResourceWarning -m unittest discover -s tests
```

### check

Validate this briefing, including structural warnings.

```sh
uv run unpark check --strict
```

### demo

Open the disposable generated sample project and interactive dashboard.

- background: true

```sh
uv run unpark demo
```

## Distribute

Install the published tool with `uv tool install unpark` (or `pipx install
unpark`). Use `uv tool install .` for a development checkout. Version 0.2.1
is the first PyPI release.
