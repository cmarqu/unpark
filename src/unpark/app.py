#!/usr/bin/env python3
"""Application services and CLI for unpark.

Reads WELCOME.md (plus live git state) and briefs you on a project you
haven't touched in a while: what it is, status vs goals, how to build,
run, demo, and distribute it.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html as _html
import http.server
import json
import os
import re
import secrets
import shlex
import signal
import socketserver
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

from .model import Recipe, Section, Welcome
from .parser import parse_recipes as _parse_recipes
from .parser import parse_welcome
from .demo import build_demo_project
from .pager import display as display_text

__version__ = "0.2.1"
_ATTACHED_PIDS = set()


def _env(name: str):
    """Read an unpark environment setting."""
    return os.environ.get(f"UNPARK_{name}")


def _home() -> Path:
    """Home directory, honoring HOME as an explicit portable override."""
    return Path(os.environ.get("HOME") or Path.home())


def _safe_host_header(value: str) -> bool:
    """Reject DNS-rebinding Host values on localhost-only servers."""
    raw = (value or "").lower().strip()
    if raw.startswith("["):
        host = raw.partition("]")[0].lstrip("[")
    else:
        host = raw.split(":", 1)[0]
    return host in {"127.0.0.1", "localhost", "::1"}


# ------------------------------------------------------------ git facts


def _git(root, *args):
    try:
        p = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.strip()


def git_info(root):
    """Live facts derived from git, or None if not a git repo."""
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return None
    last = _git(root, "log", "-1", "--pretty=%ad\t%s", "--date=short") or "\t"
    last_date, _, last_subject = last.partition("\t")
    status = _git(root, "status", "--porcelain") or ""
    return {
        "branch": branch,
        "last_date": last_date,
        "last_subject": last_subject,
        "dirty": len(status.splitlines()),
    }


def commits_since(root, updated: str):
    """(count, latest_subject) of commits after the WELCOME.md updated date."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(updated or "")):
        return 0, ""
    out = _git(root, "log", f"--since={updated} 23:59:59", "--pretty=%s")
    if not out:
        return 0, ""
    subjects = out.splitlines()
    return len(subjects), subjects[0]


# ------------------------------------------------------------ rendering


def days_ago(datestr: str, today: str) -> str:
    try:
        d = _dt.date.fromisoformat(str(datestr))
        t = _dt.date.fromisoformat(today)
    except ValueError:
        return "?"
    n = (t - d).days
    if n <= 0:
        return "today"
    if n == 1:
        return "yesterday"
    return f"{n} days ago"


class _Style:
    """ANSI styling that degrades to plain text when color is off."""

    def __init__(self, color: bool):
        self.on = color

    def _c(self, code, s):
        return f"\x1b[{code}m{s}\x1b[0m" if self.on else str(s)

    def bold(self, s):
        return self._c(1, s)

    def dim(self, s):
        return self._c(90, s)

    def warn(self, s):
        return self._c(33, s)

    def ok(self, s):
        return self._c(32, s)

    def name(self, s):
        return self._c(36, s)

    def status(self, s):
        return self._c(35, s)


def _wrap(body: str, indent: str = "  ", width: int = 78) -> str:
    out = []
    for para in body.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if any(ln.lstrip().startswith(("-", "*", "```", "|")) for ln in para.splitlines()):
            out.extend(indent + ln for ln in para.splitlines())
        else:
            out.extend(textwrap.wrap(para.replace("\n", " "), width,
                                     initial_indent=indent,
                                     subsequent_indent=indent))
        out.append("")
    return "\n".join(out).rstrip("\n")


def render_text(w: Welcome, derived: dict, color: bool = False) -> str:
    s = _Style(color)
    today = derived.get("today") or _dt.date.today().isoformat()
    git = derived.get("git")
    lines = []

    name = w.meta.get("name", "?")
    tagline = w.meta.get("tagline", "")
    status = w.meta.get("status", "")
    updated = w.meta.get("updated", "")

    lines.append("")
    head = s.bold(name)
    if tagline:
        head += f" — {tagline}"
    lines.append(head)

    facts = []
    if status:
        facts.append(s.status(status))
    if git:
        facts.append(f"last commit {s.bold(days_ago(git['last_date'], today))}")
    else:
        facts.append("not a git repository")
    if updated:
        facts.append(f"WELCOME.md updated {s.bold(days_ago(updated, today))}")
    lines.append(f" {s.dim('·')} ".join(facts))
    if derived.get("root"):
        reg = ("" if derived.get("registered", True)
               else " · not in your portfolio — `unpark register` adds it")
        lines.append(s.dim(str(derived["root"]) + reg))
    lines.append("")

    if derived.get("stale_count"):
        n = derived["stale_count"]
        lines.append(s.warn(
            f"⚠ WELCOME.md may be stale: {n} commit{'s' if n != 1 else ''} "
            "since it was last updated"))
        if derived.get("stale_latest"):
            lines.append(s.warn(f'  latest: "{derived["stale_latest"]}"'))
        lines.append("")

    for sec in w.sections:
        title = sec.title.strip()
        if title.lower() == "recipes":
            continue  # rendered as a table below
        heading = s.bold(title.upper())
        if title.lower() == "goals":
            heading += f"  {s.ok(w.goals_done)}/{w.goals_total} done"
            body = re.sub(r"^(\s*[-*]\s*)\[([xX])\]", r"\1[x]", sec.body,
                          flags=re.MULTILINE)
        else:
            body = sec.body
        lines.append(heading)
        lines.append(_wrap(body))
        lines.append("")

    if w.recipes:
        lines.append(s.bold("RECIPES") + s.dim(
            "        unpark run <name> · unpark start <name>"))
        width = max(len(r.name) for r in w.recipes)
        for r in w.recipes:
            tags = []
            if r.background:
                tags.append("background")
            if r.url:
                tags.append(r.url)
            tag = s.dim(" · ".join(tags)) if tags else ""
            desc = r.description or s.dim("(no description)")
            lines.append(f"  {s.name(r.name.ljust(width))}  {desc}"
                         + (f"  {tag}" if tag else ""))
        lines.append("")

    runners = derived.get("runners") or []
    if runners:
        lines.append(s.bold("TASK RUNNERS") + s.dim(
            "   recipes delegate to these — full task catalogs live there"))
        for ru in runners:
            count = f" ({ru['count']})" if ru.get("count") else ""
            lines.append(f"  {s.name(ru['name'])}{count}  {ru['file']}"
                         f"  {s.dim('— ' + ru['hint'])}")
        lines.append("")

    lines.append(s.bold("GIT"))
    if git:
        dirty = (s.warn(f"{git['dirty']} uncommitted change"
                        + ("s" if git["dirty"] != 1 else ""))
                 if git["dirty"] else s.ok("clean"))
        lines.append(f"  branch {s.name(git['branch'])} · {dirty} · "
                     f"last: {git['last_date']} \"{git['last_subject']}\"")
    else:
        lines.append("  not a git repository")
    lines.append("")

    lines.append(s.bold("RUNNING"))
    running = derived.get("running") or []
    if running:
        for p in running:
            entry = f"  {s.name(p['recipe'])}  pid {p['pid']}"
            if p.get("attached"):
                entry += s.dim("  (attached to its start terminal)")
            if p.get("url"):
                entry += f"  {p['url']}"
            lines.append(entry)
    else:
        hint = next((r.name for r in w.recipes if r.background), None)
        tip = f" — try: {s.bold('unpark start ' + hint)}" if hint else ""
        lines.append(f"  {s.dim('nothing running' + tip)}")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------- recipes


def find_recipe(w: Welcome, name: str):
    for r in w.recipes:
        if r.name == name:
            return r
    return None


def _step_shell(command: str, cwd, env=None) -> int:
    """Run one fenced step through the shell, streaming output."""
    # our own headers must reach the pipe before the child writes to it
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":
        # cmd.exe treats a newline in a command string as the end of the
        # command passed through /c. Normalise a fenced multi-line block into
        # an explicit chain so every line runs and errors stop the block.
        command = " && ".join(
            line.strip() for line in command.splitlines() if line.strip())
        p = subprocess.run(command, shell=True, cwd=str(cwd), env=env)
    else:
        p = subprocess.run(["/bin/sh", "-c", command], cwd=str(cwd), env=env)
    return p.returncode


def _load_dotenv(path) -> dict:
    envd = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" in line:
            k, v = line.split("=", 1)
            envd[k.strip()] = v.strip().strip('"').strip("'")
    return envd


def recipe_env(root, r: Recipe) -> "dict | None":
    """Environment for a recipe's steps: `dotenv:` file first, then
    `- env: KEY=val KEY2=val` pairs on top. None = inherit untouched."""
    dotenv = r.meta.get("dotenv")
    pairs = r.meta.get("env")
    if not dotenv and not pairs:
        return None
    env = dict(os.environ)
    if dotenv:
        f = Path(root) / dotenv
        if f.is_file():
            env.update(_load_dotenv(f))
    if pairs:
        for tok in shlex.split(str(pairs).replace(",", " ")):
            if "=" in tok:
                k, v = tok.split("=", 1)
                env[k] = v
    return env


def run_recipe(root, w: Welcome, name: str, _seen=None,
               extra_args=None) -> int:
    """Run a recipe's steps sequentially in the foreground.

    extra_args (from `unpark run <name> -- …`) are shell-quoted and
    appended to the LAST step — the natural place for delegations like
    `just test` or `pytest`."""
    r = find_recipe(w, name)
    if r is None:
        known = ", ".join(x.name for x in w.recipes) or "(none)"
        print(f"unpark: no recipe '{name}' (known: {known})", file=sys.stderr)
        return 2

    _seen = _seen or set()
    if name in _seen:
        print(f"unpark: dependency cycle at '{name}'", file=sys.stderr)
        return 2
    _seen.add(name)

    if r.needs:
        rc = run_recipe(root, w, r.needs, _seen)
        if rc != 0:
            return rc

    cwd = Path(root) / r.dir if r.dir else Path(root)
    if not cwd.is_dir():
        print(f"unpark: recipe '{name}' dir not found: {cwd}", file=sys.stderr)
        return 2
    # Fenced blocks conventionally end in a newline. Strip it before adding
    # passthrough arguments: on cmd.exe an argument after that newline becomes
    # a separate command rather than an argument to the recipe step.
    steps = [step.strip() for step in r.steps]
    if extra_args:
        quoted = (subprocess.list2cmdline(extra_args)
                  if os.name == "nt"
                  else " ".join(shlex.quote(a) for a in extra_args))
        steps[-1] = steps[-1] + " " + quoted
    env = recipe_env(root, r)
    for i, step in enumerate(steps, 1):
        print(f"[{name}] step {i}/{len(steps)}: {step.splitlines()[0]}"
              + (" …" if len(step.splitlines()) > 1 else ""))
        rc = _step_shell(step, cwd, env=env)
        if rc != 0:
            print(f"[{name}] step {i} failed with exit code {rc}",
                  file=sys.stderr)
            return rc
    return 0


