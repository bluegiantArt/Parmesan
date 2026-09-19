"""Install the Parmesan panel into Houdini. Run this inside Houdini.

    import sys; sys.path.insert(0, r"/path/to/Parmesan")
    import install; install.install()

What it does:

1. Copies ``python_panels/parmesan.pypanel`` into your Houdini preferences,
   with this repo's path baked into it -- so the panel imports parmesan
   without any environment setup.
2. Adds this repo to ``PYTHONPATH`` in ``houdini.env``, so the generated parm
   callbacks still find parmesan after a restart when the panel has not been
   opened. A timestamped backup is made first, the edit is one line, and it is
   skipped if already present. Pass ``update_env=False`` to do it yourself.

Nothing here has been run against a live Houdini yet. If a step fails it says
which one, and no step depends on a later one succeeding.
"""

from __future__ import annotations

import datetime
import os
import shutil
from typing import List, Optional

try:
    import hou
except ImportError:  # pragma: no cover
    hou = None

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PANEL_SOURCE = os.path.join(REPO_ROOT, "python_panels", "parmesan.pypanel")
INJECTION_MARKER = "# PARMESAN_PATH_INJECTION"
ENV_MARKER = "# added by parmesan install.py"


def prefs_dir() -> str:
    """Houdini's user preference directory."""
    if hou is not None:
        return hou.expandString("$HOUDINI_USER_PREF_DIR")
    guess = os.environ.get("HOUDINI_USER_PREF_DIR")
    if not guess:
        raise RuntimeError(
            "Run this from inside Houdini, or set HOUDINI_USER_PREF_DIR first."
        )
    return guess


def install(update_env: bool = True, verbose: bool = True) -> List[str]:
    """Do the install. Returns the lines it reported, for testing."""
    report: List[str] = []

    def say(line: str) -> None:
        report.append(line)
        if verbose:
            print(line)

    prefs = prefs_dir()
    say(f"Houdini preferences: {prefs}")
    say(f"Parmesan repo:       {REPO_ROOT}")

    # ---- 1. the panel file ------------------------------------------------
    try:
        target = _install_panel(prefs)
        say(f"OK   Panel installed: {target}")
    except OSError as exc:
        say(f"FAIL Could not write the panel file: {exc}")
        return report

    # ---- 2. houdini.env --------------------------------------------------
    if update_env:
        try:
            outcome = _update_env(prefs)
            say(f"OK   {outcome}")
        except OSError as exc:
            say(f"WARN Could not edit houdini.env: {exc}")
            say(f"     Add this line yourself: {_env_line()}")
    else:
        say(f"SKIP houdini.env untouched. Add this line yourself: {_env_line()}")

    say("")
    say("Next: in Houdini, open a pane tab menu -> New Pane Tab Type ->")
    say("      Python Panel, then pick 'Parmesan' from its menu.")
    say("      Restart Houdini once so the PYTHONPATH change takes effect.")
    return report


# --------------------------------------------------------------------------

def _install_panel(prefs: str) -> str:
    """Write the .pypanel with this repo's path injected."""
    with open(PANEL_SOURCE, "r", encoding="utf-8") as handle:
        text = handle.read()

    bootstrap = (
        "import sys\n"
        f"_parmesan_root = r{REPO_ROOT!r}\n"
        "if _parmesan_root not in sys.path:\n"
        "    sys.path.insert(0, _parmesan_root)"
    )
    if INJECTION_MARKER not in text:
        raise OSError(f"{PANEL_SOURCE} is missing its {INJECTION_MARKER} line")
    text = text.replace(INJECTION_MARKER, bootstrap)

    panels_dir = os.path.join(prefs, "python_panels")
    os.makedirs(panels_dir, exist_ok=True)
    target = os.path.join(panels_dir, "parmesan.pypanel")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text)
    return target


def _env_line() -> str:
    """The houdini.env line, with the separator this platform uses."""
    sep = ";" if os.name == "nt" else ":"
    return f'PYTHONPATH = "{REPO_ROOT}{sep}$PYTHONPATH"'


def _update_env(prefs: str) -> str:
    """Append the PYTHONPATH line to houdini.env, once, with a backup."""
    path = os.path.join(prefs, "houdini.env")
    line = _env_line()

    existing = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            existing = handle.read()

    # Idempotent: match on the repo path rather than the whole line, so a
    # hand-edited variant is still recognised and not duplicated.
    for raw in existing.splitlines():
        if REPO_ROOT in raw and "PYTHONPATH" in raw and not raw.strip().startswith("#"):
            return f"houdini.env already lists this repo ({path})"

    if existing:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{path}.{stamp}.bak"
        shutil.copy2(path, backup)
        note = f"houdini.env updated (backup: {os.path.basename(backup)})"
    else:
        note = f"houdini.env created ({path})"

    suffix = "" if existing.endswith("\n") or not existing else "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{suffix}\n{ENV_MARKER}\n{line}\n")
    return note


def uninstall(verbose: bool = True) -> List[str]:
    """Remove the panel file. Leaves houdini.env alone -- an automatic edit to
    a file that can stop Houdini starting is worth making, but not worth
    reversing blind."""
    report: List[str] = []

    def say(line: str) -> None:
        report.append(line)
        if verbose:
            print(line)

    target = os.path.join(prefs_dir(), "python_panels", "parmesan.pypanel")
    if os.path.exists(target):
        os.remove(target)
        say(f"Removed {target}")
    else:
        say(f"Nothing to remove at {target}")
    say("houdini.env untouched. Remove this line by hand if you want it gone:")
    say(f"    {_env_line()}")
    return report


if __name__ == "__main__":
    install()
