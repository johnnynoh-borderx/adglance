"""The ad accounts adglance reads, and everything that differs between them:
~/.config/adglance/accounts.json, readable by you alone (600).

    {"accounts": [
      {"platform": "tiktok", "id": "7000000000000000001", "label": "Brand US",
       "token": "…", "currency": "KRW", "timezone": "Asia/Seoul"},
      {"platform": "meta", "id": "act_123", "label": "Brand CA", "token": "…"}
    ]}

    platform  tiktok or meta
    id        the ad account (TikTok advertiser id; Meta act_…)
    token     a read-only access token for it
    label     what the account picker shows
    currency, timezone   filled in by setup from the platform itself

What a row is judged against (targets), and everything else about the screen,
is that account's own file, accounts/<platform>-<id>.json (settings.py).

`adglance setup` writes it (the onboarding screen on the first run); after that
it is yours to edit by hand as well. Nothing here is sent anywhere but the
platform the token belongs to.
"""
import json
import os
import pathlib
import re

PATH = pathlib.Path.home() / ".config" / "adglance" / "accounts.json"


def load():
    """([account dicts], [problems]). No file is no accounts, not a problem."""
    try:
        data = json.loads(PATH.read_text())
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError) as e:
        return [], [f"accounts.json: cannot read it ({e})"]
    out, problems = [], []
    raw = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return [], ['accounts.json: expected {"accounts": [...]}']
    for i, acc in enumerate(raw):
        where = f"accounts.json: account {i + 1}"
        if not isinstance(acc, dict) or not acc.get("platform") or not acc.get("id"):
            problems.append(f"{where}: needs at least platform and id -- skipped")
            continue
        acc = dict(acc)
        if "fee" in acc:                             # an option now, like any other
            problems.append(f"{where}: fee is an option now -- put \"fee\": {acc.pop('fee')} in "
                            "this account's own file (, on the screen); not used here")
        acc.pop("profile", None)                     # an account's options are its own file now
        if acc.pop("gates", None):
            problems.append(f"{where}: gates are targets in settings.json now -- not used here")
        acc["id"] = str(acc["id"])
        acc.setdefault("label", acc["id"])
        out.append(acc)
    return out, problems


def save(accounts):
    """Write the file, readable by this user alone."""
    PATH.parent.mkdir(parents=True, exist_ok=True)
    plain = [{k: v for k, v in acc.items() if not k.startswith("_")} for acc in accounts]
    tmp = PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"accounts": plain}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, PATH)


def loose():
    """True when someone besides this user can read the file."""
    try:
        return bool(PATH.stat().st_mode & 0o077)
    except OSError:
        return False