def pick_recipe(w: Welcome, verb: str, ask=input) -> "str | None":
    """Interactive chooser when run/start is called without a recipe."""
    if not w.recipes:
        return None
    print("recipes:")
    for i, r in enumerate(w.recipes, 1):
        extra = "  (background)" if r.background else ""
        print(f"  {i}. {r.name}  {r.description}{extra}")
    try:
        ans = ask(f"{verb} which recipe? [number or name] ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(w.recipes):
        return w.recipes[int(ans) - 1].name
    return ans or None


# --------------------------------------------------------- html output

_HTML_CSS = """
:root { --bg:#f6f7fb; --card:#fff; --ink:#1d2333; --muted:#6b7280;
  --line:#e5e8f0; --accent:#3b5bdb; --warn-bg:#fff7e0; --warn-ink:#8a6100;
  --ok:#2f9e44; --pill:#e7ebff; --pill-ink:#3b5bdb; --code:#f1f3f9; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0e1220; --card:#171c2e; --ink:#dbe2f4; --muted:#8b94ad;
    --line:#262d45; --accent:#7aa2ff; --warn-bg:#33290e; --warn-ink:#ffd166;
    --ok:#69db7c; --pill:#24304f; --pill-ink:#9db4ff; --code:#10162a; } }
:root { color-scheme: light dark }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif }
button { font:inherit; font-size:12.5px; font-weight:600;
  padding:2px 12px; border-radius:6px; border:1px solid var(--line);
  background:var(--pill); color:var(--pill-ink); cursor:pointer }
button:hover:not(:disabled) { border-color:var(--accent) }
button:disabled { opacity:.45; cursor:default }
.wrap { max-width:860px; margin:0 auto; padding:32px 20px 60px }
h1 { margin:0; font-size:26px }
h1 small { font-weight:400; color:var(--muted); font-size:15px; margin-left:10px }
.facts { margin-top:6px; color:var(--muted); font-size:13.5px }
.facts b { color:var(--ink) }
.pill { display:inline-block; padding:1px 10px; border-radius:999px;
  background:var(--pill); color:var(--pill-ink); font-size:12.5px;
  font-weight:600; vertical-align:2px }
.stale { margin:18px 0 0; padding:10px 14px; border-radius:10px;
  background:var(--warn-bg); color:var(--warn-ink); font-size:14px }
section { background:var(--card); border:1px solid var(--line);
  border-radius:12px; padding:18px 20px; margin-top:18px }
h2 { margin:0 0 10px; font-size:13px; letter-spacing:.08em;
  text-transform:uppercase; color:var(--muted) }
p { margin:8px 0 }
ul { margin:8px 0; padding-left:22px }
ul.goals { list-style:none; padding:0 }
ul.goals li { padding:3px 0 }
ul.goals .done { color:var(--muted); text-decoration:line-through }
ul.goals .box { display:inline-block; width:1.4em; font-weight:700 }
ul.goals .done .box { color:var(--ok); text-decoration:none }
.progress { float:right; font-size:13px; color:var(--muted) }
table { width:100%; border-collapse:collapse }
td { padding:8px 6px; border-top:1px solid var(--line); vertical-align:top }
tr:first-child td { border-top:none }
td.rname { font-weight:600; color:var(--accent); white-space:nowrap;
  width:1%; padding-right:16px }
td.rtags { white-space:nowrap; color:var(--muted); font-size:12.5px;
  text-align:right; width:1% }
code, pre { font:13px/1.5 ui-monospace,Consolas,monospace;
  background:var(--code); border-radius:6px }
code { padding:1px 6px }
pre { padding:10px 14px; overflow-x:auto; margin:8px 0 0 }
.cmd { color:var(--muted); font-size:13px; margin-top:4px }
footer { margin-top:26px; color:var(--muted); font-size:12.5px;
  text-align:center }
nav.pagenav { float:right; font-size:13px; color:var(--muted) }
nav.pagenav a { color:var(--accent); text-decoration:none;
  padding:2px 6px; border-radius:6px }
nav.pagenav a:hover { background:var(--pill) }
.cols { display:flex; gap:18px; align-items:flex-start }
.cards { flex:1; min-width:0 }
nav.sidebar { position:sticky; top:14px; flex:0 0 170px;
  display:flex; flex-direction:column; gap:1px; padding:8px 0;
  max-height:calc(100vh - 28px); overflow-y:auto }
nav.sidebar a { color:var(--muted); text-decoration:none;
  font-size:13px; padding:3px 10px; border-radius:6px;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
nav.sidebar a:hover { color:var(--pill-ink); background:var(--pill) }
@media (max-width:700px) { nav.sidebar { display:none } }
"""


def _md_html(body: str, defs: bool = False) -> str:
    """Tiny markdown subset → HTML (everything escaped first).

    With defs=True, bullet items shaped like `command` — description
    render as an aligned two-column definition list (ul.def) instead of
    a plain bullet list; other items in the same list span both columns.
    """
    out = []
    fence = None
    paras = []
    items = None  # current list's raw item texts, or None

    def _esc_inline(s):
        s = _html.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*([^*\s][^*]*)\*", r"<em>\1</em>", s)
        return s

    def flush_paras():
        if paras:
            text = _esc_inline(" ".join(paras))
            out.append(f"<p>{text}</p>")
            paras.clear()

    def flush_list():
        nonlocal items
        if items is None:
            return
        matches = ([re.match(r"^`([^`]+)`\s+—\s+(.*)$", it) for it in items]
                   if defs else [None] * len(items))
        if any(matches):
            out.append('<ul class="def">')
            for it, m in zip(items, matches):
                if m:
                    out.append('<li><span class="k"><code>'
                               + _html.escape(m.group(1))
                               + '</code></span><span class="v">'
                               + _esc_inline(m.group(2)) + "</span></li>")
                else:
                    out.append(f'<li class="plain">{_esc_inline(it)}</li>')
            out.append("</ul>")
        else:
            out.append("<ul>")
            for it in items:
                out.append(f"<li>{_esc_inline(it)}</li>")
            out.append("</ul>")
        items = None

    for line in body.splitlines() + [""]:
        if fence is not None:
            if line.strip().startswith("```"):
                out.append("<pre>" + _html.escape("\n".join(fence)) + "</pre>")
                fence = None
            else:
                fence.append(line)
            continue
        if line.strip().startswith("```"):
            flush_paras()
            flush_list()
            fence = []
            continue
        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            flush_paras()
            if items is None:
                items = []
            items.append(m.group(1))
            continue
        if items is not None and line.strip() and line[:1] in (" ", "\t"):
            items[-1] += " " + line.strip()  # wrapped bullet continuation
            continue
        flush_list()
        if not line.strip():
            flush_paras()
        else:
            paras.append(line.strip())
    flush_list()
    return "\n".join(out)


def _goals_html(body: str) -> str:
    items = []
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s*\[([ xX])\]\s*(.*)$", line)
        if m:
            done = bool(m.group(1).strip())
            cls = ' class="done"' if done else ""
            box = "✓" if done else "○"
            items.append(f'<li{cls}><span class="box">{box}</span>'
                         f"{_html.escape(m.group(2))}</li>")
    return '<ul class="goals">' + "\n".join(items) + "</ul>"


_DASH_JS = """
<script>
const TOKEN = "%TOKEN%";
const HDRS = {"X-Unpark-Token": TOKEN};
async function api(path, method) {
  try {
    const r = await fetch(path, {method: method || "GET", headers: HDRS});
    return r.ok ? await r.json() : null;
  } catch (e) { return null; }
}
function esc(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
async function tick() {
  const s = await api("/api/status");
  if (!s) {
    document.getElementById("running").innerHTML =
      "<li><i>server gone — reopen with `unpark html`</i></li>";
    return;
  }
  const el = document.getElementById("running");
  el.innerHTML = s.running.length
    ? s.running.map(p => "<li><b>" + esc(p.recipe) + "</b> pid " + p.pid +
        (p.url ? " — <a href=\\"" + esc(p.url) +
                 "\\" target=\\"_blank\\" rel=\\"noopener\\">" +
                 esc(p.url) + "</a>" : "") +
        (p.log ? " — <a href=\\"/logs/" + esc(p.recipe) +
                 "\\" target=\\"_blank\\" rel=\\"noopener\\">logs</a>"
               : " <i>(attached — output in the unpark terminal)</i>") +
        " <button onclick=\\"doStop('" + esc(p.recipe) + "')\\">stop</button></li>"
      ).join("")
    : "<li>nothing running</li>";
  document.querySelectorAll("button[data-recipe]").forEach(b => {
    b.disabled = s.running.some(p => p.recipe === b.dataset.recipe);
  });
  renderLogFiles(s);
  setupTick(s);
}
function renderLogFiles(s) {
  const lf = document.getElementById("logfiles");
  if (!lf) return;
  lf.innerHTML = (s.logs && s.logs.length)
    ? "all logs: " + s.logs.map(n =>
        '<a href="/logs/' + esc(n) + '" target="_blank" rel="noopener">' +
        esc(n) + "</a>").join(" · ")
    : "";
}
async function doStart(n, ev) {
  // shift-click: run ATTACHED to the terminal unpark was started in
  // (output and keyboard input there); plain click: managed + logged
  const attached = ev && ev.shiftKey ? "?attached=1" : "";
  await api("/api/start/" + n + attached, "POST");
  tick();
}
async function doStop(n)  { await api("/api/stop/" + n, "POST"); tick(); }
function setBadge(id, on, yes, no) {
  const el = document.getElementById(id);
  if (el) { el.textContent = on ? yes : no; el.dataset.on = on ? "1" : "0"; }
}
function setupTick(s) {
  setBadge("su-reg", s.registered, "in your portfolio", "not in your portfolio");
  setBadge("su-local", s.local_installed, "installed", "not installed");
  const ln = document.getElementById("su-local-note");
  if (ln) ln.textContent = (s.global_installed && !s.local_installed)
    ? "— covered by your global install; only needed for teammates " +
      "and non-Claude tools"
    : "";
  setBadge("su-global", s.global_installed, "installed", "not installed");
  const rb = document.querySelector('button[data-setup="register"]');
  if (rb) rb.textContent = s.registered ? "remove" : "add";
  const lb = document.querySelector('button[data-setup="skill-local"]');
  if (lb) lb.disabled = !!s.local_installed;
  const gb = document.querySelector('button[data-setup="skill-global"]');
  if (gb) gb.disabled = !!s.global_installed;
  const sc = document.getElementById("su-code");
  if (sc && s.code_target)
    sc.textContent = s.code_kind + " — " + s.code_target;
  const wsb = document.querySelector('button[data-setup="ws-init"]');
  if (wsb) wsb.style.display = s.code_kind === "folder" ? "" : "none";
}
async function setup(kind) {
  if (kind === "ws-init" && !window.confirm(
      "Create a .code-workspace file in the repo root?\\n\\n" +
      "This migrates the project to workspace-based opening. " +
      "Registration and unpark state are untouched.\\n\\n" +
      "IMPORTANT: VS Code chat history (Copilot/Claude sessions) does " +
      "NOT carry over to an externally created workspace file. If those " +
      "sessions matter, cancel and instead convert inside VS Code: " +
      "File \\u2192 Save Workspace As\\u2026 into the repo root \\u2014 " +
      "unpark detects the result automatically.")) return;
  const reg = document.getElementById("su-reg");
  const path = kind === "skill-local" ? "/api/skill/local"
             : kind === "skill-global" ? "/api/skill/global"
             : kind === "code" ? "/api/code"
             : kind === "ws-init" ? "/api/workspace/init"
             : (reg && reg.dataset.on === "1" ? "/api/unregister"
                                              : "/api/register");
  await api(path, "POST");
  tick();
}
setInterval(tick, 2000);   // heartbeat: server exits when these stop
tick();
</script>
"""


def render_html(w: Welcome, derived: dict, token: "str | None" = None) -> str:
    e = _html.escape
    today = derived.get("today") or _dt.date.today().isoformat()
    git = derived.get("git")
    name = w.meta.get("name", "?")
    tagline = w.meta.get("tagline", "")
    status = w.meta.get("status", "")
    updated = w.meta.get("updated", "")

    facts = []
    if git:
        facts.append(f"last commit <b>{e(days_ago(git['last_date'], today))}</b>")
        facts.append(f"branch <b>{e(git['branch'])}</b>")
        if git["dirty"]:
            facts.append(f"<b>{git['dirty']} uncommitted change"
                         + ("s" if git["dirty"] != 1 else "") + "</b>")
    else:
        facts.append("not a git repository")
    if updated:
        facts.append(f"WELCOME.md updated <b>{e(days_ago(updated, today))}</b>")

    parts = ['<div class="wrap">', "<header>"]
    if token:
        # served mode: the page is an app — offer navigation up front
        parts.append('<nav class="pagenav">'
                     '<a href="/projects">your projects</a> · '
                     '<a href="/manual">manual</a></nav>')
    pill = f' <span class="pill">{e(status)}</span>' if status else ""
    small = f" <small>{e(tagline)}</small>" if tagline else ""
    parts.append(f"<h1>{e(name)}{pill}{small}</h1>")
    parts.append(f'<div class="facts">{" · ".join(facts)}</div>')
    if derived.get("stale_count"):
        n = derived["stale_count"]
        latest = derived.get("stale_latest", "")
        parts.append(f'<div class="stale">⚠ WELCOME.md may be stale — {n} '
                     f'commit{"s" if n != 1 else ""} since it was last updated'
                     + (f" (latest: “{e(latest)}”)" if latest else "") + "</div>")
    if token and is_skeleton_welcome(w):
        # an unfilled template: the fill-it instruction IS the page's
        # point — banner first, placeholder sections below
        parts.append(
            '<div class="stale" style="background:var(--pill);'
            'color:var(--pill-ink)">📝 This briefing is an unfilled '
            f"template. {_phrase_tip_html(_skill_covered(derived.get('root')))}"
            "</div>")
    parts.append("</header>")

    for sec in w.sections:
        t = sec.title.strip()
        low = t.lower()
        parts.append("<section>")
        if low == "goals":
            parts.append(f"<h2>{e(t)} <span class='progress'>"
                         f"{w.goals_done} / {w.goals_total} done</span></h2>")
            parts.append(_goals_html(sec.body))
        elif low == "recipes":
            parts.append(f"<h2>{e(t)}</h2>")
            parts.append("<table>")
            for r in w.recipes:
                verb = "start" if r.background else "run"
                cmd = f"unpark {verb} {r.name}"
                # pages may be served ephemerally (no back button):
                # links always open a fresh tab
                url = (f' → <a href="{e(r.url)}" target="_blank" '
                       f'rel="noopener">{e(r.url)}</a>'
                       if r.url else "")
                tags = " · ".join(filter(None, [
                    "background" if r.background else "",
                    f"dir: {e(r.dir)}" if r.dir else "",
                    f"needs: {e(r.needs)}" if r.needs else "",
                ]))
                button = ""
                if token:
                    safe = e(r.name).replace("'", "&#39;")
                    button = (f'<button data-recipe="{e(r.name)}" '
                              f'onclick="doStart(\'{safe}\', event)" '
                              'title="click: managed — survives this page; '
                              "output goes to a log file (a ‘logs’ "
                              "link appears in the Running panel) · "
                              'shift-click: attached — output and keyboard '
                              'input in the unpark terminal">'
                              f"{verb}</button> ")
                parts.append(
                    f'<tr><td class="rname">{e(r.name)}</td>'
                    f"<td>{_esc_or_dim(r.description)}"
                    f'<div class="cmd">{button}terminal: <code>{e(cmd)}</code>{url}</div></td>'
                    f'<td class="rtags">{tags}</td></tr>')
            parts.append("</table>")
            runners = derived.get("runners") or []
            if runners:
                items = " · ".join(
                    f"<b>{e(ru['name'])}</b>"
                    + (f" ({ru['count']})" if ru.get("count") else "")
                    + f" — <code>{e(ru['hint'])}</code>"
                    for ru in runners)
                parts.append(
                    f'<div class="cmd">task runners here: {items} '
                    "· recipes delegate to these; the full catalogs "
                    "live there</div>")
        else:
            parts.append(f"<h2>{e(t)}</h2>")
            parts.append(_md_html(sec.body))
        parts.append("</section>")

    if token:
        parts.append('<section><h2>Running</h2>'
                     '<ul id="running" class="goals"><li>…</li></ul>'
                     '<p class="cmd" id="logfiles"></p></section>')
        # everything doable by button — no command line required
        parts.append(
            '<section><h2>Welcome setup</h2><ul class="goals">'
            '<li>your portfolio: <b id="su-reg" data-on="0">…</b> '
            '<button data-setup="register" onclick="setup(\'register\')">…'
            "</button></li>"
            '<li>LLM upkeep skill, this project: <b id="su-local">…</b> '
            '<button data-setup="skill-local" '
            'onclick="setup(\'skill-local\')">install</button> '
            '<i id="su-local-note" class="cmd"></i></li>'
            '<li>LLM upkeep skill, all your projects: <b id="su-global">…</b> '
            '<button data-setup="skill-global" '
            'onclick="setup(\'skill-global\')">install</button></li>'
            '<li>VS Code: <b id="su-code">…</b> '
            '<button data-setup="code" onclick="setup(\'code\')" '
            'title="opens the workspace file if the project has one, '
            'else the folder">open</button> '
            '<button data-setup="ws-init" onclick="setup(\'ws-init\')" '
            'style="display:none" title="folder→workspace migration: '
            "creates <name>.code-workspace in the repo root; add further "
            "folders in VS Code and save — registration stays untouched. "
            "Caveat: VS Code chat history only carries over when you "
            "convert inside VS Code (File → Save Workspace As…) — use "
            'that route instead if your Copilot/Claude sessions matter">'
            "create workspace file</button></li>"
            "<li>LLM instructions for any other agent: "
            '<button data-setup="copy" onclick="copyInstr(this)">'
            "copy to clipboard</button></li>"
            "</ul></section>")
        # the project comes first; the tool introduces itself quietly below
        parts.append(
            '<p class="cmd" style="text-align:center;margin-top:22px">'
            "This page is served by <b>unpark</b> — a re-entry briefing "
            "for parked projects, generated from this repo's WELCOME.md. "
            '<a href="/manual" target="_blank" rel="noopener">Open the '
            "manual</a></p>")

    parts.append(f"<footer>generated by <b>unpark</b> v{__version__} · "
                 f"{e(today)} · source: WELCOME.md + git</footer>")
    parts.append("</div>")
    if token:
        parts.append(_DASH_JS.replace("%TOKEN%", token))
        parts.append(_copy_js())

    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{e(name)} — unpark</title>\n<style>{_HTML_CSS}</style>\n"
        "</head>\n<body>\n" + "\n".join(parts) + "\n</body>\n</html>\n"
    )


def _esc_or_dim(desc: str) -> str:
    return _html.escape(desc) if desc else "<i>(no description)</i>"


# ------------------------------------------------------ browser open


def _is_wsl(proc_version: "str | None" = None) -> bool:
    if proc_version is None:
        try:
            proc_version = Path("/proc/version").read_text()
        except OSError:
            proc_version = ""
    return "microsoft" in proc_version.lower()


def _browser_command(url: str, browser_env: "str | None" = None,
                     is_wsl: "bool | None" = None, have=None):
    """Command to open url in the user's *real* browser, or None to let
    the stdlib webbrowser module handle it.

    Inside WSL2 the stdlib finds a Linux browser under WSLg — wrong
    cursor theme, no knowledge of the Windows dark-mode setting. The
    user's actual browser lives on the Windows side, so hand the URL
    over: wslview if installed, else cmd.exe's `start`.
    """
    if browser_env is None:
        browser_env = os.environ.get("BROWSER", "")
    if browser_env:
        return None  # explicit user choice: let webbrowser honor it
    if is_wsl is None:
        is_wsl = _is_wsl()
    if have is None:
        import shutil
        have = lambda name: shutil.which(name) is not None  # noqa: E731
    if is_wsl:
        if have("wslview"):
            return ["wslview", url]
        if have("cmd.exe"):
            # empty "" is start's window-title argument
            return ["cmd.exe", "/c", "start", "", url]
    return None


def open_browser(url: str) -> None:
    cmd = _browser_command(url)
    if cmd:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             cwd=_home())  # cmd.exe dislikes WSL cwd
            return
        except OSError:
            pass
    import webbrowser
    webbrowser.open(url)


# ------------------------------------------------- ephemeral serving


def serve_dashboard(root, w: Welcome, timeout: float = 120.0,
                    grace: float = 8.0, on_bound=None) -> bool:
    """Serve an interactive briefing until the page is closed.

    No file touches disk: the program is the server. The page's JS polls
    /api/status every 2s — that poll is the heartbeat. When the tab
    closes (no heartbeat for `grace` seconds) the server exits; managed
    processes it started keep running (they are detached).

    API (all guarded by a per-session token in a custom header, so a
    malicious webpage cannot blindly POST to localhost ports):
      GET  /api/status          → {"running": [...]}
      POST /api/start/<recipe>  → {"ok": bool}
      POST /api/stop/<recipe>   → {"ok": bool}

    Returns True if the page was opened at least once.
    """
    token = secrets.token_urlsafe(16)
    lock = threading.Lock()
    state = {"opened": False, "last_seen": time.monotonic()}

    def seen(opened=False):
        with lock:
            state["last_seen"] = time.monotonic()
            if opened:
                state["opened"] = True

    class Handler(http.server.BaseHTTPRequestHandler):
        def _authed(self):
            supplied = (self.headers.get("X-Unpark-Token")
                        or self.headers.get("X-Welcome-Token"))
            return supplied == token

        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            if not _safe_host_header(self.headers.get("Host", "")):
                return self.send_error(421, "invalid Host header")
            if self.path in ("/", "/index.html"):
                # re-render every load: refresh shows fresh git facts
                page = render_html(w, _derived(root, w), token=token)
                self._send(200, page.encode("utf-8"),
                           "text/html; charset=utf-8")
                seen(opened=True)
            elif self.path == "/manual":
                # the tool's manual rides on the same server so the
                # dashboard can link to it; same heartbeat keeps both alive
                page = render_docs_html(_docs_context(root), token=token)
                self._send(200, page.encode("utf-8"),
                           "text/html; charset=utf-8")
                seen(opened=True)
            elif self.path == "/projects":
                regs = gather_registered()
                page = render_portfolio_html(
                    regs, root, token=token,
                    title=f"{len(regs)} registered project"
                          f"{'s' if len(regs) != 1 else ''}",
                    nav=[("/", "project"), ("/manual", "manual")])
                self._send(200, page.encode("utf-8"),
                           "text/html; charset=utf-8")
                seen(opened=True)
            elif self.path.startswith("/logs/"):
                # read-only, same trust level as the dashboard page itself
                name = self.path[len("/logs/"):]
                logf = state_dir(root) / f"{name}.log"
                if "/" in name or not logf.is_file():
                    return self.send_error(404)
                tail = logf.read_text(errors="replace").splitlines()[-200:]
                body = (f"log: {logf}\n(last {len(tail)} lines — refresh "
                        "for more)\n\n" + "\n".join(tail) + "\n").encode()
                self._send(200, body, "text/plain; charset=utf-8")
                seen()
            elif self.path == "/api/status":
                if not self._authed():
                    return self._json({"error": "forbidden"}, 403)
                ctx = _docs_context(root)
                target = vscode_target(root, w)
                self._json({"running": list_running(root),
                            "logs": log_names(root),
                            "has_project": ctx["root"] is not None,
                            "global_installed": ctx["global_installed"],
                            "local_installed": ctx["local_installed"],
                            "registered": registry_has(root),
                            "code_kind": ("workspace" if str(target).endswith(
                                ".code-workspace") else "folder"),
                            "code_target": str(target)})
                seen()
            else:
                self.send_error(404)

        def do_POST(self):
            if not _safe_host_header(self.headers.get("Host", "")):
                return self.send_error(421, "invalid Host header")
            if not self._authed():
                return self._json({"error": "forbidden"}, 403)
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            m = re.match(r"^/api/(start|stop)/([^/]+)$", parsed.path)
            if m:
                action, name = m.group(1), m.group(2)
                if action == "start":
                    attached = parse_qs(parsed.query).get("attached", ["0"])[0]
                    rc = start_recipe(root, w, name,
                                      attached=attached == "1")
                else:
                    rc = stop_recipe(root, name)
                sys.stdout.flush()
                self._json({"ok": rc == 0})
                return seen()
            if self.path == "/api/skill/global":
                rc = cmd_skill(root, False, True)
                sys.stdout.flush()
                self._json({"ok": rc == 0, "wrote": [str(
                    _home() / ".claude" / "skills" / "unpark-upkeep"
                    / "SKILL.md")]})
                return seen()
            if self.path == "/api/skill/local":
                rc = cmd_skill(root, True, False)
                sys.stdout.flush()
                self._json({"ok": rc == 0, "wrote": [
                    str(Path(root) / "AGENTS.md"),
                    str(Path(root) / ".claude" / "skills" / "unpark-upkeep"
                        / "SKILL.md")]})
                return seen()
            if self.path == "/api/register":
                registry_add(root)
                self._json({"ok": True})
                return seen()
            if self.path == "/api/unregister":
                registry_remove(root)
                self._json({"ok": True})
                return seen()
            if self.path.startswith("/api/open"):
                q = parse_qs(urlparse(self.path).query)
                code, obj = _open_api_response((q.get("p") or [""])[0])
                self._json(obj, code)
                return seen()
            if self.path.startswith("/api/code"):
                q = parse_qs(urlparse(self.path).query)
                code, obj = _code_api_response((q.get("p") or [""])[0], root)
                self._json(obj, code)
                return seen()
            if self.path == "/api/workspace/init":
                res = create_workspace_file(root, w)
                self._json(res, 200 if res["ok"] else 400)
                return seen()
            self.send_error(404)

        def log_message(self, *args):  # keep the terminal clean
            pass

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def server_bind(self):
            """Bind without reverse-DNS lookup of the loopback address."""
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    with Server(("127.0.0.1", 0), Handler) as srv:
        srv.timeout = 0.25  # poll so deadlines are honored
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        if on_bound:
            on_bound(url)
        started = time.monotonic()
        while True:
            srv.handle_request()
            now = time.monotonic()
            with lock:
                opened, last = state["opened"], state["last_seen"]
            if not opened:
                if now - started > timeout:
                    return False
            elif now - last > grace:
                return True


