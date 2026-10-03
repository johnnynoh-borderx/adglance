"""adglance: every ad's spend at a glance, in the terminal. Read-only."""
import functools
import pathlib
import subprocess
from importlib import metadata


@functools.cache
def version():
    """This copy's version: from git when it runs from a checkout (an editable
    install, always current), else what it was installed as -- the tag
    (0.1.0), or the commits since one with the last's hash (0.1.1.dev3+gc5002fb)."""
    here = pathlib.Path(__file__).resolve().parent.parent
    if (here / ".git").exists():
        try:
            git = ["git", "-C", str(here)]
            described = subprocess.run([*git, "describe", "--tags", "--always", "--dirty"],
                                       capture_output=True, text=True, timeout=2).stdout.strip()
            day = subprocess.run([*git, "log", "-1", "--format=%cs"],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
            if described:
                return f"{described.removeprefix('v')} ({day})" if day else described.removeprefix("v")
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return metadata.version("adglance")
    except metadata.PackageNotFoundError:
        return "unknown"
