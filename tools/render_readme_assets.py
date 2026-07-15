#!/usr/bin/env python3
"""Regenerate the dark README dashboard snapshots.

Run with:
    uv run --with weasyprint==52.5 --with pillow tools/render_readme_assets.py
"""

from pathlib import Path
import shutil
import tempfile

from PIL import Image
from weasyprint import HTML

from unpark.app import (_derived, _load, gather_portfolio, render_html,
                        render_portfolio_html)
from unpark.demo import build_demo_project


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
PALETTE = {
    "bg": "#0e1220", "card": "#171c2e", "ink": "#dbe2f4",
    "muted": "#8b94ad", "line": "#262d45", "accent": "#7aa2ff",
    "warn-bg": "#33290e", "warn-ink": "#ffd166", "ok": "#69db7c",
    "pill": "#24304f", "pill-ink": "#9db4ff", "code": "#10162a",
}
PROJECTS = [
    ("archive-lantern", "parked", "A searchable archive for old workshop notes"),
    ("bird-count", "active", "A seasonal notebook for backyard sightings"),
    ("cache-map", "paused", "A visual map of local development caches"),
    ("draft-shelf", "parked", "An inbox for half-written essays and sketches"),
    ("field-notes", "active", "A compact field-notes notebook"),
    ("garden-log", "active", "Planting dates and balcony observations"),
    ("habit-orbit", "paused", "A small experiment in gentle habit tracking"),
    ("invoice-jar", "active", "A monthly freelance invoice checklist"),
    ("kitchen-lab", "parked", "Recipes and notes from weekend experiments"),
    ("language-gym", "active", "Tiny vocabulary practice sessions"),
    ("map-room", "paused", "A map annotation tool for walking routes"),
    ("note-pile", "parked", "A calmer home for scattered research notes"),
    ("photo-globe", "parked", "Photos on a spinning 3D globe"),
    ("reading-radar", "active", "Books to read, quote, and lend out"),
    ("tiny-weather", "paused", "A local weather display for a spare screen"),
]


def dark_page(page: str, height: int) -> str:
    for name, value in PALETTE.items():
        page = page.replace(f"var(--{name})", value)
    return page + f"<style>@page {{ size: 1200px {height}px; margin: 0 }}</style>"


def write_png(page: str, destination: Path, height: int, crop_height: int):
    temporary = destination.with_suffix(".full.png")
    HTML(string=dark_page(page, height)).write_png(str(temporary))
    try:
        with Image.open(temporary) as image:
            image.crop((0, 0, 1200, crop_height)).save(destination,
                                                         optimize=True)
    finally:
        temporary.unlink(missing_ok=True)


def rewrite_briefing(path: Path, name: str, status: str, tagline: str):
    briefing = path.read_text()
    briefing = briefing.replace("name: photo-globe", f"name: {name}")
    briefing = briefing.replace(
        "tagline: Photos on a spinning 3D globe, placed by EXIF geolocation",
        f"tagline: {tagline}")
    briefing = briefing.replace("status: parked", f"status: {status}")
    path.write_text(briefing)


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="unpark-readme-") as directory:
        root = Path(directory)
        demo = build_demo_project(root / "photo-globe")
        demo_welcome = _load(demo / "WELCOME.md")
        write_png(render_html(demo_welcome, _derived(demo, demo_welcome)),
                  ASSETS / "wb-html.png", 1200, 1200)

        portfolio = root / "sample-projects"
        for name, status, tagline in PROJECTS:
            target = portfolio / name
            shutil.copytree(demo, target)
            rewrite_briefing(target / "WELCOME.md", name, status, tagline)
        write_png(render_portfolio_html(gather_portfolio(portfolio), portfolio),
                  ASSETS / "projects-dashboard.png", 3800, 1200)


if __name__ == "__main__":
    main()