# -------------------------------------------------- managed processes


def state_dir(root) -> Path:
    """Per-project state dir for pidfiles and logs (never inside the repo)."""
    base = _env("STATE_DIR")
    if base:
        base = Path(base)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", _home())) / "unpark"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME",
                                   _home() / ".local" / "state")) / "unpark"
    resolved = str(Path(root).resolve())
    tag = hashlib.sha1(resolved.encode()).hexdigest()[:8]
    d = base / f"{Path(resolved).name}-{tag}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    try:
        # reap if it is our own exited child (else it lingers as a zombie
        # and os.kill would report it alive)
        done, _ = os.waitpid(int(pid), os.WNOHANG)
        if done:
            return False
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _proc_file(root, name: str) -> Path:
    return state_dir(root) / f"{name}.json"


def list_running(root) -> list:
    """Live managed processes; stale pidfiles are cleaned up."""
    running = []
    for f in sorted(state_dir(root).glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if pid_alive(rec.get("pid", -1)):
            running.append(rec)
        else:
            f.unlink(missing_ok=True)
    return running


def log_names(root) -> list:
    """Recipes with a log file in the state dir (running or not)."""
    try:
        return sorted(f.stem for f in state_dir(root).glob("*.log"))
    except OSError:
        return []


# --------------------------------------------------------- task runners

# Complementary tools: they own the full task catalog of a project;
# unpark recipes stay a curated handful of re-entry verbs that DELEGATE
# to them. Detection is by their well-known files.
_RUNNER_SPECS = (
    ("just", ("justfile", "Justfile", ".justfile"), "just --list"),
    ("task", ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml",
              "taskfile.yaml"), "task --list"),
    ("mask", ("maskfile.md",), "mask --help"),
    ("mise", ("mise.toml", ".mise.toml"), "mise tasks"),
    ("make", ("Makefile", "makefile", "GNUmakefile"), "make <target>"),
    ("npm scripts", ("package.json",), "npm run"),
)


def detect_task_runners(root) -> list:
    found = []
    for name, files, hint in _RUNNER_SPECS:
        for f in files:
            p = Path(root) / f
            if not p.is_file():
                continue
            count = None
            if f == "package.json":
                try:
                    scripts = json.loads(p.read_text()).get("scripts") or {}
                except (OSError, ValueError):
                    break
                if not scripts:
                    break  # a package.json without scripts is not a runner
                count = len(scripts)
            found.append({"name": name, "file": f, "hint": hint,
                          "count": count})
            break
    return found


def start_recipe(root, w: Welcome, name: str, attached: bool = False) -> int:
    """Launch a recipe as a managed background process.

    attached=True keeps the process wired to this terminal: output
    streams here and it can read keyboard input, but it shares the
    terminal's fate (Ctrl+C hits it too) and writes no log file.
    """
    r = find_recipe(w, name)
    if r is None:
        known = ", ".join(x.name for x in w.recipes) or "(none)"
        print(f"unpark: no recipe '{name}' (known: {known})", file=sys.stderr)
        return 2

    pf = _proc_file(root, name)
    if pf.exists():
        try:
            old = json.loads(pf.read_text())
        except (OSError, ValueError):
            old = {}
        if pid_alive(old.get("pid", -1)):
            print(f"unpark: '{name}' already running (pid {old['pid']}) — "
                  f"unpark stop {name} first", file=sys.stderr)
            return 1

    if r.needs:
        rc = run_recipe(root, w, r.needs)
        if rc != 0:
            return rc

    cwd = Path(root) / r.dir if r.dir else Path(root)
    if not cwd.is_dir():
        print(f"unpark: recipe '{name}' dir not found: {cwd}", file=sys.stderr)
        return 2

    log = state_dir(root) / f"{name}.log"
    env = recipe_env(root, r)
    if os.name == "nt":
        script = " && ".join(
            line.strip() for step in r.steps
            for line in step.splitlines() if line.strip())
        # Track a Python wrapper rather than cmd.exe. cmd.exe can exit before
        # the recipe child, leaving a pidfile for a shell that is already gone.
        argv = [sys.executable, "-c",
                "import subprocess, sys; raise SystemExit("
                "subprocess.call(sys.argv[1], shell=True))", script]
        use_shell = False
    else:
        script = "set -e\n" + "\n".join(r.steps)
        argv, use_shell = ["/bin/sh", "-c", script], False
    if attached:
        # inherit this terminal: output visible here, stdin available.
        # A separate session makes later tree cleanup safe. Keyboard input
        # and output still use this terminal; main() forwards Ctrl+C cleanup.
        proc = subprocess.Popen(
            argv, shell=use_shell, cwd=str(cwd), env=env,
            start_new_session=(os.name != "nt"),
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                           if os.name == "nt" else 0),
        )
    else:
        logf = open(log, "ab")
        if os.name == "nt":
            proc = subprocess.Popen(
                argv, shell=use_shell, cwd=str(cwd), stdout=logf, stderr=logf,
                stdin=subprocess.DEVNULL, env=env,
                creationflags=getattr(subprocess,
                                      "CREATE_NEW_PROCESS_GROUP", 0),
            )
        else:
            proc = subprocess.Popen(
                argv, cwd=str(cwd), stdout=logf, stderr=logf, env=env,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        logf.close()

    # brief health check: catch commands that die immediately
    # (port in use, missing binary, syntax error) instead of
    # reporting a phantom success
    try:
        exit_code = proc.wait(timeout=0.7)
    except subprocess.TimeoutExpired:
        exit_code = None  # still running — the normal server case
    if exit_code is not None:
        if exit_code == 0:
            print(f"[{name}] finished immediately (exit 0) — "
                  "not a long-running process")
            if not r.background:
                print(f"[{name}] note: this recipe has no `background: true` "
                      f"in WELCOME.md — for one-shot recipes use "
                      f"`unpark run {name}`; add the flag if it should be "
                      "a managed server")
            return 0
        if attached:
            print(f"[{name}] died right after start (exit {exit_code}) — "
                  "output above", file=sys.stderr)
            return 1
        print(f"[{name}] died right after start (exit {exit_code}). "
              f"Last log lines ({log}):", file=sys.stderr)
        tail = log.read_text(errors="replace").splitlines()[-8:]
        for line in tail:
            print(f"  {line}", file=sys.stderr)
        return 1

    rec = {
        "recipe": name,
        "pid": proc.pid,
        "url": r.url,
        "log": None if attached else str(log),
        "attached": attached,
        "cwd": str(cwd),
        "started": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    pf.write_text(json.dumps(rec, indent=2))
    # From here state is tracked by pidfile rather than the Popen wrapper.
    # Prevent CPython's wrapper destructor from warning about an intentionally
    # managed child; pid_alive/stop_recipe own reaping and termination.
    proc._child_created = False
    if attached:
        _ATTACHED_PIDS.add(proc.pid)
    if attached:
        print(f"[{name}] started attached, pid {proc.pid} — output appears "
              "in this terminal, Ctrl+C here stops it too")
    else:
        print(f"[{name}] started, pid {proc.pid}, log: {log}")
    if r.url:
        print(f"[{name}] {r.url}")
    return 0


def _terminate(pid: int, attached: bool = False):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True)
        # taskkill returns before Windows has necessarily released the
        # child's working directory. Wait briefly so callers may safely
        # remove a TemporaryDirectory immediately afterwards.
        for _ in range(30):
            if not pid_alive(pid):
                break
            time.sleep(0.05)
        _ATTACHED_PIDS.discard(int(pid))
        return
    def _signal_it(sig):
        if attached and os.getsid(pid) != pid:
            # Attached shells share our terminal group, so killpg would also
            # kill unpark. Walk their child tree explicitly instead.
            descendants = []
            pending = [pid]
            while pending:
                parent = pending.pop()
                try:
                    found = subprocess.run(
                        ["ps", "-o", "pid=", "--ppid", str(parent)],
                        capture_output=True, text=True, timeout=1,
                    ).stdout.split()
                except (OSError, subprocess.TimeoutExpired):
                    found = []
                children = [int(value) for value in found
                            if value.isdigit()]
                descendants.extend(children)
                pending.extend(children)
            for child_pid in reversed(descendants):
                try:
                    os.kill(child_pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            return
        try:
            # Only signal a group we can prove was created as this child's
            # detached session. A stale/reused PID must never target an
            # unrelated process group (including unpark's own test shell).
            if os.getpgid(pid) != pid or os.getsid(pid) != pid:
                raise ProcessLookupError
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, sig)  # attached starts are no group leader
            except (ProcessLookupError, PermissionError):
                pass

    _signal_it(signal.SIGTERM)
    for _ in range(20):
        if not pid_alive(pid):
            _ATTACHED_PIDS.discard(int(pid))
            return
        time.sleep(0.1)
    _signal_it(signal.SIGKILL)


def stop_recipe(root, name=None) -> int:
    """Stop one managed process, or all of them when name is None."""
    targets = list_running(root)
    if name is not None:
        targets = [t for t in targets if t["recipe"] == name]
    if not targets:
        what = f"'{name}'" if name else "anything"
        print(f"unpark: {what} is not running", file=sys.stderr)
        return 1
    for t in targets:
        _terminate(t["pid"], attached=t.get("attached", False))
        _proc_file(root, t["recipe"]).unlink(missing_ok=True)
        print(f"[{t['recipe']}] stopped")
    return 0


# ------------------------------------------------------ init / check


_TEMPLATE = """\
---
name: {name}
tagline: One line: what is this project?
status: active
updated: {today}
---

## What is this

What problem this project solves and who it is for.

What exists today: the stack and its main parts.

## Goals

- [ ] First goal
- [ ] Second goal

## State of things

What was most recently completed or changed.

Known issues:
- Replace this with a real limitation, or write "None known."

Next step: replace this with the intended next move.

## Recipes

### demo

What someone should see when you show this project off.

- background: true
- url: http://localhost:8000

```sh
echo "replace me with the real demo command"
python3 -m http.server 8000
```

## Distribute

How to package and ship this to others.
"""


def cmd_init(root) -> int:
    target = Path(root) / "WELCOME.md"
    if target.exists():
        print(f"unpark: {target} already exists", file=sys.stderr)
        return 1
    target.write_text(_TEMPLATE.format(
        name=Path(root).resolve().name,
        today=_dt.date.today().isoformat(),
    ))
    print(f"created {target} — edit it, then run `unpark`")
    return 0


def cmd_demo(destination=None, html: bool = False,
             no_open: bool = False) -> int:
    """Build a fresh example; opening its dashboard is always explicit."""
    import tempfile

    temporary = None
    try:
        if destination:
            root = build_demo_project(destination)
        else:
            temporary = tempfile.TemporaryDirectory(prefix="unpark-demo-")
            root = build_demo_project(temporary.name)
    except FileExistsError as exc:
        print(f"unpark: {exc}", file=sys.stderr)
        return 1

    w = _load(root / "WELCOME.md")
    if not html:
        if destination:
            print(f"created demo project: {root}")
        else:
            print("disposable demo project (removed after this briefing)")
        print(render_text(w, _derived(root, w), _use_color(False)))
        if destination:
            print(f"\nOpen its dashboard: unpark -C {shlex.quote(str(root))} html")
        else:
            print("\nOpen a disposable dashboard: unpark demo --html")
        if temporary:
            temporary.cleanup()
        return 0

    def bound(url):
        print(f"disposable demo at {url} — removed when this page closes")
        sys.stdout.flush()
        if not no_open and not _env("NO_BROWSER"):
            open_browser(url)

    opened = serve_dashboard(root, w, on_bound=bound)
    temporary.cleanup()
    return 0 if opened else 1


# tell-tale placeholders from _TEMPLATE: while any survive, the briefing
# is an unfilled skeleton and the fill-it-with-your-LLM tip stays visible
_SKELETON_MARKERS = (
    "One line: what is this project?",
    "What problem this project solves and who it is for.",
    "replace me with the real demo command",
)


def is_skeleton_text(text: str) -> bool:
    return any(m in text for m in _SKELETON_MARKERS)


def is_skeleton_welcome(w: Welcome) -> bool:
    parts = ([str(v) for v in w.meta.values()]
             + [s.body for s in w.sections]
             + [step for r in w.recipes for step in r.steps])
    return any(m in p for p in parts for m in _SKELETON_MARKERS)


def _skill_covered(root=None) -> bool:
    home_skill = (_home() / ".claude" / "skills" / "unpark-upkeep"
                  / "SKILL.md")
    if home_skill.exists():
        return True
    return bool(root) and (Path(root) / ".claude" / "skills"
                           / "unpark-upkeep" / "SKILL.md").exists()


def _phrase_tip_html(covered: bool) -> str:
    return (
        "next: tell your LLM agent <b>“Set up unpark for this "
        "project”</b> <button data-copy=\"phrase\" onclick=\"copyText(this, "
        "'Set up unpark for this project')\">copy</button> to fill this "
        "skeleton from the repo. "
        + ("Your agent already knows the recipe — the unpark upkeep "
           "skill is installed."
           if covered else
           "Note: the unpark skill is <b>not installed yet</b> — use "
           "“install globally” first, or the agent won't know what the "
           "phrase means."))


def _init_api_response(start, loc: str) -> "tuple":
    """(http_code, json_obj) for creating a briefing from the browser.
    Only in a repo root (a .git directory) and only when no briefing
    resolves — the guard against littering arbitrary subdirectories."""
    start = Path(start)
    if find_project(start):
        return 400, {"ok": False, "error": "a briefing already exists here"}
    if not (start / ".git").exists():
        return 400, {"ok": False, "error": "not a repo root (no .git) — "
                     "create the briefing from the repo's top level"}
    if loc == "top":
        target = start / "WELCOME.md"
    elif loc == "docs":
        (start / "docs").mkdir(exist_ok=True)
        target = start / "docs" / "WELCOME.md"
    else:
        return 400, {"ok": False, "error": "loc must be top or docs"}
    target.write_text(_TEMPLATE.format(
        name=start.resolve().name,
        today=_dt.date.today().isoformat(),
    ))
    return 200, {"ok": True, "path": str(target)}


_CONFLICT_RE = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.MULTILINE)


def has_conflict_markers(text: str) -> bool:
    return bool(_CONFLICT_RE.search(text))


def _briefing_structure_warnings(w: Welcome) -> list:
    """Human-writing guardrails that become enforceable with --strict."""
    warnings = []
    sections = {section.title.strip().lower(): section
                for section in w.sections}
    what = sections.get("what is this")
    if what:
        paragraphs = [p for p in re.split(r"\n\s*\n", what.body)
                      if p.strip()]
        if len(paragraphs) > 2:
            warnings.append("'What is this' should use at most 2 paragraphs")
    state = sections.get("state of things")
    if state:
        if not re.search(r"(?im)^known (issues|limitations):", state.body):
            warnings.append("'State of things' needs a 'Known issues:' line")
        if not re.search(r"(?im)^next step:", state.body):
            warnings.append("'State of things' needs a 'Next step:' line")
    for title in ("what is this", "state of things"):
        section = sections.get(title)
        if not section:
            continue
        for paragraph in re.split(r"\n\s*\n", section.body):
            prose = " ".join(line.strip() for line in paragraph.splitlines()
                             if line.strip() and not line.lstrip().startswith(
                                 ("-", "*", "```")))
            sentences = re.findall(r"[.!?](?:\s|$)", prose)
            if len(sentences) > 3:
                warnings.append(f"'{section.title}' has a paragraph longer "
                                "than 3 sentences")
                break
    return warnings


