"""TTY-aware paging for human-facing terminal output."""

import os
import shlex
import shutil
import subprocess
import sys


def display(text: str, *, no_pager: bool = False, stream=None,
            environ=None, terminal_lines=None, run=None) -> bool:
    """Write text, paging only when an interactive terminal overflows.

    Returns whether a pager was started. The optional arguments keep this
    policy straightforward to test without a real terminal or pager.
    """
    stream = stream or sys.stdout
    environ = os.environ if environ is None else environ
    if (no_pager or "NO_PAGER" in environ
            or not getattr(stream, "isatty", lambda: False)()):
        print(text, file=stream)
        return False

    lines = (terminal_lines if terminal_lines is not None
             else shutil.get_terminal_size(fallback=(80, 24)).lines)
    if len(text.splitlines()) <= lines:
        print(text, file=stream)
        return False

    command = shlex.split(environ.get("PAGER", "less"))
    if not command:
        print(text, file=stream)
        return False
    child_env = dict(environ)
    child_env.setdefault("LESS", "FRX")
    run = subprocess.run if run is None else run
    try:
        run(command, input=text + "\n", text=True, env=child_env,
            check=False)
    except OSError:
        print(text, file=stream)
        return False
    return True
