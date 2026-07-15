# Security policy

## Trust model

Unpark reads project files, inspects local git state, starts subprocesses, and
can open VS Code. A recipe in `WELCOME.md` is executable code. Review it with
the same care as a `Makefile`, `package.json` script, or shell script before
running `unpark run`, `unpark start`, or using dashboard action buttons.

Do not place secrets directly in `WELCOME.md`. Recipe `env:` values are visible
in the committed briefing. A referenced `dotenv:` file is read into the recipe
process environment and should normally be excluded from version control.

## Local browser server

The dashboard and manual:

- bind to an ephemeral port on `127.0.0.1` only;
- reject unexpected Host headers as DNS-rebinding defense in depth;
- require an unpredictable per-session `X-Unpark-Token` header for API
  actions;
- do not grant cross-origin access;
- stop after the browser heartbeat disappears.

Managed recipes intentionally outlive the dashboard. Inspect and stop them
with `unpark ps`, `unpark logs`, and `unpark stop`.

The browser API can start and stop recipes, register projects, install upkeep
instructions, create a workspace file, and open registered projects or VS
Code. Those actions use the same session-token protection.

## Data locations

Project registration is stored under the platform configuration directory;
process records and logs use the platform state directory. Set
`UNPARK_CONFIG_DIR` and `UNPARK_STATE_DIR` to override them. Prototype
state was migrated once during the private rebrand and is not part of the
public compatibility surface.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do
not include credentials, private project contents, or recipe logs in a public
issue.