def cmd_check(root, w: Welcome, welcome_file=None, strict: bool = False) -> int:
    errors, warnings = [], []
    welcome_file = Path(welcome_file) if welcome_file else Path(root) / "WELCOME.md"
    if has_conflict_markers(welcome_file.read_text()):
        errors.append("WELCOME.md contains unresolved merge conflict "
                      "markers (<<<<<<< / ======= / >>>>>>>)")
    if not w.meta.get("name"):
        warnings.append("front-matter has no `name:`")
    if not w.meta.get("updated"):
        warnings.append("front-matter has no `updated:` date — "
                        "staleness detection is off")
    if not w.recipes:
        warnings.append("no recipes — `unpark start/run` will do nothing")
    warnings.extend(_briefing_structure_warnings(w))
    names = {r.name for r in w.recipes}
    for r in w.recipes:
        if r.dir and not (Path(root) / r.dir).is_dir():
            errors.append(f"recipe '{r.name}': dir not found: {r.dir}")
        if r.needs and r.needs not in names:
            errors.append(f"recipe '{r.name}': needs unknown recipe "
                          f"'{r.needs}'")
        if not r.steps:
            warnings.append(f"recipe '{r.name}' has no command steps")
        dotenv = r.meta.get("dotenv")
        if dotenv and not (Path(root) / dotenv).is_file():
            warnings.append(f"recipe '{r.name}': dotenv file not found: "
                            f"{dotenv}")
    copy_states = (
        ("AGENTS.md", agents_block_state(Path(root) / "AGENTS.md"), ""),
        (".claude/skills/unpark-upkeep/SKILL.md",
         skill_copy_current(Path(root) / ".claude" / "skills"
                            / "unpark-upkeep" / "SKILL.md"), ""),
        ("global skill (~/.claude/skills)",
         skill_copy_current(_home() / ".claude" / "skills"
                            / "unpark-upkeep" / "SKILL.md"), " --global"),
    )
    for label, state, flag in copy_states:
        if state is False:
            warnings.append(f"{label} carries an outdated upkeep skill — "
                            f"rerun `unpark skill --install{flag}`")
    ws = w.meta.get("workspace")
    if ws and not (Path(root) / ws).is_file():
        errors.append(f"front-matter workspace file not found: {ws} — "
                      "VS Code would silently open the folder instead")
    ws_files = sorted(Path(root).glob("*.code-workspace"))
    if not ws and len(ws_files) > 1:
        names = ", ".join(f.name for f in ws_files)
        warnings.append(f"{len(ws_files)} .code-workspace files ({names}) — "
                        f"unpark opens the first; pin one with a "
                        "`workspace:` front-matter key")
    n, latest = commits_since(root, w.meta.get("updated", ""))
    if n:
        warnings.append(f"WELCOME.md may be stale: {n} commits since "
                        f"updated ({latest})")
    for msg in errors:
        print(f"error: {msg}")
    for msg in warnings:
        print(f"warning: {msg}")
    if not errors and not warnings:
        print("ok")
    return 1 if errors or (strict and warnings) else 0


# --------------------------------------------------------------- vscode


def vscode_target(root, w: Welcome) -> Path:
    """What `code` should open: an explicit or detected .code-workspace
    file (workspace-based project), else the project folder.

    Precedence: `workspace:` in front-matter (works for workspace files
    kept outside the repo) → *.code-workspace in the root → in .vscode/
    → the folder itself. A VS Code terminal does not expose the running
    workspace file in the environment, so detection is by convention.
    """
    # Resolve once at the boundary. macOS exposes /var through a symlink,
    # and Windows may use an 8.3 spelling for the same temporary directory.
    root = Path(root).resolve()
    ws = w.meta.get("workspace")
    if ws:
        p = Path(ws)
        if not p.is_absolute():
            p = root / p
        if p.is_file():
            return p.resolve()
    for pattern_dir in (root, root / ".vscode"):
        found = sorted(pattern_dir.glob("*.code-workspace"))
        if found:
            return found[0].resolve()
    return root


def code_command(root, w: Welcome) -> list:
    return ["code", str(vscode_target(root, w))]


def set_front_matter_key(welcome_file, key: str, value: str) -> None:
    """Insert or replace one `key: value` line in the front-matter,
    leaving everything else byte-identical."""
    f = Path(welcome_file)
    lines = f.read_text().splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        # no front-matter yet: create a minimal one
        f.write_text(f"---\n{key}: {value}\n---\n" + "".join(lines))
        return
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    else:
        return
    for i in range(1, end):
        if lines[i].split(":", 1)[0].strip() == key:
            lines[i] = f"{key}: {value}\n"
            break
    else:
        lines.insert(end, f"{key}: {value}\n")
    f.write_text("".join(lines))


def create_workspace_file(root, w: Welcome) -> "dict":
    """folder → workspace migration: write <name>.code-workspace in the
    repo root (the recommended home for it). The project root — and
    therefore the registry entry and state dir — stays the anchor;
    nothing else needs migrating."""
    existing = vscode_target(root, w)
    if str(existing).endswith(".code-workspace"):
        return {"ok": False,
                "error": f"already workspace-based: {Path(existing).name}"}
    name = w.meta.get("name") or Path(root).name
    ws = Path(root) / f"{name}.code-workspace"
    ws.write_text(json.dumps({"folders": [{"path": "."}]}, indent=2) + "\n")
    return {"ok": True, "target": str(ws)}


def open_vscode(project_root) -> "dict | None":
    """Open VS Code for a project; {'kind','target'} or None if no `code`."""
    f = find_welcome_file(project_root)
    try:
        w = parse_welcome(f.read_text()) if f else Welcome()
    except OSError:
        w = Welcome()
    target = vscode_target(project_root, w)
    kind = ("workspace" if str(target).endswith(".code-workspace")
            else "folder")
    try:
        subprocess.Popen(["code", str(target)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        return None
    return {"kind": kind, "target": str(target)}


# ------------------------------------------------------ project registry


def _config_base() -> Path:
    """User config base directory for unpark files."""
    base = _env("CONFIG_DIR")
    if base:
        return Path(base)
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", _home()))
    return Path(os.environ.get("XDG_CONFIG_HOME", _home() / ".config"))


def registry_file() -> Path:
    """User-level list of registered projects (portfolio from anywhere)."""
    return _config_base() / "unpark" / "projects.json"


def registry_load() -> list:
    source = registry_file()
    try:
        data = json.loads(source.read_text())
        return [Path(p) for p in data.get("projects", [])]
    except (OSError, ValueError):
        return []


def _registry_save(paths: list) -> None:
    f = registry_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(
        {"projects": sorted(str(p) for p in paths)}, indent=2) + "\n")


def registry_add(path) -> None:
    paths = set(registry_load())
    paths.add(Path(path).resolve())
    _registry_save(paths)


def registry_remove(path) -> None:
    target = Path(path).resolve()
    _registry_save([p for p in registry_load() if p != target])


def registry_has(path) -> bool:
    return Path(path).resolve() in registry_load()


def spawn_project_dashboard(project_root) -> "dict | None":
    """Launch `unpark html` for another project as a detached process.

    Returns {"url", "pid"} once the child's server is listening, or None
    if it failed to come up (no briefing there, python missing, …). The
    child is heartbeat-bound like any dashboard: it dies when its tab
    closes, independent of whoever spawned it.
    """
    try:
        log = state_dir(project_root) / "dashboard.log"
    except OSError:
        return None
    with open(log, "wb") as logf:
        child = subprocess.Popen(
            [sys.executable, "-m", "unpark", "-C", str(project_root),
             "html", "--no-open"],
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                           if os.name == "nt" else 0),
            env=dict(os.environ, UNPARK_NO_BROWSER="1"),
        )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        m = re.search(rb"http://127\.0\.0\.1:\d+/", log.read_bytes())
        if m:
            child._child_created = False
            return {"url": m.group(0).decode(), "pid": child.pid}
        if child.poll() is not None:
            child.wait()
            return None
        time.sleep(0.1)
    _terminate(child.pid)
    child.wait()
    return None


def _code_api_response(target: str, default_root=None) -> "tuple":
    """(http_code, json_obj) for the open-in-VS-Code API. Without a
    target it opens the server's own project; explicit targets must be
    registered (same opt-in rule as spawning dashboards)."""
    if target:
        if Path(target).resolve() not in registry_load():
            return 400, {"ok": False, "error": "not a registered project"}
        root = Path(target).resolve()
    elif default_root is not None:
        root = Path(default_root).resolve()
    else:
        return 400, {"ok": False, "error": "no project"}
    res = open_vscode(root)
    if res:
        return 200, {"ok": True, **res}
    return 500, {"ok": False, "error": "`code` not found on PATH"}


def _open_api_response(target: str) -> "tuple":
    """(http_code, json_obj) for the open-a-project's-dashboard API.
    Only registered projects may be launched — the API runs shell-free,
    but starting arbitrary directories' recipes-bearing dashboards
    should still be an explicit opt-in."""
    if not target or Path(target).resolve() not in registry_load():
        return 400, {"ok": False, "error": "not a registered project"}
    res = spawn_project_dashboard(Path(target).resolve())
    if res:
        return 200, {"ok": True, **res}
    return 500, {"ok": False, "error": "dashboard failed to start"}


def gather_registered() -> list:
    """Facts for every registered project, recency first; gone ones skipped."""
    facts = [_project_facts(p) for p in registry_load()]
    return sorted((f for f in facts if f),
                  key=lambda p: p["name"].lower())  # alphabetical overview


def select_registered_project(projects: list, selector: str):
    """Return every case-insensitive registered-name match."""
    return [p for p in projects if p["name"].casefold() == selector.casefold()]


def show_registered_project(project: dict, *, no_pager: bool,
                            no_color: bool) -> int:
    """Render one registered project's normal terminal briefing."""
    root = project["path"]
    welcome_file = find_welcome_file(root)
    if welcome_file is None:  # Registration can disappear between list/open.
        print(f"unpark: project no longer has a WELCOME.md: {root}",
              file=sys.stderr)
        return 1
    w = _load(welcome_file)
    display_text(render_text(w, _derived(root, w), _use_color(no_color)),
                 no_pager=no_pager)
    return 0


def pick_registered_project(projects: list, *, ask=None):
    """Ask for a project before paging output; blank means cancel."""
    ask = input if ask is None else ask
    print("Choose a registered project:")
    width = max(len(project["name"]) for project in projects)
    today = _dt.date.today().isoformat()
    for i, project in enumerate(projects, 1):
        age = (days_ago(project["last_date"], today)
               if project["last_date"] else "—")
        done, total = project["goals"]
        goals = f"{done}/{total}" if total else "—"
        print(f"  {i:>2}. {project['name'].ljust(width)}  "
              f"{project['status']} · {age} · {goals}")
    try:
        answer = ask("Project number (blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not answer:
        return None
    try:
        index = int(answer)
    except ValueError:
        return False
    return projects[index - 1] if 1 <= index <= len(projects) else False


# ----------------------------------------------------------- portfolio


def _project_facts(d) -> "dict | None":
    f = find_welcome_file(d)
    if f is None:
        return None
    try:
        w = parse_welcome(f.read_text())
    except OSError:
        return None
    info = git_info(d)
    stale, _ = commits_since(d, w.meta.get("updated", ""))
    code_target = vscode_target(d, w)
    is_ws = str(code_target).endswith(".code-workspace")
    return {
        "name": w.meta.get("name", Path(d).name),
        "path": Path(d),
        "code_kind": "workspace" if is_ws else "folder",
        "code_name": Path(code_target).name if is_ws else "",
        "status": w.meta.get("status", "?"),
        "tagline": w.meta.get("tagline", ""),
        "last_date": info["last_date"] if info else "",
        "dirty": info["dirty"] if info else 0,
        "stale_count": stale,
        "goals": (w.goals_done, w.goals_total),
        "running": [p["recipe"] for p in list_running(d)],
    }


def gather_portfolio(hub, extra=None) -> list:
    """Facts about child projects plus any extra (registered) paths,
    deduplicated, most recently touched first. Stale entries (a
    registered project that vanished) are skipped silently."""
    projects, seen = [], set()
    for d in [*find_child_projects(hub), *(extra or [])]:
        r = Path(d).resolve()
        if r in seen:
            continue
        seen.add(r)
        facts = _project_facts(r)
        if facts:
            projects.append(facts)
    return sorted(projects, key=lambda p: p["name"].lower())  # alphabetical overview


def render_portfolio_text(projects: list, hub, today: "str | None" = None,
                          color: bool = False,
                          title: "str | None" = None) -> str:
    s = _Style(color)
    today = today or _dt.date.today().isoformat()
    n = len(projects)
    header = (s.bold(title) if title else
              s.bold(f"{n} project{'s' if n != 1 else ''}")
              + f" under {Path(hub).resolve()}")
    lines = ["", header, ""]
    if not projects:
        return "\n".join(lines)
    wname = max(len(p["name"]) for p in projects)
    wstat = max(len(p["status"]) for p in projects)
    for p in projects:
        age = days_ago(p["last_date"], today) if p["last_date"] else "—"
        done, total = p["goals"]
        goals = f"{done}/{total}" if total else "  —"
        notes = []
        if p["running"]:
            notes.append(s.ok("▶ " + ", ".join(p["running"])))
        if p["stale_count"]:
            notes.append(s.warn(f"⚠ briefing stale ({p['stale_count']} "
                                f"commit{'s' if p['stale_count'] != 1 else ''} behind)"))
        if p["dirty"]:
            notes.append(s.dim(f"{p['dirty']} uncommitted"))
        # Keep identifying fields on their own stable row. Taglines and
        # warnings may be long, so putting them below cannot obscure names,
        # status, age, or goal numbers when the terminal wraps.
        lines.append("  ".join([
            f"  {s.name(p['name'].ljust(wname))}",
            s.status(p["status"].ljust(wstat)),
            age,
            f"goals {goals}",
        ]))
        if p["tagline"]:
            tagline = p["tagline"]
            lines.append("      " + s.dim(
                tagline if len(tagline) <= 68 else tagline[:67] + "…"))
        for note in notes:
            lines.append("      " + note)
    lines.append("")
    lines.append(s.dim("  unpark project NAME for a full briefing"))
    lines.append("")
    return "\n".join(lines)


_COPY_JS = """
<script>
const INSTR = %INSTR%;
async function copyText(btn, text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {  // clipboard API denied: fall back to execCommand
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  const old = btn.textContent;
  btn.textContent = "copied ✓";
  setTimeout(() => { btn.textContent = old; }, 2000);
}
async function copyInstr(btn) { return copyText(btn, INSTR); }
</script>
"""


def _copy_js() -> str:
    return _COPY_JS.replace("%INSTR%", json.dumps(_UPKEEP_TEXT))


_BEAT_JS = """
<script>
const TOKEN = "%TOKEN%";
/* heartbeat: keeps the ephemeral server alive while this page
   (reached via in-app navigation) is open */
setInterval(() => {
  fetch("/api/status", {headers: {"X-Unpark-Token": TOKEN}})
    .catch(() => {});
}, 2000);
async function openDash(btn) {
  // open the tab synchronously (user gesture) so popup blockers relax,
  // then point it at the spawned dashboard once we know its port
  const win = window.open("", "_blank");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "starting…";
  try {
    const r = await fetch("/api/open?p=" + encodeURIComponent(btn.dataset.path),
      {method: "POST", headers: {"X-Unpark-Token": TOKEN}});
    const j = r.ok ? await r.json() : null;
    if (j && j.ok) { win.location = j.url; btn.textContent = old; }
    else { win.close(); btn.textContent = "failed"; }
  } catch (e) { win.close(); btn.textContent = "failed"; }
  btn.disabled = false;
}
async function openCode(btn) {
  const old = btn.textContent;
  btn.textContent = "opening…";
  try {
    const r = await fetch("/api/code?p=" + encodeURIComponent(btn.dataset.code),
      {method: "POST", headers: {"X-Unpark-Token": TOKEN}});
    const j = r.ok ? await r.json() : null;
    btn.textContent = j && j.ok ? old : "failed";
  } catch (e) { btn.textContent = "failed"; }
}
</script>
"""


def render_portfolio_html(projects: list, hub,
                          today: "str | None" = None,
                          token: "str | None" = None,
                          title: "str | None" = None,
                          nav: "list | None" = None) -> str:
    e = _html.escape
    today = today or _dt.date.today().isoformat()
    heading = e(title) if title else (
        f"{len(projects)} project{'s' if len(projects) != 1 else ''} "
        f"under {e(str(Path(hub).resolve()))}")
    navline = ""
    if nav:
        links = " · ".join(f'<a href="{e(href)}">{e(label)}</a>'
                           for href, label in nav)
        navline = f'<nav class="pagenav">{links}</nav>'

    def _slug(name, i):
        base = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        return f"p-{base or i}"

    slugs = [_slug(p["name"], i) for i, p in enumerate(projects)]
    sidebar = ""
    if len(projects) > 5:  # many projects: a names-only jump list
        items = "".join(f'<a href="#{s}">{e(p["name"])}</a>'
                        for s, p in zip(slugs, projects))
        sidebar = f'<nav class="sidebar">{items}</nav>'

    parts = ['<div class="wrap">', "<header>", navline,
             f"<h1>portfolio <small>{heading}</small></h1>", "</header>",
             f'<div class="cols">{sidebar}<div class="cards">']
    for i, p in enumerate(projects):
        age = days_ago(p["last_date"], today) if p["last_date"] else "—"
        done, total = p["goals"]
        facts = [f"last commit <b>{e(age)}</b>"]
        if total:
            facts.append(f"goals <b>{done} / {total}</b>")
        if p["running"]:
            facts.append("running: <b>" + e(", ".join(p["running"])) + "</b>")
        if p["dirty"]:
            facts.append(f"<b>{p['dirty']} uncommitted</b>")
        parts.append(f'<section id="{slugs[i]}">')
        parts.append(f'<h1 style="font-size:19px">{e(p["name"])} '
                     f'<span class="pill">{e(p["status"])}</span> '
                     f"<small>{e(p['tagline'])}</small></h1>")
        parts.append(f'<div class="facts">{" · ".join(facts)}</div>')
        if p["stale_count"]:
            n = p["stale_count"]
            parts.append(f'<div class="stale">⚠ briefing stale — {n} '
                         f'commit{"s" if n != 1 else ""} behind</div>')
        opens = (f"opens as <b>workspace</b> "
                 f"(<code>{e(p['code_name'])}</code>)"
                 if p.get("code_kind") == "workspace"
                 else "opens as <b>folder</b>")
        parts.append(f'<div class="cmd"><code>{e(str(p["path"].resolve()))}'
                     f"</code> · {opens}</div>")
        launch = ""
        if token:
            launch = (f'<button data-path="{e(str(p["path"].resolve()))}" '
                      'onclick="openDash(this)">open dashboard</button> '
                      f'<button data-code="{e(str(p["path"].resolve()))}" '
                      'onclick="openCode(this)" title="workspace file if '
                      'the project has one, else the folder">VS Code'
                      "</button> ")
        parts.append(f'<div class="cmd" style="margin-top:8px">{launch}'
                     f"<code>unpark -C {e(str(p['path'].resolve()))}"
                     "</code></div>")
        parts.append("</section>")
    parts.append("</div></div>")  # .cards, .cols
    parts.append(f"<footer>generated by <b>unpark</b> v{__version__} · "
                 f"{e(today)}</footer>")
    parts.append("</div>")
    if token:
        parts.append(_BEAT_JS.replace("%TOKEN%", token))
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>portfolio — unpark</title>\n<style>{_HTML_CSS}</style>\n"
        "</head>\n<body>\n" + "\n".join(parts) + "\n</body>\n</html>\n"
    )


