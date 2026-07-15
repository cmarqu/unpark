---
name: unpark-upkeep
description: Use when finishing any piece of work in this repo — update WELCOME.md (State of things, Goals, Recipes), bump the updated: date, and run `unpark check` so the re-entry briefing stays fresh. Also use when the user asks to set up unpark / initialize a WELCOME.md briefing.
---

<!-- unpark-skill-version: 3eba4086 -->

## Keeping WELCOME.md fresh

WELCOME.md is this project's re-entry briefing; the `unpark` CLI
renders it for humans returning after weeks away. It lives at
WELCOME.md, docs/WELCOME.md or .unpark/WELCOME.md — same format
either way. Treat it as part of the deliverable: a stale briefing is
worse than none.

## Setting up a project for the first time

When the user asks something like "Set up unpark for this project" /
"initialize a WELCOME.md" (or you find no briefing in a project that
would benefit from one — suggest it, don't just do it):

1. Run `unpark init` to create the skeleton (top-level WELCOME.md;
   move it to docs/WELCOME.md if the repo's top level is restricted).
2. Replace every placeholder by reading the repo — README, package
   manifests, build files, recent git history:
   - **What is this** — 2-3 sentences: problem, audience, what exists.
   - **Goals** — reconstruct from README aims, TODOs, issue titles;
     tick what is demonstrably done.
   - **State of things** — from the recent commits: what was last
     worked on, known issues, the plausible next step. Follow the
     Formatting section below — short paragraphs and bullets, never
     one block of prose.
   - **Recipes** — the *real* build/run/test commands from the build
     files. Define a `demo` recipe (`background: true`, `url:` if it
     serves) that shows the project off from a cold start — this is
     the most valuable recipe; verify each command actually runs.
     Keep it to at most ~5 re-entry verbs. If the project has a task
     runner (justfile, Taskfile.yml, maskfile.md, package.json
     scripts, Makefile, mise), recipe steps must DELEGATE to it in
     one line (`just demo`, `npm run dev`) — never duplicate the
     command bodies; the runner owns the details and nothing drifts.
   - VS Code: prefer keeping the `.code-workspace` file in
     the repo root — tools and teammates find it, and re-entry opens
     the workspace (with its associated chats), not a bare folder.
     If it must live outside the repo, record it with a `workspace:`
     front-matter key so it is never forgotten.
3. Set `updated:` to today, run `unpark check`, fix what it reports.
4. Offer (don't force): `unpark register` to add it to the user's
   portfolio, and `unpark skill --install` if this repo should carry
   the upkeep instructions for other agents. Skip the local install
   when the user's global skill is installed and nobody else works on
   the repo — locally installed instructions only add value for
   teammates and tools without the global one.

## Keeping it fresh

When you finish a piece of work — before your final summary or commit —
update it:

1. Read WELCOME.md and revise what your work changed:
   - **State of things** — rewrite to 2–6 sentences: what was just
     done, known issues, the next intended step. Present tense,
     most-recent first. Prose, not a changelog (history lives in git).
   - **Goals** — tick finished items to `- [x]`; add new goals only for
     real scope changes.
   - **Recipes** — if build/run/demo/distribute commands changed, fix
     the fenced steps and the `- key: value` bullets (background, dir,
     url, needs, env, dotenv). A recipe named `demo` should always
     show the project off from a cold start. Prefer one-line
     delegation to the project's task runner (`just …`, `npm run …`)
     over duplicated command bodies; keep recipes to ~5 re-entry
     verbs, the full catalog belongs to the runner.
   - **What is this / Distribute** — only when purpose or shipping
     actually changed.
2. Set `updated:` in the front-matter to today's date (YYYY-MM-DD).
3. Run `unpark check --strict` and fix every error and warning.

## Formatting — no wall of text

The briefing is read by a human squinting at a terminal or a browser
after weeks away. Every section must be scannable, not a jumble:

- Paragraphs of at most 3 sentences, one idea each, separated by a
  blank line. A section body that is one unbroken block is wrong.
- Use bullet lists for anything enumerable — known issues, next
  steps, components. Three facts joined by commas want to be three
  bullets.
- **State of things** works best as: one short lead paragraph (what
  just happened), then a `Known issues:` bullet list and a
  `Next step:` line. Not everything mashed together.
- **What is this** is at most two short paragraphs: first the
  problem/purpose, then the shape of the solution (stack, parts).
- Bold sparingly (`**word**`) to anchor the eye on key nouns;
  backticks for files, commands, and identifiers.
- Terminal, GitHub, and the dashboard all render this markdown —
  plain prose with blank lines and simple bullets survives all three.

Rules:
- Keep it honest and short — the reader is the future maintainer with
  zero context.
- Never delete sections you didn't write; unknown sections are legal.
- No WELCOME.md in this project? Suggest `unpark init`; don't invent
  another format.
- If `unpark` prints a "may be stale" warning, run this checklist
  immediately, not just at the end.

After a merge (or when `unpark check` reports conflict markers in
WELCOME.md), resolve the conflict by *reconciling*, never by picking a
side wholesale:
- `updated:` — keep the later date.
- **Goals** — union of both sides; a goal ticked on either side stays
  ticked.
- **State of things** — merge both sides' facts into fresh prose,
  newest first; drop statements the other branch made obsolete.
- **Recipes** — keep both sides' recipes; if the same recipe differs,
  keep the variant matching the merged code (verify the command runs).
- Finish with `unpark check`.
