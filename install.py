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
import tempfile
import urllib.request
import zipfile
from typing import List, Optional

try:
    import hou
except ImportError:  # pragma: no cover
    hou = None

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PANEL_SOURCE = os.path.join(REPO_ROOT, "python_panels", "parmesan.pypanel")
INJECTION_MARKER = "# PARMESAN_PATH_INJECTION"
ENV_MARKER = "# added by parmesan install.py"

#: Where update() pulls from.
REPO_ZIP = "https://github.com/bluegiantArt/Parmesan/archive/refs/heads/main.zip"

#: Only these are replaced by an update. Anything else in the folder is left
#: alone: an update should never be able to delete something it did not put
#: there, and a stray scene file next to the code is not ours to remove.
UPDATABLE = ("parmesan", "python_panels", "tests", "install.py", "README.md")


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


def update(url: str = REPO_ZIP, verbose: bool = True) -> List[str]:
    """Download the latest code over this folder. Run from inside Houdini:

        import install; install.update()

    Then close and reopen the Parmesan panel -- it reloads its modules on
    open, so no Houdini restart is needed. Re-run install() only if told the
    panel file itself changed.

    Local edits to the tracked files are overwritten. Everything not listed in
    UPDATABLE is left untouched.
    """
    report: List[str] = []

    def say(line: str) -> None:
        report.append(line)
        if verbose:
            print(line)

    say(f"Downloading {url}")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "parmesan.zip")
            urllib.request.urlretrieve(url, archive)
            extracted = os.path.join(tmp, "extracted")
            with zipfile.ZipFile(archive) as handle:
                _safe_extract(handle, extracted)
            source = _archive_root(extracted)
            if source is None:
                say("FAIL The download did not contain a Parmesan folder.")
                return report
            changed = _copy_over(source, REPO_ROOT)
            for name in changed:
                say(f"  updated {name}")
            say(f"OK   Updated {len(changed)} item(s) in {REPO_ROOT}")
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        say(f"FAIL Could not update: {exc}")
        say("     Download the ZIP from GitHub by hand and unzip it over this folder.")
        return report

    say("")
    say("Next: close and reopen the Parmesan panel to load the new code.")
    return report


def _safe_extract(handle: zipfile.ZipFile, destination: str) -> None:
    """Extract, refusing entries that would escape the destination.

    A zip can name ``../../etc/thing``; this one comes from GitHub so it will
    not, but code that writes files from a downloaded archive should never be
    the thing that takes the check on trust.
    """
    root = os.path.abspath(destination)
    for member in handle.namelist():
        target = os.path.abspath(os.path.join(root, member))
        if not target.startswith(root + os.sep) and target != root:
            raise ValueError(f"archive entry escapes the destination: {member}")
    handle.extractall(destination)


def _archive_root(extracted: str) -> Optional[str]:
    """GitHub wraps everything in one folder named after the branch."""
    entries = [
        os.path.join(extracted, name)
        for name in os.listdir(extracted)
        if os.path.isdir(os.path.join(extracted, name))
    ]
    for candidate in [extracted] + entries:
        if os.path.exists(os.path.join(candidate, "install.py")):
            return candidate
    return None


def _copy_over(source: str, destination: str) -> List[str]:
    """Replace the updatable items. Returns what was replaced."""
    changed: List[str] = []
    for name in UPDATABLE:
        incoming = os.path.join(source, name)
        if not os.path.exists(incoming):
            continue
        target = os.path.join(destination, name)
        if os.path.isdir(incoming):
            if os.path.isdir(target):
                shutil.rmtree(target)
            shutil.copytree(incoming, target)
        else:
            shutil.copy2(incoming, target)
        changed.append(name)
    return changed


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