def _serve_page_once(page: str, timeout: float = 120.0, on_bound=None) -> bool:
    """Serve a static page once on an ephemeral port, then exit."""
    import http.server

    body = page.encode("utf-8")
    done = {"served": False}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not _safe_host_header(self.headers.get("Host", "")):
                return self.send_error(421, "invalid Host header")
            if self.path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                done["served"] = True
            else:
                self.send_error(404)

        def log_message(self, *args):
            pass

    class Server(http.server.HTTPServer):
        def server_bind(self):
            """Bind without reverse-DNS lookup of the loopback address."""
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    with Server(("127.0.0.1", 0), Handler) as srv:
        srv.timeout = 0.25
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        if on_bound:
            on_bound(url)
        deadline = time.monotonic() + timeout
        while not done["served"] and time.monotonic() < deadline:
            srv.handle_request()
    return done["served"]


# ------------------------------------------------------- upkeep skill

_UPKEEP_TEXT = """\
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
"""


def _skill_version() -> str:
    """Content hash of the canonical instructions: installed copies
    carry it, so staleness is detectable when the skill evolves."""
    return hashlib.sha1(_UPKEEP_TEXT.encode()).hexdigest()[:8]


def _version_line() -> str:
    return f"<!-- unpark-skill-version: {_skill_version()} -->"


def skill_copy_current(path) -> "bool | None":
    """True/False for an existing copy, None when absent."""
    p = Path(path)
    if not p.is_file():
        return None
    return _skill_version() in p.read_text()


def agents_block_state(path) -> "bool | None":
    """Like skill_copy_current, but only for OUR managed block — a
    hand-written AGENTS.md without it is None, not outdated."""
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    if "unpark:upkeep:start" not in text:
        return None
    return _skill_version() in text


_GLOBAL_SKILL_TARGETS = ("claude", "codex", "copilot")


def _agents_block() -> str:
    marker_start = "<!-- unpark:upkeep:start (managed by `unpark skill --install`) -->"
    marker_end = "<!-- unpark:upkeep:end -->"
    return (f"{marker_start}\n{_version_line()}\n\n"
            f"{_UPKEEP_TEXT}\n{marker_end}\n")


def _upsert_agents_block(path) -> None:
    """Add or replace only unpark's block in an AGENTS.md file."""
    path = Path(path)
    block = _agents_block()
    marker_start = "<!-- unpark:upkeep:start (managed by `unpark skill --install`) -->"
    marker_end = "<!-- unpark:upkeep:end -->"
    if path.exists():
        text = path.read_text()
        if marker_start in text and marker_end in text:
            head, _, rest = text.partition(marker_start)
            _, _, tail = rest.partition(marker_end)
            text = head + block.rstrip("\n") + tail
        else:
            text = text.rstrip("\n") + "\n\n" + block
    else:
        text = block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n")


def _claude_skill_text(global_: bool) -> str:
    scope = "any piece of work in a repo that has a WELCOME.md (or docs/WELCOME.md)" if global_ else "any piece of work in this repo"
    return (
        "---\n"
        "name: unpark-upkeep\n"
        f"description: Use when finishing {scope} — update WELCOME.md "
        "(State of things, Goals, Recipes), bump the updated: date, "
        "and run `unpark check` so the re-entry briefing stays fresh. "
        "ALSO use when the user asks to set up unpark / initialize a "
        "WELCOME.md briefing for a project — the skill contains the "
        "first-time setup recipe.\n"
        "---\n\n" + _version_line() + "\n\n" + _UPKEEP_TEXT)


def cmd_skill(root, install: bool, global_: bool = False,
              targets=None) -> int:
    if not install and not global_:
        print(_UPKEEP_TEXT)
        return 0

    if global_:
        selected = list(targets or ["claude"])
        if "all" in selected:
            selected = list(_GLOBAL_SKILL_TARGETS)
        selected = list(dict.fromkeys(selected))
        for target in selected:
            if target == "claude":
                skill = (_home() / ".claude" / "skills" /
                         "unpark-upkeep" / "SKILL.md")
                skill.parent.mkdir(parents=True, exist_ok=True)
                skill.write_text(_claude_skill_text(True))
            elif target == "codex":
                skill = _home() / ".codex" / "AGENTS.md"
                _upsert_agents_block(skill)
            elif target == "copilot":
                skill = (_home() / ".copilot" / "instructions" /
                         "unpark.instructions.md")
                skill.parent.mkdir(parents=True, exist_ok=True)
                skill.write_text("---\napplyTo: \"**\"\n---\n\n" +
                                 _version_line() + "\n\n" + _UPKEEP_TEXT)
            else:  # argparse protects this; keep cmd_skill safe for API use.
                raise ValueError(f"unknown global skill target: {target}")
            print(f"installed globally for {target}: {skill}")
        print("applies only when the project has a WELCOME.md; use "
              "`unpark skill --install` in shared repos so other tools "
              "also see AGENTS.md")
        return 0

    # AGENTS.md — the cross-tool convention (Codex, Cursor, aider, …)
    agents = Path(root) / "AGENTS.md"
    _upsert_agents_block(agents)

    # Claude Code skill — same content, native format
    skill_dir = Path(root) / ".claude" / "skills" / "unpark-upkeep"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_claude_skill_text(False))

    print(f"installed: {agents}")
    print(f"installed: {skill_dir / 'SKILL.md'}")
    print("note: tools that read neither AGENTS.md nor .claude/skills "
          "can be pointed at `unpark skill` output")
    return 0


# -------------------------------------------------- shell integrations

_SHELL_TARGETS = ("fish",)

_FISH_MARKER_START = "# >>> unpark:cd-hook:start (managed by `unpark shell fish`) >>>"
_FISH_MARKER_END = "# <<< unpark:cd-hook:end <<<"

# Runs on every directory change in an interactive fish shell. fish has
# no chpwd hook, so the block wraps fish_prompt once — the same trick
# `direnv fish` uses: copy the existing prompt, call ours first, then the
# original. unpark runs through `uvx` (uv keeps the package in its cache,
# so nothing has to be installed ahead of time); the heavy lifting
# (project lookup, briefing) stays in Python via `unpark cd-hook`, so
# the WELCOME.md location rules live in one place. A failing command is
# reported — with the file it came from — once per shell session.
_FISH_HOOK_BODY = """\
function __unpark_hook_error
    set -q __unpark_hook_error_shown
    and return
    set -g __unpark_hook_error_shown 1
    printf "unpark cd-hook failed (file: %s):\n" (status filename) >&2
    if test (count $argv) -gt 0
        printf "%s\n" $argv[1] >&2
    end
end

function __unpark_dir_change_hook
    set -q __unpark_last_dir
    or set -g __unpark_last_dir (pwd)
    set -l now (pwd)
    if test "$__unpark_last_dir" = "$now"
        return
    end
    set -l prev $__unpark_last_dir
    set -g __unpark_last_dir "$now"
    if not command -q uvx
        __unpark_hook_error "uvx not found — install uv: https://docs.astral.sh/uv/"
        return
    end
    set -l output (uvx unpark cd-hook "$now" --from "$prev" 2>&1)
    if test $status -ne 0
        __unpark_hook_error "$output"
        return
    end
    if test -n "$output"
        printf "%s\n" "$output"
    end
end

set -l __unpark_wrapped 0
if functions -q fish_prompt
    if string match -q "*__unpark_orig_fish_prompt*" (functions fish_prompt)
        set __unpark_wrapped 1
    end
end
if test $__unpark_wrapped -eq 0
    if functions -q fish_prompt
        functions -c fish_prompt __unpark_orig_fish_prompt
    end
    function fish_prompt
        __unpark_dir_change_hook
        if functions -q __unpark_orig_fish_prompt
            __unpark_orig_fish_prompt
        end
    end
end
"""


def _fish_version() -> str:
    """Content hash of the hook: installed copies carry it, so a newer
    `unpark shell fish --install` upgrades the block in place."""
    return hashlib.sha1(_FISH_HOOK_BODY.encode()).hexdigest()[:8]


def _fish_block() -> str:
    return (f"{_FISH_MARKER_START}\n"
            f"# unpark-shell-version: {_fish_version()}\n"
            f"# Runs the unpark briefing when you change into a project\n"
            f"# directory, and offers `unpark init` in a repo without one.\n"
            f"# Remove with: unpark shell fish --uninstall\n"
            f"\n{_FISH_HOOK_BODY}\n"
            f"{_FISH_MARKER_END}\n")


def shell_config_path(target: str) -> Path:
    """Where target's shell hook lives: a conf.d file the shell sources
    on every interactive start, so user config files stay untouched."""
    if target != "fish":
        raise ValueError(f"unknown shell target: {target}")
    return _config_base() / "fish" / "conf.d" / "unpark.fish"


# Starter content for a missing ~/.config/unparkrc, created by
# `unpark shell fish --install`: a fully commented dummy that shows
# the entry format and self-documents its source.
_UNPARKRC_DUMMY = """\
# unparkrc — user config for unpark's shell hooks
#
# Created by `unpark shell fish --install` as a starting point; edit
# freely, unpark never rewrites an existing file. One entry per line;
# lines starting with '#' are comments. Entries name project roots —
# local directories, or repository URLs that contain your projects.
#
# Source: https://github.com/Tauris/unpark
"""


def unparkrc_path() -> Path:
    """User config for the shell hooks (roots the cd-hook cares about)."""
    return _config_base() / "unparkrc"


def _upsert_shell_block(path, marker_start: str, marker_end: str, block: str,
                         remove: bool = False) -> bool:
    """Add or replace only unpark's marked block in a shell config file.

    Returns True when the file changed (or was removed). On removal a
    file left without meaningful content is deleted (conf.d hygiene).
    """
    path = Path(path)
    if remove:
        if not path.exists():
            return False
        text = path.read_text()
        if marker_start not in text or marker_end not in text:
            return False
        head, _, rest = text.partition(marker_start)
        _, _, tail = rest.partition(marker_end)
        kept = re.sub(r"\n{3,}", "\n\n", head + tail).strip("\n")
        if kept.strip():
            path.write_text(kept + "\n")
        else:
            path.unlink()
        return True
    if path.exists():
        text = path.read_text()
        if marker_start in text and marker_end in text:
            head, _, rest = text.partition(marker_start)
            _, _, tail = rest.partition(marker_end)
            text = head + block.rstrip("\n") + tail
        else:
            text = text.rstrip("\n") + "\n\n" + block.rstrip("\n")
    else:
        text = block.rstrip("\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n")
    return True


def cmd_shell(target: str, install: bool = False,
              uninstall: bool = False) -> int:
    """Print, install or remove the shell directory-change hook."""
    if target not in _SHELL_TARGETS:
        print(f"unpark: unknown shell target: {target} "
              f"(known: {', '.join(_SHELL_TARGETS)})", file=sys.stderr)
        return 1
    if install and uninstall:
        print("unpark: --install and --uninstall are mutually exclusive",
              file=sys.stderr)
        return 1
    if not install and not uninstall:
        print(_fish_block())
        return 0
    path = shell_config_path(target)
    if uninstall:
        if _upsert_shell_block(path, _FISH_MARKER_START, _FISH_MARKER_END,
                               "", remove=True):
            print(f"removed directory-change hook: {path}")
        else:
            print("no unpark shell hook installed")
        return 0
    _upsert_shell_block(path, _FISH_MARKER_START, _FISH_MARKER_END,
                        _fish_block())
    print(f"installed fish directory-change hook: {path}")
    rc_file = unparkrc_path()
    if not rc_file.exists():
        rc_file.parent.mkdir(parents=True, exist_ok=True)
        rc_file.write_text(_UNPARKRC_DUMMY)
        print(f"created {rc_file} — dummy config; edit its entry list "
              "to your project roots")
    print("new fish shells now run the briefing via `uvx unpark` "
          "(cached by uv) when you change into a project, and offer "
          "`unpark init` in repos without one; a failing command is "
          "reported with the file it came from, once per shell "
          "session")
    return 0


