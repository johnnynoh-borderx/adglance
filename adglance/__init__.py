"""adglance: every ad's spend at a glance, in the terminal. Read-only."""
import functools
import pathlib
import subprocess
from importlib import metadata


def _pep440(described):
    """git describe's v0.1.0-3-gc5002fb as an installed copy names it,
    0.1.1.dev3+gc5002fb, so a checkout and an install compare at a glance."""
    dirty = described.endswith("-dirty")
    described = described.removesuffix("-dirty").removeprefix("v")
    parts = described.rsplit("-", 2)
    if len(parts) == 3 and parts[1].isdigit():
        tag, n, sha = parts
        nums = tag.split(".")
        nums[-1] = str(int(nums[-1]) + 1) if nums[-1].isdigit() else nums[-1]
        described = f"{'.'.join(nums)}.dev{n}+{sha}"
    elif "." not in described:                        # no tag yet: just the hash
        described = f"0.0.0+g{described}"
    return described + (" (uncommitted changes)" if dirty else "")


@functools.cache
def version():
    """This copy's version: from git when it runs from a checkout (an editable
    install, always current), else what it was installed as -- the tag
    (0.1.0), or the commits since one with the last's hash (0.1.1.dev3+gc5002fb)."""
    here = pathlib.Path(__file__).resolve().parent.parent
    if (here / ".git").exists():
        try:
            git = ["git", "-C", str(here)]
            described = subprocess.run([*git, "describe", "--tags", "--always", "--dirty", "--abbrev=9"],
                                       capture_output=True, text=True, timeout=2).stdout.strip()
            day = subprocess.run([*git, "log", "-1", "--format=%cs"],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
            if described:
                shown = _pep440(described)
                return f"{shown} ({day})" if day else shown
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return metadata.version("adglance")
    except metadata.PackageNotFoundError:
        return "unknown"
