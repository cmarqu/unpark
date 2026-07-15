# README images

The dashboard images are generated, not hand-edited. Refresh them after a
visual dashboard change with:

```sh
uv run --with weasyprint==52.5 --with pillow tools/render_readme_assets.py
```

The renderer uses the dark dashboard palette and creates all example projects
in a temporary directory. It does not inspect the user's project registry.