def _inside_git_repo(directory) -> "Path | None":
    """Git toplevel containing directory, by stat-walk, or None.

    No subprocess — this runs on the hot path of every directory
    change. A `.git` *file* marks a worktree or submodule; both count
    as repos.
    """
    try:
        p = Path(directory).resolve()
    except OSError:
        return None
    for candidate in [p, *p.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def cmd_cd_hook(directory, from_dir=None) -> int:
    """What the installed shell hook runs on a directory change.

    Entering a project prints its terminal briefing; entering a git
    repo without one offers `unpark init` (with `UNPARK_CD_AUTO_INIT=1`
    in the environment, the template is created at the repo root
    instead). Moving within the same project or repo stays silent.
    Never fails — a prompt must survive anything.
    """
    to_project = find_project(directory)
    if to_project is not None:
        from_project = (find_project(from_dir) if from_dir else None)
        if from_project is not None and from_project[0] == to_project[0]:
            return 0  # moving inside the project we already briefed
        root, welcome_file = to_project
        w = _load(welcome_file)
        if Path(directory).resolve() != root:
            print(f"project: {w.meta.get('name', root.name)} — {root}")
        display_text(render_text(w, _derived(root, w),
                                 _use_color(False)))
        return 0
    to_repo = _inside_git_repo(directory)
    if to_repo is None:
        return 0
    from_repo = (_inside_git_repo(from_dir) if from_dir else None)
    if from_repo is not None and from_repo == to_repo:
        return 0  # moving inside the same repo: one hint is enough
    if _env("CD_AUTO_INIT") == "1":
        return cmd_init(to_repo)
    print("no unpark briefing in this repo — `unpark init` creates one "
          "(set UNPARK_CD_AUTO_INIT=1 to do that automatically)",
          file=sys.stderr)
    return 0


# ---------------------------------------------------------- the manual

_DOCS = [
    ("What is unpark", """
`unpark` is a re-entry briefing for projects you parked
weeks ago: what it is, status vs goals, how to build, run, demo and
distribute. It reads one markdown file plus live git state. LLM agents
keep the file fresh (see *Keeping it fresh*); the tool itself never
needs one.

`wb` is an optional shorthand for “welcome back”; it runs the same command.
"""),
    ("Quick start", """
- **First time, with an LLM** (recommended): install the skill once
  (`unpark skill --install --global --target claude`), then tell your agent
  *"Set up unpark for this project"* — it creates the briefing and
  fills every section from the repo, with working recipes.
- `unpark init` — create a briefing skeleton by hand instead
- `unpark` — read the briefing (terminal)
- `unpark html` — the briefing as an interactive browser dashboard
- `unpark start demo` — launch the demo as a managed process
- `unpark skill --install` — teach LLM agents to keep it fresh
"""),
    ("Commands", """
- `unpark` — briefing for the current project; from a directory above
  projects it renders the portfolio overview instead
- `unpark run <recipe>` — run a recipe's steps in the foreground
- `unpark start <recipe>` — launch as a managed background process
  (health-checked: an immediate death is reported with the log tail)
- `unpark stop [recipe]` — stop one or all managed processes
- `unpark ps` / `unpark logs <recipe> [-n N]` — inspect them
- `unpark html [-o FILE] [--open] [--no-open]` — browser dashboard,
  served ephemerally; `-o` writes a static file instead
- `unpark init` — create a WELCOME.md template
- `unpark check` — validate the briefing (CI-friendly; fails on
  errors and unresolved merge conflict markers)
- `unpark register` / `unpark unregister` — add/remove this project
  in your portfolio (stored in a user config file)
- `unpark projects` — page registered projects, or use `--pick` for a
  numbered chooser; `unpark project NAME` opens one terminal briefing
- `unpark code` — open the project in VS Code: an explicit
  `workspace:` front-matter key wins, else a detected
  `*.code-workspace` in the root or `.vscode/`, else the folder
- `unpark workspace [init | set PATH]` — show how VS Code opens this
  project; `init` migrates folder→workspace (creates the file in the
  repo root, add more folders in VS Code); `set` records an external
  workspace file in the front-matter. Registration and state stay
  anchored to the repo folder — a migration never touches them.
  Caveat: VS Code migrates chat/UI state (Copilot sessions) only when
  the conversion happens *inside* VS Code (File → Save Workspace As…
  into the repo root); use that route when history matters — unpark
  detects the resulting file either way
- `unpark skill [--install] [--global]` — LLM upkeep instructions
- `unpark shell fish [--install | --uninstall]` — the fish
  directory-change hook (see *Directory change hook (fish))*
- `unpark manual` — this manual · `unpark help` — terminal help
- Global flags: `-C DIR` (project directory), `--file PATH`,
  `--no-color`, `--no-pager`, `--version`. Terminal briefings page through
  `$PAGER` when they exceed the terminal height; `NO_PAGER` disables this.
"""),
    ("The WELCOME.md format", """
Front-matter between `---` lines, then free markdown sections:

```
---
name: photo-globe
tagline: One line about the project
status: active | parked | done | abandoned
updated: 2026-07-11
---

## What is this
## Goals            (checkbox list — progress is counted)
## State of things  (the "you were here" bookmark)
## Recipes          (see below)
## Distribute
```

Unknown front-matter keys and extra sections are preserved — the
format is forgiving by design.

Recipes are `###` headings under `## Recipes`: prose description,
then optional `- key: value` bullets, then fenced code blocks as
sequential steps:

- `background: true` — a long-running process for `unpark start`
- `dir: web` — run steps from this repo-relative directory
- `url: http://localhost:5183` — shown and linked when running
- `needs: build` — run that recipe first
- `env: DEBUG=1 PORT=8080` — extra environment for the steps
- `dotenv: .env` — load a dotenv file (repo-relative) for the steps

`unpark run <name> -- ARGS` appends ARGS (shell-quoted) to the last
step — handy for delegations like `just test` or `pytest`.
"""),
    ("Task runners — friends, not rivals", """
Projects already have task catalogs: `just` (justfile), `task`
(Taskfile.yml), `mask`, `mise`, `make`, `npm run` scripts. unpark does
**not** replace them — its goal is different: the re-entry briefing,
staleness detection, portfolio, dashboard, and LLM upkeep. Task
runners own the *how to do everything*; unpark owns the *what is this
and what do I do first*. They are complementary.

The doctrine that keeps them friends:

- Recipes are a curated handful of **re-entry verbs** (`demo`, `dev`,
  `test`, `publish`) — at most ~5, answering “what does a returning
  human do first?”, never the full catalog.
- Where a runner exists, recipe steps **delegate** in one line
  (`just demo`, `npm run dev`) instead of duplicating command bodies —
  the runner owns the details, so nothing drifts.
- unpark detects runners and shows them in the briefing and the
  dashboard (with a task count where cheap, e.g. npm scripts), so the
  full catalog is always one hint away.

If you already keep tasks in markdown for `xc` or `mask`, unpark's
recipe syntax will feel familiar — the convergence is deliberate.
"""),
    ("Where the briefing lives", """
Walking up from where you stand, the first hit wins per directory:
`WELCOME.md`, then `docs/WELCOME.md`, then `.unpark/WELCOME.md` —
useful when a repo's top level is off-limits. The project root is the
directory the file was found from, so recipe `dir:` paths stay
repo-relative. Explicit override: `--file PATH` or the `UNPARK_FILE`
environment variable.
"""),
    ("Managed processes", """
Two ways to execute a recipe. `unpark run` is the simple one: steps
run in your terminal, in the foreground, and when they finish (or you
Ctrl+C) it is over — right for builds, indexers, one-shot scripts.

`unpark start` is for things that should *keep running*, like a demo
server. What "managed" means concretely:

- **detached** — the process is started in its own session, so it
  survives the unpark command, the dashboard, and even closing the
  terminal. You start a demo, you walk away, it stays up.
- **recorded** — the pid and a log file are written to a per-project
  state directory in your home (never inside the repo), so any later
  `unpark` invocation — from any terminal, or the dashboard —
  knows what is running. `unpark ps` lists it, `unpark logs demo`
  shows its output.
- **stoppable** — `unpark stop [recipe]` terminates the whole
  process group (the shell and everything it spawned), not just the
  top process.
- **health-checked** — a start that dies within a second (port in
  use, missing binary) is reported as a failure with the last log
  lines, instead of a phantom success.

The dashboard's buttons drive exactly these mechanics. Note the
asymmetry with the dashboard itself: the dashboard dies with its
heartbeat when you close the tab, but processes it started do not —
they are managed, not children of the page.
"""),
    ("The HTML dashboard", """
`unpark html` serves the briefing on an ephemeral localhost port —
nothing is written to disk. Buttons start and stop recipes; a Running
panel updates every 2 seconds. That poll doubles as a heartbeat: close
the tab and the server exits a few seconds later (managed processes
keep running). The API requires a per-session token in a custom
header, so foreign webpages cannot POST to the port. Links open in new
tabs — there is no server to go back to.

**What happens when a button starts a recipe.** Two variants:

- **click** — managed start: the process is detached, its output goes
  to a log file in the per-project state directory
  (`~/.local/state/unpark/<project>-<hash>/<recipe>.log`, override
  with `UNPARK_STATE_DIR`), stdin is closed. Read it via the **logs**
  link on the Running panel or `unpark logs <name>`. It does NOT
  appear in the terminal where `unpark html`/`unpark manual` runs, and it
  survives that terminal, the server, and the tab.
- **shift-click** — attached start: output streams into the terminal
  where the dashboard server runs, and the process can read keyboard
  input typed there (interactive scripts work). The trade-offs: no
  log file, and it shares the terminal's fate — Ctrl+C in that
  terminal stops it (and the server). Best for watching a script or
  answering a one-off prompt; use plain click for demo servers.

Neither variant blocks the server — every button press is handled in
its own thread, so the page stays live while recipes start and stop.
"""),
    ("Portfolio", """
Run `unpark` in a directory that is not a project but contains
projects (say `~/src`): you get one line per project — status, last
commit age, goal progress, tagline, running demos, staleness and
uncommitted markers — alphabetical. `unpark html` there serves
the same as a card page.

Registered projects (`unpark register`, or the button on a project's
dashboard) appear in the portfolio from *anywhere*, not just their
parent directory. The list lives in a user config file
(`~/.config/unpark/projects.json`); vanished projects are skipped
silently.

On the served `/projects` page every card has an **open dashboard**
button: it launches that project's own `unpark html` server (a
detached process with its own heartbeat and lifetime) and opens it in
a new tab — the portfolio doubles as a launcher.
"""),
    ("Keeping it fresh (LLM upkeep)", """
`unpark skill` prints a checklist for any LLM agent: update *State of
things*, tick *Goals*, fix *Recipes*, bump `updated:`, run `unpark
check` — plus reconciliation rules for merge conflicts (later date
wins, goals union, prose merged newest-first).

Install it **locally** (`unpark skill --install`): writes an
idempotent block into the repo's `AGENTS.md` (read by Codex, Cursor
and most agents) and `.claude/skills/unpark-upkeep/SKILL.md` (Claude
native). Travels with the repo — teammates get it.

Install it **globally** with an explicit target, for example
`unpark skill --install --global --target codex`. Choose `claude`,
`codex`, or `copilot`; repeat `--target` to select more, or use
`--target all` for every file-based integration. Cursor's global User
Rules are set in Cursor itself, so unpark does not modify them.
Every global option has zero repo footprint.

With the global skill installed, a local install adds **nothing for
you** — your agent is already covered in every repo. Install locally
only so *others* get it: teammates without the global skill, and
tools that read AGENTS.md from the repo. Both can coexist (identical
content; personal wins the tie).

Independently of any LLM, `unpark` warns "may be stale" whenever
commits are newer than `updated:` — drift is always visible.

**When does it fire?** The skill is instructions, not a daemon —
it acts through the agent reading it:

- *Claude Code*: skill descriptions load at session start (local
  `.claude/skills/` and global `~/.claude/skills/`); the model invokes
  the skill when the situation matches its description — here, "when
  finishing any piece of work in a repo with a WELCOME.md". In
  practice: at the end of a change, before committing or summarizing.
- *AGENTS.md readers* (Codex, Cursor, …): the block is part of the
  project instructions loaded into every session, so the checklist is
  simply always in force.
- *The tripwire*: any agent (or you) running `unpark` sees the
  "may be stale" warning, and the instructions say explicitly: seeing
  that warning means run the checklist now. That is the safety net
  when an agent forgot mid-session.

It does NOT fire on a timer or a git hook — nothing happens while no
agent is working. If a project drifts while you edit by hand, the
staleness warning is what tells you.
"""),
    ("Directory change hook (fish)", """
`unpark shell fish --install` adds a managed block to
`~/.config/fish/conf.d/unpark.fish` (Windows:
`%APPDATA%\\fish\\conf.d\\unpark.fish`). From then on, changing into a
project directory runs the terminal briefing itself — direnv-style
re-entry, no muscle memory needed. New shells only: an already running
shell keeps the old prompt until you start a new one.

What happens on a directory change:

- *into a project* (a WELCOME.md found while walking up): the
  terminal briefing prints. Moving *within* the same project stays
  silent; leaving and coming back briefs again.
- *into a git repo without a briefing*: one line offering
  `unpark init`. With `UNPARK_CD_AUTO_INIT=1` in the environment the
  template is created at the repo root automatically instead — opt-in
  on purpose, so a stray `cd` into a fresh clone never writes files.
- *anywhere else*: nothing happens, and the hook never changes the
  exit status of your prompt.

Mechanics: fish has no directory-change hook, so the block wraps
`fish_prompt` exactly once (the same trick `direnv fish` uses): your
existing prompt is copied and called as before, with the hook in
front. The hook runs `uvx unpark cd-hook DIR --from PREV` — uv keeps
the unpark package in its cache, so nothing has to be installed ahead
of time (the first run may take a few seconds until the cache is
warm). The project lookup itself stays in Python, so the WELCOME.md
location rules live in one place. If the command fails (uv missing,
no network, a cached unpark too old to know `cd-hook`), the hook
prints the conf.d file it came from and the error message — once per
shell session; `uv tool upgrade unpark` refreshes a stale cache.
`unpark shell fish` without flags prints the generated script;
`--uninstall` removes the block and keeps anything you added to the
file by hand. The install also creates a dummy `~/.config/unparkrc`
(a fully commented starter that shows the entry format and
self-documents its source, `https://github.com/Tauris/unpark`)
unless one already exists — an existing file is never touched, and
uninstall never removes it.

bash and zsh have native `chpwd`/`PROMPT_COMMAND` hooks and are next.
Running `unpark` on *git branch* changes (also requested in issue #1)
would be a git `post-checkout` hook rather than a shell hook — a
follow-up.
"""),
    ("Environment variables", """
- `UNPARK_FILE` — explicit briefing path (like `--file`)
- `UNPARK_STATE_DIR` — where pidfiles/logs live (default:
  `~/.local/state/unpark`, per-project subdirectories)
- `UNPARK_CD_AUTO_INIT` — set to `1`: the fish directory-change hook
  creates a WELCOME.md template automatically in a git repo without
  one, instead of only offering `unpark init`
- `NO_COLOR` — disable ANSI colors
- `BROWSER` — force a specific browser (otherwise WSL hands URLs to
  Windows automatically)
"""),
]

# per-section unicode icon + short sticky-nav label (defaults: § / title)
_DOCS_META = {
    "What is unpark": ("◈", "intro"),
    "Quick start": ("»", "quick start"),
    "Commands": ("❯", "commands"),
    "The WELCOME.md format": ("¶", "format"),
    "Where the briefing lives": ("⌂", "location"),
    "Managed processes": ("⟳", "processes"),
    "The HTML dashboard": ("▦", "dashboard"),
    "Portfolio": ("▤", "portfolio"),
    "Keeping it fresh (LLM upkeep)": ("✎", "llm upkeep"),
    "Directory change hook (fish)": ("↪", "cd hook"),
    "Environment variables": ("$", "env vars"),
}


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


# The manual's own stylesheet. Shares the palette variables with
# _HTML_CSS (dashboard/portfolio) but is deliberately separate: those
# pages keep their look, the manual gets hero/toc/cards/def-lists.
_DOCS_CSS = """
:root { --bg:#f6f7fb; --card:#fff; --ink:#1d2333; --muted:#6b7280;
  --line:#e5e8f0; --accent:#3b5bdb; --ok:#2f9e44; --pill:#e7ebff;
  --pill-ink:#3b5bdb; --code:#f1f3f9; }
nav.pagenav { float:right; font-size:13px; margin-top:46px }
nav.pagenav a { color:var(--accent); text-decoration:none;
  padding:2px 8px; border-radius:6px }
nav.pagenav a:hover { background:var(--pill) }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0e1220; --card:#171c2e; --ink:#dbe2f4; --muted:#8b94ad;
    --line:#262d45; --accent:#7aa2ff; --ok:#69db7c; --pill:#24304f;
    --pill-ink:#9db4ff; --code:#10162a; } }
:root { color-scheme: light dark }
* { box-sizing:border-box }
html { scroll-behavior:smooth }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif }
.wrap { max-width:880px; margin:0 auto; padding:0 20px 64px }
code, pre { font:13px/1.5 ui-monospace,Consolas,monospace;
  background:var(--code); border-radius:6px }
code { padding:1px 6px }
pre { padding:12px 16px; overflow-x:auto; margin:10px 0 0;
  border-left:3px solid var(--line) }
a { color:var(--accent) }
p { margin:8px 0 }
strong { font-weight:650 }
button { font:inherit; font-size:12.5px; font-weight:600;
  padding:3px 12px; border-radius:6px; border:1px solid var(--line);
  background:var(--pill); color:var(--pill-ink); cursor:pointer;
  transition:border-color .15s }
button:hover:not(:disabled) { border-color:var(--accent) }
button:disabled { opacity:.45; cursor:default }
.hero { padding:44px 0 18px }
.hero .prompt { color:var(--muted); letter-spacing:.08em;
  font:12.5px ui-monospace,Consolas,monospace }
.hero h1 { margin:4px 0 8px; letter-spacing:-.02em;
  font:700 34px/1.1 ui-monospace,Consolas,monospace }
.hero h1 .pill { vertical-align:9px; margin-left:2px }
.hero .tag { margin:0; color:var(--muted); font-size:15.5px; max-width:62ch }
.pill { display:inline-block; padding:2px 10px; border-radius:999px;
  background:var(--pill); color:var(--pill-ink); font-size:12.5px;
  font-weight:600 }
nav.toc { position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap;
  gap:2px; padding:10px 0 8px; background:var(--bg);
  border-bottom:1px solid var(--line) }
nav.toc a { color:var(--muted); text-decoration:none; padding:2px 9px;
  border-radius:999px; font:12.5px ui-monospace,Consolas,monospace;
  transition:color .15s,background .15s }
nav.toc a:hover { color:var(--pill-ink); background:var(--pill) }
section.card { background:var(--card); border:1px solid var(--line);
  border-left:3px solid var(--accent); border-radius:12px;
  padding:20px 24px 22px; margin-top:22px; scroll-margin-top:52px }
h2 { margin:0 0 10px; font-size:13px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--muted) }
h2 .ico { color:var(--accent); margin-right:9px }
h2 a { color:inherit; text-decoration:none }
h2 a:hover { color:var(--accent) }
ul { margin:8px 0; padding-left:22px }
li { margin:3px 0 }
ul.def { display:grid; grid-template-columns:fit-content(300px) 1fr;
  gap:0 18px; list-style:none; margin:12px 0 0; padding:0 }
ul.def li { display:contents }
ul.def .k, ul.def .v { padding:8px 0; border-top:1px solid var(--line) }
ul.def li:first-child .k, ul.def li:first-child .v { border-top:none }
ul.def .k code { background:var(--pill); color:var(--pill-ink);
  font-weight:600; padding:2px 8px }
ul.def li.plain { display:block; grid-column:1 / -1; padding:8px 0;
  border-top:1px solid var(--line); color:var(--muted); font-size:13.5px }
@media (max-width:620px) {
  ul.def { grid-template-columns:1fr }
  ul.def .v { border-top:none; padding-top:0 } }
.here-name { font:700 21px/1.3 ui-monospace,Consolas,monospace }
.here-name.dim { color:var(--muted); font-size:17px }
.here-paths { margin-top:6px; color:var(--muted); font-size:13.5px }
.skill { margin-top:16px; padding-top:12px;
  border-top:1px dashed var(--line) }
.skill h3 { margin:0 0 4px; font-size:11.5px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--muted) }
.skill-row { display:flex; flex-wrap:wrap; align-items:center;
  gap:8px 10px; padding:8px 0 2px }
.skill-row .scope { font-weight:600; font-size:14px; min-width:9em }
.skill-row .dest { flex-basis:100%; color:var(--muted); font-size:12.5px }
.badge { display:inline-block; padding:1px 9px; border-radius:999px;
  border:1px solid var(--line); background:var(--code); color:var(--muted);
  font-size:11.5px; font-weight:700; letter-spacing:.03em }
.badge.on { color:var(--ok); border-color:var(--ok); background:transparent }
.note { color:var(--muted); font-size:12.5px; min-height:1.2em }
footer { margin-top:30px; color:var(--muted); font-size:12.5px;
  text-align:center }
"""

_DOCS_JS = """
<script>
const TOKEN = "%TOKEN%";
const HDRS = {"X-Unpark-Token": TOKEN};
async function api(path, method) {
  try {
    const r = await fetch(path, {method: method || "GET", headers: HDRS});
    return r.ok ? await r.json() : null;
  } catch (e) { return null; }
}
function mark(id, on, yes, no) {
  const el = document.getElementById(id);
  if (el) { el.textContent = on ? yes : no; el.classList.toggle("on", on); }
}
function esc(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
async function doStop(n) { await api("/api/stop/" + n, "POST"); tick(); }
function renderProcs(s) {
  const el = document.getElementById("running");
  if (el && s.running) {
    el.innerHTML = s.running.length
      ? s.running.map(p => "<li><b>" + esc(p.recipe) + "</b> pid " + p.pid +
          (p.url ? " — <a href=\\"" + esc(p.url) +
                   "\\" target=\\"_blank\\" rel=\\"noopener\\">" +
                   esc(p.url) + "</a>" : "") +
          (p.log ? " — <a href=\\"/logs/" + esc(p.recipe) +
                   "\\" target=\\"_blank\\" rel=\\"noopener\\">logs</a>" : "") +
          " <button onclick=\\"doStop('" + esc(p.recipe) +
          "')\\">stop</button></li>").join("")
      : "<li>nothing running</li>";
  }
  const lf = document.getElementById("logfiles");
  if (lf) lf.innerHTML = (s.logs && s.logs.length)
    ? "all logs: " + s.logs.map(n =>
        '<a href="/logs/' + esc(n) + '" target="_blank" rel="noopener">' +
        esc(n) + "</a>").join(" · ")
    : "";
}
async function tick() {
  const s = await api("/api/status");
  if (!s) return;
  mark("st-global", s.global_installed, "installed", "not installed");
  const note = document.getElementById("st-local-note");
  if (note) note.textContent = (s.global_installed && !s.local_installed)
    ? " already covered by your global install — install locally only so " +
      "teammates and non-Claude tools get it from the repo"
    : "";
  mark("st-local", s.local_installed, "installed", "not installed");
  const lb = document.querySelector('button[data-scope="local"]');
  if (lb && s.has_project) lb.disabled = s.local_installed;
  const gb = document.querySelector('button[data-scope="global"]');
  if (gb) gb.disabled = s.global_installed;
  renderProcs(s);
}
async function install(scope) {
  const r = await api("/api/skill/" + scope, "POST");
  const note = document.getElementById("install-note");
  if (note) note.textContent = r && r.ok
    ? (r.wrote || []).join("  ·  ")
    : "failed — see terminal";
  tick();
}
async function initHere(loc) {
  const r = await api("/api/init?loc=" + loc, "POST");
  const note = document.getElementById("init-note");
  if (r && r.ok) { location.reload(); return; }
  if (note) note.textContent = (r && r.error) || "failed — see terminal";
}
setInterval(tick, 2000);   // heartbeat: server exits when these stop
tick();
</script>
"""


def _docs_context(start) -> dict:
    found = find_project(start)
    name = None
    if found:
        try:
            name = parse_welcome(found[1].read_text()).meta.get("name")
        except OSError:
            pass
    global_skill = (_home() / ".claude" / "skills" / "unpark-upkeep"
                    / "SKILL.md")
    local_skill = (found[0] / ".claude" / "skills" / "unpark-upkeep"
                   / "SKILL.md") if found else None
    return {
        "cwd": Path(start).resolve(),
        "root": found[0] if found else None,
        "file": found[1] if found else None,
        "name": name,
        "global_installed": global_skill.exists(),
        "local_installed": bool(local_skill and local_skill.exists()),
        "is_repo_root": (Path(start) / ".git").exists(),
        "is_skeleton": _safe_skeleton(found),
    }


def _safe_skeleton(found) -> bool:
    if not found:
        return False
    try:
        return is_skeleton_text(found[1].read_text())
    except OSError:
        return False


def render_docs_html(ctx: dict, token: "str | None" = None) -> str:
    e = _html.escape
    parts = ['<div class="wrap">']

    # -- hero ----------------------------------------------------------
    hero_nav = ('<nav class="pagenav"><a href="/projects">your projects'
                "</a></nav>" if token else "")
    parts.append(
        '<header class="hero">'
        + hero_nav +
        '<div class="prompt">$ unpark manual</div>'
        f'<h1>unpark <span class="pill">v{__version__}</span></h1>'
        '<p class="tag">re-entry briefings for projects you parked — '
        '<code>unpark</code> (or <code>wb</code>, short for “welcome back”) '
        'in the terminal, a live '
        'dashboard in the browser, kept fresh by your LLM agents.</p>'
        "</header>")

    # -- sticky table of contents --------------------------------------
    toc = ['<nav class="toc" aria-label="contents">',
           '<a href="#you-are-here">◉ you are here</a>']
    for title, _body in _DOCS:
        _ico, short = _DOCS_META.get(title, ("§", title))
        toc.append(f'<a href="#{_slug(title)}">{e(short)}</a>')
    toc.append("</nav>")
    parts.append("".join(toc))

    # -- you are here (status card) -------------------------------------
    parts.append('<section class="card here" id="you-are-here">')
    parts.append('<h2><span class="ico">◉</span>You are here</h2>')
    if ctx["root"]:
        rel = ctx["file"].relative_to(ctx["root"]) if ctx["file"] else "?"
        parts.append(f'<div class="here-name">'
                     f"{e(ctx['name'] or ctx['root'].name)}</div>")
        parts.append(f'<div class="here-paths">project root '
                     f"<code>{e(str(ctx['root']))}</code> · briefing "
                     f"<code>{e(str(rel))}</code></div>")
        if token and ctx.get("is_skeleton"):
            covered = ctx["global_installed"] or ctx["local_installed"]
            parts.append(f'<p class="note">{_phrase_tip_html(covered)}</p>')
        if token:
            parts.append(
                '<div class="skill"><h3>Running</h3>'
                '<ul id="running" style="list-style:none;padding:0">'
                "<li>…</li></ul>"
                '<p class="note" id="logfiles"></p></div>')
    else:
        parts.append('<div class="here-name dim">no project here</div>')
        parts.append(f'<div class="here-paths">working directory '
                     f"<code>{e(str(ctx['cwd']))}</code> has no WELCOME.md "
                     'upward — see <a href="#where-the-briefing-lives">where '
                     "the briefing lives</a></div>")
        if token and ctx.get("is_repo_root"):
            parts.append(
                '<div class="skill"><h3>Create the briefing</h3>'
                "<p>this is a repo root — start right here:</p>"
                '<div class="skill-row">'
                '<button data-init="top" onclick="initHere(\'top\')">'
                "create WELCOME.md</button>"
                '<span class="dest">top level — renders on the repo '
                "front page</span></div>"
                '<div class="skill-row">'
                '<button data-init="docs" onclick="initHere(\'docs\')">'
                "create docs/WELCOME.md</button>"
                '<span class="dest">for repos whose top level is '
                "restricted</span></div>"
                '<p class="note" id="init-note">tip: afterwards, tell your '
                "LLM agent <b>“Set up unpark for this project”</b> "
                '<button data-copy="phrase" onclick="copyText(this, '
                "'Set up unpark for this project')\">copy</button> "
                "to fill it from the repo. "
                + ("Your agent already knows the recipe — the unpark "
                   "skill is installed globally on this machine."
                   if ctx["global_installed"] or ctx["local_installed"]
                   else "Note: the unpark skill is <b>not installed "
                        "yet</b> — use “install globally” above first, or "
                        "the agent won't know what the phrase means.")
                + "</p></div>")
    if token:
        root = ctx["root"]
        local_target = (f"<code>{e(str(root))}/AGENTS.md</code> + "
                        f"<code>{e(str(root))}/.claude/skills/"
                        "unpark-upkeep/</code>"
                        if root else "(requires a project)")
        gl = _home() / ".claude" / "skills" / "unpark-upkeep"
        disabled = "" if root else " disabled"
        b_local = " on" if ctx["local_installed"] else ""
        b_global = " on" if ctx["global_installed"] else ""
        t_local = "installed" if ctx["local_installed"] else "not installed"
        t_global = "installed" if ctx["global_installed"] else "not installed"
        parts.append(
            '<div class="skill"><h3>LLM upkeep skill</h3>'
            '<div class="skill-row"><span class="scope">this project</span>'
            f'<span class="badge{b_local}" id="st-local">{t_local}</span>'
            '<button data-scope="local" onclick="install(\'local\')"'
            f"{disabled}>install locally</button>"
            f'<span class="dest">→ {local_target}'
            '<i id="st-local-note"> '
            + ("already covered by your global install — install locally "
               "only so teammates and non-Claude tools get it from the repo"
               if ctx["global_installed"] and not ctx["local_installed"]
               else "")
            + "</i></span></div>"
            '<div class="skill-row"><span class="scope">this machine</span>'
            f'<span class="badge{b_global}" id="st-global">{t_global}</span>'
            '<button data-scope="global" onclick="install(\'global\')">'
            "install globally</button>"
            '<span class="dest">→ all projects, Claude by default · '
            f"<code>{e(str(gl))}/</code></span></div>"
            '<div class="skill-row"><span class="scope">any other agent</span>'
            '<span class="badge on">paste</span>'
            '<button data-setup="copy" onclick="copyInstr(this)">'
            "copy instructions</button>"
            '<span class="dest">→ clipboard, for agents that read '
            "neither AGENTS.md nor skills</span></div>"
            '<p class="note" id="install-note"></p></div>')
    parts.append("</section>")

    # -- doc sections ----------------------------------------------------
    for title, body in _DOCS:
        ico, _short = _DOCS_META.get(title, ("§", title))
        slug = _slug(title)
        parts.append(f'<section class="card" id="{slug}">')
        parts.append(f'<h2><span class="ico">{ico}</span>'
                     f'<a href="#{slug}">{e(title)}</a></h2>')
        parts.append(_md_html(body.strip(), defs=True))
        parts.append("</section>")

    parts.append(f"<footer><b>unpark</b> v{__version__} · "
                 "<code>unpark manual</code> "
                 "reopens this manual · served locally, nothing leaves "
                 "your machine</footer>")
    parts.append("</div>")
    if token:
        parts.append(_DOCS_JS.replace("%TOKEN%", token))
        parts.append(_copy_js())
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>unpark — manual</title>\n<style>{_DOCS_CSS}</style>\n"
        "</head>\n<body>\n" + "\n".join(parts) + "\n</body>\n</html>\n"
    )


