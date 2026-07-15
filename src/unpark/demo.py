"""Disposable demonstration project for evaluating unpark safely."""

import datetime as dt
import os
from pathlib import Path
import subprocess


def build_demo_project(destination) -> Path:
    """Create a small project whose git dates stay meaningful over time."""
    root = Path(destination).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"demo destination is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)

    today = dt.date.today()
    started = today - dt.timedelta(days=120)
    briefing_date = today - dt.timedelta(days=40)
    last_change = today - dt.timedelta(days=8)

    (root / "README.md").write_text(
        "# photo-globe\n\nA tiny generated project used to demonstrate unpark.\n"
    )
    _git_commit(root, "start photo-globe", started)

    (root / "WELCOME.md").write_text(f"""---
name: photo-globe
tagline: Photos on a spinning 3D globe, placed by EXIF geolocation
status: parked
updated: {briefing_date.isoformat()}
---

## What is this

A generated sample for trying unpark without changing one of your projects.

It contains a tiny static web page and deliberately stale project history.

## Goals

- [x] Render the globe
- [x] Add sample locations
- [ ] Import a real photo library

## State of things

The static prototype works; the briefing intentionally predates the latest
commit so unpark can demonstrate its staleness warning.

Known issues:
- This is a disposable example, not a real photo application.

Next step: click or run the `demo` recipe, then close it with `unpark stop`.

## Recipes

### demo

Serve the generated globe page.

- background: true
- url: http://localhost:8765

```sh
python -m http.server 8765
```

## Distribute

There is nothing to distribute; `unpark demo` regenerates this project.
""")
    _git_commit(root, "add re-entry briefing", briefing_date)

    (root / "index.html").write_text("""<!doctype html>
<meta charset="utf-8">
<title>photo-globe demo</title>
<style>body{background:#09111f;color:#dce8ff;font:20px system-ui;
display:grid;place-items:center;min-height:90vh}.globe{font-size:8rem}</style>
<main><div class="globe">🌍</div><h1>photo-globe</h1>
<p>A tiny project, successfully unparked.</p></main>
""")
    _git_commit(root, "polish globe landing page", last_change)
    (root / "IDEAS.md").write_text(
        "Untracked on purpose: add drag-and-drop photo import.\n"
    )
    return root


def _git_commit(root: Path, message: str, date: dt.date) -> None:
    env = dict(os.environ)
    timestamp = f"{date.isoformat()}T12:00:00+0000"
    env.update({
        "GIT_AUTHOR_NAME": "Unpark Demo",
        "GIT_AUTHOR_EMAIL": "demo@unpark.local",
        "GIT_COMMITTER_NAME": "Unpark Demo",
        "GIT_COMMITTER_EMAIL": "demo@unpark.local",
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_DATE": timestamp,
    })
    try:
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=root, env=env,
                           check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, env=env,
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=root,
                       env=env, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        # Git facts enrich the demonstration but are not required to try it.
        return