def serve_docs(start, timeout: float = 120.0, grace: float = 8.0,
               on_bound=None) -> bool:
    """Serve the manual like the dashboard: heartbeat-bound, token-guarded."""
    token = secrets.token_urlsafe(16)
    lock = threading.Lock()
    state = {"opened": False, "last_seen": time.monotonic()}

    def seen(opened=False):
        with lock:
            state["last_seen"] = time.monotonic()
            if opened:
                state["opened"] = True

    class Handler(http.server.BaseHTTPRequestHandler):
        def _authed(self):
            supplied = (self.headers.get("X-Unpark-Token")
                        or self.headers.get("X-Welcome-Token"))
            return supplied == token

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not _safe_host_header(self.headers.get("Host", "")):
                return self.send_error(421, "invalid Host header")
            if self.path in ("/", "/index.html"):
                page = render_docs_html(_docs_context(start), token=token)
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                seen(opened=True)
            elif self.path == "/projects":
                regs = gather_registered()
                page = render_portfolio_html(
                    regs, start, token=token,
                    title=f"{len(regs)} registered project"
                          f"{'s' if len(regs) != 1 else ''}",
                    nav=[("/", "manual")])
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                seen(opened=True)
            elif self.path == "/api/status":
                if not self._authed():
                    return self._json({"error": "forbidden"}, 403)
                ctx = _docs_context(start)
                base = ctx["root"] or Path(start)
                self._json({"has_project": ctx["root"] is not None,
                            "global_installed": ctx["global_installed"],
                            "local_installed": ctx["local_installed"],
                            "running": (list_running(ctx["root"])
                                        if ctx["root"] else []),
                            "logs": (log_names(ctx["root"])
                                     if ctx["root"] else [])})
                seen()
            elif self.path.startswith("/logs/"):
                ctx = _docs_context(start)
                if not ctx["root"]:
                    return self.send_error(404)
                name = self.path[len("/logs/"):]
                logf = state_dir(ctx["root"]) / f"{name}.log"
                if "/" in name or not logf.is_file():
                    return self.send_error(404)
                tail = logf.read_text(errors="replace").splitlines()[-200:]
                body = (f"log: {logf}\n(last {len(tail)} lines — refresh "
                        "for more)\n\n" + "\n".join(tail) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                seen()
            else:
                self.send_error(404)

        def do_POST(self):
            if not _safe_host_header(self.headers.get("Host", "")):
                return self.send_error(421, "invalid Host header")
            if not self._authed():
                return self._json({"error": "forbidden"}, 403)
            ctx = _docs_context(start)
            if self.path == "/api/skill/global":
                rc = cmd_skill(ctx["root"] or ctx["cwd"], False, True)
                wrote = [str(_home() / ".claude" / "skills"
                             / "unpark-upkeep" / "SKILL.md")]
            elif self.path == "/api/skill/local":
                if not ctx["root"]:
                    return self._json({"ok": False,
                                       "error": "no project here"}, 400)
                rc = cmd_skill(ctx["root"], True, False)
                wrote = [str(ctx["root"] / "AGENTS.md"),
                         str(ctx["root"] / ".claude" / "skills"
                             / "unpark-upkeep" / "SKILL.md")]
            elif self.path.startswith("/api/open"):
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                code, obj = _open_api_response((q.get("p") or [""])[0])
                self._json(obj, code)
                return seen()
            elif self.path.startswith("/api/code"):
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                code, obj = _code_api_response((q.get("p") or [""])[0],
                                               ctx["root"])
                self._json(obj, code)
                return seen()
            elif self.path.startswith("/api/init"):
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                code, obj = _init_api_response(start,
                                               (q.get("loc") or [""])[0])
                self._json(obj, code)
                return seen()
            elif self.path.startswith("/api/stop/"):
                if not ctx["root"]:
                    return self._json({"ok": False,
                                       "error": "no project here"}, 400)
                name = self.path[len("/api/stop/"):]
                rc = stop_recipe(ctx["root"], name)
                sys.stdout.flush()
                self._json({"ok": rc == 0})
                return seen()
            else:
                return self.send_error(404)
            sys.stdout.flush()
            self._json({"ok": rc == 0, "wrote": wrote})
            seen()

        def log_message(self, *args):
            pass

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

        def server_bind(self):
            """Bind without reverse-DNS lookup of the loopback address."""
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    with Server(("127.0.0.1", 0), Handler) as srv:
        srv.timeout = 0.25
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        if on_bound:
            on_bound(url)
        started = time.monotonic()
        while True:
            srv.handle_request()
            now = time.monotonic()
            with lock:
                opened, last = state["opened"], state["last_seen"]
            if not opened:
                if now - started > timeout:
                    return False
            elif now - last > grace:
                return True


# ------------------------------------------------------------------ CLI


# where a project's briefing may live, relative to its root — top level
# preferred, but company repos may only allow docs/ (or a hidden dir)
_WELCOME_LOCATIONS = (
    "WELCOME.md", "docs/WELCOME.md", ".unpark/WELCOME.md",
)


def find_welcome_file(directory) -> "Path | None":
    """The briefing file for a project rooted at directory, if any."""
    d = Path(directory)
    for rel in _WELCOME_LOCATIONS:
        f = d / rel
        if f.is_file():
            return f
    return None


def find_project(start) -> "tuple | None":
    """Walk upward from start; return (root, welcome_file) or None.

    The root is the directory the file was found from (the repo root
    when the file lives at docs/WELCOME.md), so recipe `dir:` paths stay
    repo-relative regardless of where the briefing is kept.
    """
    p = Path(start).resolve()
    for candidate in [p, *p.parents]:
        f = find_welcome_file(candidate)
        if f:
            return candidate, f.resolve()
    return None


def find_child_projects(start) -> list:
    """Immediate subdirectories that carry their own briefing."""
    p = Path(start).resolve()
    try:
        entries = list(p.iterdir())
    except OSError:
        return []
    kids = []
    for d in entries:
        try:
            if d.is_dir() and find_welcome_file(d) is not None:
                kids.append(d)
        except OSError:
            continue  # one unreadable entry must not kill the portfolio
    return sorted(kids)


def _child_hint(start) -> str:
    kids = find_child_projects(start)
    if not kids:
        return ""
    listing = " · ".join(k.name for k in kids)
    return (f"projects below you: {listing}\n"
            f"  (cd into one, or: unpark -C {kids[0].name})")


def _load(welcome_file) -> Welcome:
    return parse_welcome(Path(welcome_file).read_text())


def _derived(root, w: Welcome) -> dict:
    n, latest = commits_since(root, w.meta.get("updated", ""))
    return {
        "git": git_info(root),
        "stale_count": n,
        "stale_latest": latest,
        "running": list_running(root),
        "today": _dt.date.today().isoformat(),
        "root": root,
        "registered": registry_has(root),
        "runners": detect_task_runners(root),
    }


def _use_color(flag_no_color: bool) -> bool:
    if flag_no_color or os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def main(argv=None) -> int:
    """CLI entry point: _main plus clean Ctrl+C handling."""
    try:
        return _main(argv)
    except KeyboardInterrupt:
        for pid in list(_ATTACHED_PIDS):
            _terminate(pid)
        # 128 + SIGINT, the shell convention; no traceback noise
        print("unpark: interrupted", file=sys.stderr)
        return 130


def _main(argv=None) -> int:
    import argparse

    here = find_project(".")
    if here:
        try:
            here_name = (parse_welcome(here[1].read_text())
                         .meta.get("name") or here[0].name)
        except OSError:
            here_name = here[0].name
        where = f"current project: {here_name} — {here[0]}"
    else:
        where = f"no project here ({Path.cwd()})"
    ap = argparse.ArgumentParser(
        prog="unpark",
        description="The project welcomes you back: briefing, recipes, "
                    "and demo launcher. `wb` is an optional shorthand for "
                    f"“welcome back”.\n({where})",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  unpark                      briefing (or portfolio when above projects)
  unpark start demo           launch the demo as a managed process
  unpark html                 interactive briefing dashboard in the browser
  unpark demo                 open a disposable example project
  unpark manual               full context-aware HTML manual
  unpark skill --install      LLM upkeep skill into THIS repo
                              (AGENTS.md + .claude/skills — teammates get it)
  unpark skill --install --global
                              LLM upkeep skill for ALL projects on this
                              machine (use --target claude|codex|copilot|all;
                              default: claude; repo untouched)
  unpark shell fish --install
                              fish hook: the briefing runs itself when
                              you change into a project directory
  unpark register             add this project to your portfolio
  unpark projects             page registered projects
  unpark projects --pick      choose one before opening its briefing
  unpark project NAME         open one registered project's briefing
  unpark code                 open the project in VS Code
  unpark help                 this help

The optional `wb` shorthand means “welcome back”. The dashboard
(`unpark html`) offers all of this as buttons.
""")
    ap.add_argument("-C", dest="dir", default=".",
                    help="project directory (default: walk up from cwd)")
    ap.add_argument("--file", dest="welcome_file",
                    help="explicit briefing file (default: WELCOME.md, "
                         "docs/WELCOME.md or .unpark/WELCOME.md, walking "
                         "up; env UNPARK_FILE works too)")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--no-pager", action="store_true",
                    help="print terminal output directly")
    ap.add_argument("--version", action="store_true")
    sub = ap.add_subparsers(dest="cmd", metavar="command")

    sub.add_parser("init", help="create a WELCOME.md template")
    p = sub.add_parser(
        "demo", help="try unpark on a generated sample",
        description="Create a generated sample project and show its briefing. "
                    "Without DESTINATION it is temporary and deleted before "
                    "the command exits; with --html it remains until the "
                    "dashboard closes. Supply DESTINATION to keep it.")
    p.add_argument("destination", nargs="?",
                   help="directory to keep the demo in (default: temporary "
                        "and deleted after use)")
    p.add_argument("--no-open", action="store_true",
                   help="with --html, print the dashboard URL without opening it")
    p.add_argument("--html", action="store_true",
                   help="serve its dashboard; a temporary demo is deleted "
                        "when the dashboard closes")
    sub.add_parser("register", help="add this project to your portfolio "
                   "(user config; shown by `unpark` from anywhere)")
    sub.add_parser("unregister", help="remove this project from your "
                   "portfolio")
    p = sub.add_parser("projects", help="page registered projects")
    p.add_argument("--pick", action="store_true",
                   help="choose a registered project before opening it")
    p = sub.add_parser("project", help="open one registered project's briefing")
    p.add_argument("name", help="registered project name")
    sub.add_parser("code", help="open this project in VS Code "
                   "(workspace file if one exists, else the folder)")
    p = sub.add_parser("workspace", help="show how VS Code opens this "
                       "project; `init` migrates folder→workspace, "
                       "`set PATH` records an external workspace file")
    p.add_argument("action", nargs="?", choices=["init", "set"])
    p.add_argument("path", nargs="?")
    sub.add_parser("help", help="show this help")
    sub.add_parser("manual", help="open the context-aware HTML manual")
    p = sub.add_parser("run", help="run a recipe in the foreground "
                       "(no name: interactive picker; args after -- are "
                       "appended to the last step)")
    p.add_argument("recipe", nargs="?")
    p.add_argument("args", nargs=argparse.REMAINDER,
                   help="extra arguments for the recipe (after --)")
    p = sub.add_parser("start", help="start a recipe as a managed process "
                       "(no name: interactive picker)")
    p.add_argument("recipe", nargs="?")
    p = sub.add_parser("stop", help="stop managed process(es)")
    p.add_argument("recipe", nargs="?")
    sub.add_parser("ps", help="list managed processes")
    p = sub.add_parser("logs", help="show a managed process's log")
    p.add_argument("recipe")
    p.add_argument("-n", type=int, default=40, help="lines to show")
    p = sub.add_parser("html", help="open the briefing in your browser "
                       "(served ephemerally — no file left behind)")
    p.add_argument("-o", dest="out", help="write to a file instead of serving")
    p.add_argument("--open", action="store_true",
                   help="with -o: also open the written file")
    p.add_argument("--no-open", action="store_true",
                   help="serve but don't launch a browser (prints the URL)")
    p = sub.add_parser("check", help="validate WELCOME.md (CI-friendly)")
    p.add_argument("--strict", action="store_true",
                   help="treat warnings as failures")
    p = sub.add_parser("skill", help="print LLM upkeep instructions; "
                       "--install writes them for agents to follow")
    p.add_argument("--install", action="store_true",
                   help="install into THIS repo: AGENTS.md block + "
                        ".claude/skills/unpark-upkeep/ (shared with "
                        "teammates and non-Claude tools)")
    p.add_argument("--global", dest="global_", action="store_true",
                   help="install for ALL projects on this machine: "
                        "choose one or more --target values; the repo is "
                        "left untouched")
    p.add_argument("--target", action="append",
                   choices=(*_GLOBAL_SKILL_TARGETS, "all"),
                   help="global integration to install (repeatable): "
                        "claude, codex, copilot, or all; default: claude")
    p = sub.add_parser("shell", help="shell integration: print, install "
                       "or remove the directory-change hook")
    p.add_argument("target", choices=_SHELL_TARGETS,
                   help="shell to integrate (only fish for now)")
    shell_scope = p.add_mutually_exclusive_group()
    shell_scope.add_argument("--install", action="store_true",
                             help="write the hook into the shell's conf.d "
                                  "(fish: ~/.config/fish/conf.d/unpark.fish)")
    shell_scope.add_argument("--uninstall", action="store_true",
                             help="remove the hook again")
    p = sub.add_parser("cd-hook", help="internal: what the installed shell "
                       "hook runs when you change into DIR (fine to call "
                       "by hand)")
    p.add_argument("directory", help="directory the shell changed into")
    p.add_argument("--from", dest="from_dir",
                   help="previous directory — moving within one project "
                        "or repo stays silent")

    args = ap.parse_args(argv)

    if args.version:
        print(f"unpark {__version__} ({Path(__file__).resolve()})")
        return 0

    if args.cmd == "help":
        ap.print_help()
        return 0

    if args.cmd in ("projects", "project"):
        projects = gather_registered()
        if not projects:
            print("no projects registered yet — `unpark register` inside "
                  "a project adds it (or use the dashboard button)")
            return 0
        if args.cmd == "project":
            matches = select_registered_project(projects, args.name)
            if not matches:
                print(f"unpark: no registered project named {args.name!r}",
                      file=sys.stderr)
                return 1
            if len(matches) > 1:
                print(f"unpark: {args.name!r} matches multiple registered "
                      "projects:", file=sys.stderr)
                for project in matches:
                    print(f"  {project['path']}", file=sys.stderr)
                print("use `unpark projects --pick` to choose one",
                      file=sys.stderr)
                return 1
            return show_registered_project(
                matches[0], no_pager=args.no_pager, no_color=args.no_color)
        if args.pick:
            selected = pick_registered_project(projects)
            if selected is False:
                print("unpark: choose one of the listed project numbers",
                      file=sys.stderr)
                return 1
            if selected is None:
                return 0
            return show_registered_project(
                selected, no_pager=args.no_pager, no_color=args.no_color)
        display_text(render_portfolio_text(
            projects, args.dir, color=_use_color(args.no_color),
            title=f"{len(projects)} registered project"
                  f"{'s' if len(projects) != 1 else ''} "
                  f"({registry_file()})"), no_pager=args.no_pager)
        return 0

    if args.cmd == "manual":
        # inside a project the project comes first: serve its dashboard,
        # which introduces the tool briefly and links to /manual; the
        # bare manual appears only when there is no project to show
        found_m = find_project(args.dir)
        if found_m:
            root_m, file_m = found_m
            w_m = _load(file_m)

            def _mbound(url):
                print(f"project dashboard at {url} — tool manual linked "
                      f"there (directly: {url}manual); exits when the "
                      "page closes")
                sys.stdout.flush()
                if not _env("NO_BROWSER"):
                    open_browser(url)

            opened = serve_dashboard(root_m, w_m, on_bound=_mbound)
        else:
            def _mbound(url):
                print(f"manual at {url} — exits when the page closes")
                sys.stdout.flush()
                if not _env("NO_BROWSER"):
                    open_browser(url)

            opened = serve_docs(args.dir, on_bound=_mbound)
        if opened:
            return 0
        print("unpark: nobody opened the page within 120s — giving up",
              file=sys.stderr)
        return 1

    if args.cmd == "init":
        return cmd_init(args.dir)
    if args.cmd == "demo":
        if args.no_open and not args.html:
            ap.error("demo --no-open requires --html")
        return cmd_demo(args.destination, args.html, args.no_open)
    # user-level and hook commands: valid from anywhere, no project needed
    if args.cmd == "shell":
        return cmd_shell(args.target, args.install, args.uninstall)
    if args.cmd == "cd-hook":
        return cmd_cd_hook(args.directory, args.from_dir)

    explicit = args.welcome_file or _env("FILE")
    if explicit:
        wf = Path(explicit)
        if not wf.is_absolute():
            wf = Path(args.dir) / wf
        if not wf.is_file():
            print(f"unpark: briefing file not found: {explicit}",
                  file=sys.stderr)
            return 1
        found = (Path(args.dir).resolve(), wf.resolve())
    else:
        found = find_project(args.dir)

    if found is None:
        # a repo without a briefing: serve the setup page (manual with
        # create-WELCOME.md buttons) instead of bouncing to the terminal
        if args.cmd == "html" and (Path(args.dir) / ".git").exists():
            def _sbound(url):
                print(f"no briefing here yet — setup page at {url}")
                sys.stdout.flush()
                if not (args.no_open
                        or _env("NO_BROWSER")):
                    open_browser(url)

            if serve_docs(args.dir, on_bound=_sbound):
                return 0
            print("unpark: nobody opened the page within 120s — giving up",
                  file=sys.stderr)
            return 1
        # not inside a project — but standing over some? show the portfolio
        projects = (gather_portfolio(args.dir, extra=registry_load())
                    if args.cmd in (None, "html") else [])
        if projects:
            if args.cmd is None:
                display_text(render_portfolio_text(
                    projects, args.dir, color=_use_color(args.no_color)),
                    no_pager=args.no_pager)
                return 0
            page = render_portfolio_html(projects, args.dir)
            if args.out:
                Path(args.out).write_text(page, encoding="utf-8")
                print(f"wrote {args.out}")
                if args.open:
                    open_browser(Path(args.out).resolve().as_uri())
                return 0

            def _pbound(url):
                print(f"portfolio at {url} — served once, nothing written "
                      "to disk")
                sys.stdout.flush()
                if not args.no_open:
                    open_browser(url)

            if _serve_page_once(page, on_bound=_pbound):
                return 0
            print("unpark: nobody fetched the page within 120s — giving up",
                  file=sys.stderr)
            return 1
        print(f"unpark: no WELCOME.md found from {Path(args.dir).resolve()} "
              "upward — run `unpark init` to create one", file=sys.stderr)
        hint = _child_hint(args.dir)
        if hint:
            print(hint, file=sys.stderr)
        return 1
    root, welcome_file = found
    w = _load(welcome_file)

    # nested projects exist (a repo's WELCOME.md above a subproject's):
    # when the resolved project is not where the user stands, say which
    # WELCOME.md is in charge
    resolved_elsewhere = Path(args.dir).resolve() != root
    if args.cmd in ("run", "start", "stop", "ps", "logs") and resolved_elsewhere:
        print(f"project: {w.meta.get('name', root.name)} — {root}")
        hint = _child_hint(args.dir)
        if hint:
            print(hint)

    if args.cmd is None:
        if has_conflict_markers(welcome_file.read_text()):
            print("⚠ WELCOME.md contains unresolved merge conflict markers "
                  "— the briefing below may be garbled; resolve the merge "
                  "(keep both sides' facts, newest first, updated: = the "
                  "later date)", file=sys.stderr)
        display_text(render_text(w, _derived(root, w),
                                 _use_color(args.no_color)),
                     no_pager=args.no_pager)
        if resolved_elsewhere:
            hint = _child_hint(args.dir)
            if hint:
                print(hint)
        return 0
    if args.cmd in ("run", "start") and not args.recipe:
        if sys.stdin.isatty() and sys.stdout.isatty():
            picked = pick_recipe(w, args.cmd)
            if not picked:
                return 2
            args.recipe = picked
        else:
            names = "\n".join(f"  {r.name}  {r.description}"
                              for r in w.recipes) or "  (none)"
            print(f"unpark: which recipe? available:\n{names}",
                  file=sys.stderr)
            return 2
    if args.cmd == "run":
        extra = list(args.args or [])
        if extra and extra[0] == "--":
            extra = extra[1:]  # only the separator; deeper -- pass through
        return run_recipe(root, w, args.recipe, extra_args=extra or None)
    if args.cmd == "start":
        return start_recipe(root, w, args.recipe)
    if args.cmd == "stop":
        return stop_recipe(root, args.recipe)
    if args.cmd == "ps":
        running = list_running(root)
        if not running:
            print("nothing running")
        for r in running:
            extra = f"  {r['url']}" if r.get("url") else ""
            extra += (f"  log: {r['log']}" if r.get("log")
                      else "  (attached to its start terminal)")
            print(f"{r['recipe']}  pid {r['pid']}  since {r['started']}"
                  + extra)
        return 0
    if args.cmd == "logs":
        pf = state_dir(root) / f"{args.recipe}.log"
        if not pf.exists():
            print(f"unpark: no log for '{args.recipe}'", file=sys.stderr)
            return 1
        for line in pf.read_text(errors="replace").splitlines()[-args.n:]:
            print(line)
        return 0
    if args.cmd == "html":
        page = render_html(w, _derived(root, w))
        if args.out:
            out = Path(args.out)
            out.write_text(page, encoding="utf-8")
            print(f"wrote {out}")
            if args.open:
                open_browser(out.resolve().as_uri())
            return 0

        def _bound(url):
            print(f"briefing at {url} — buttons start/stop recipes; "
                  "exits when the page closes (nothing written to disk)")
            sys.stdout.flush()
            if not args.no_open:
                open_browser(url)

        if serve_dashboard(root, w, on_bound=_bound):
            print("page closed — exiting; started demos keep running "
                  "(`unpark ps` / `unpark stop`)")
            return 0
        print("unpark: nobody opened the page within 120s — giving up",
              file=sys.stderr)
        return 1
    if args.cmd == "code":
        cmd = code_command(root, w)
        target = Path(cmd[1])
        kind = ("workspace" if target.suffix == ".code-workspace"
                else "folder")
        print(f"opening VS Code ({kind}): {target}")
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            print("unpark: `code` not found on PATH — install the "
                  "'code' shell command from VS Code", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "workspace":
        if args.action == "init":
            res = create_workspace_file(root, w)
            if not res["ok"]:
                print(f"unpark: {res['error']}", file=sys.stderr)
                return 1
            print(f"created {res['target']} (folders: ['.'])")
            print("add further folders in VS Code (File → Add Folder to "
                  "Workspace…), then save. Registration and unpark state "
                  "are anchored to this repo folder — nothing to migrate.")
            print("⚠ VS Code chat history (Copilot/Claude sessions) does "
                  "NOT carry over to an externally created workspace file: "
                  "VS Code only migrates workspace storage when YOU convert "
                  "inside it. If those sessions matter, delete this file "
                  "and instead open the folder in VS Code and use "
                  "File → Save Workspace As… into the repo root — unpark "
                  "will detect the result automatically.")
            return 0
        if args.action == "set":
            if not args.path:
                print("unpark: workspace set needs a path", file=sys.stderr)
                return 1
            ws = Path(args.path)
            if not ws.is_absolute():
                ws = Path(args.dir) / ws
            if not ws.is_file():
                print(f"unpark: workspace file not found: {args.path}",
                      file=sys.stderr)
                return 1
            rel = os.path.relpath(ws.resolve(), root)
            set_front_matter_key(welcome_file, "workspace", rel)
            print(f"recorded workspace: {rel} in {welcome_file.name} — "
                  "re-entry now opens the workspace, not the folder")
            return 0
        target = vscode_target(root, w)
        kind = ("workspace" if str(target).endswith(".code-workspace")
                else "folder")
        source = ("front-matter `workspace:` key"
                  if w.meta.get("workspace") else
                  "detected in repo" if kind == "workspace" else
                  "no .code-workspace found")
        print(f"VS Code opens this project as: {kind} — {target}")
        print(f"({source})")
        if kind == "folder":
            print("migrate with `unpark workspace init`, or record an "
                  "external file with `unpark workspace set PATH`")
        return 0
    if args.cmd == "register":
        registry_add(root)
        print(f"registered {root} in your portfolio ({registry_file()})")
        return 0
    if args.cmd == "unregister":
        registry_remove(root)
        print(f"removed {root} from your portfolio")
        return 0
    if args.cmd == "check":
        return cmd_check(root, w, welcome_file, strict=args.strict)
    if args.cmd == "skill":
        if args.target and not args.global_:
            ap.error("skill --target requires --global")
        return cmd_skill(root, args.install, args.global_, args.target)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
