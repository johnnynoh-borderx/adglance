"""adglance's options: ~/.config/adglance/settings.json, in the manner of Ghostty.

Every default lives here, in the code. The file holds only what you change and
starts out empty; a key it leaves out keeps its default. `,` on the screen opens
it in $EDITOR and closing the editor reloads it; `adglance +show-config --default
--docs` prints every option with what it does, as a file you could start from;
`adglance +validate-config` checks it. A mistake never stops adglance: that one
option keeps its default and a strip on top of the screen says why.

Lines starting with // are comments. Accounts and tokens are not here: they
are accounts.json (adglance setup).

Nothing has to be written to start: the defaults work for any account. Options
for one account go in its own file, accounts/<platform>-<id>.json -- `,` on the
screen opens the one for the account shown -- and override only there. No name
to give it and nothing to link: where the file is says what it covers.

    the defaults here  ->  settings.json (every account, optional)
                    ->  its "tiktok" / "meta" section (that platform's accounts)
                    ->  accounts/<platform>-<id>.json (this account)

The screen writes an account's file too -- H, h and shift+arrows for the
number columns, and a new metric -- keeping the comment lines at its top.
"""
import copy
import json
import os
import pathlib
import re

HOME = pathlib.Path.home() / ".config" / "adglance"
PATH = HOME / "settings.json"
PLATFORM_NAMES = ("tiktok", "meta")    # sections of settings.json for one platform's accounts
ACCOUNTS = HOME / "accounts"

METRICS = {
    "billed":      {"name": "Billed",    "formula": "billed",                      "format": "money"},
    "share":       {"name": "Share",                                               "format": "share"},
    "impressions": {"name": "Impr",      "formula": "impressions",                 "format": "number"},
    "views":       {"name": "Views",     "formula": "views",                       "format": "number"},
    "cpm":         {"name": "CPM",       "formula": "billed / impressions * 1000", "format": "money"},
    "cpv":         {"name": "CPV",       "formula": "billed / views",              "format": "cost"},
    "cpv2":        {"name": "2s CPV",    "formula": "billed / views_2s",           "format": "cost"},
    "cpv6":        {"name": "6s CPV",    "formula": "billed / views_6s",           "format": "cost"},
    "cpcv":        {"name": "CPCV",      "formula": "billed / views_100",          "format": "cost"},
    "cpf":         {"name": "CPF",       "formula": "billed / follows",            "format": "cost"},
    "cpe":         {"name": "CPE",       "formula": "billed / engagements",        "format": "cost"},
    "cpc":         {"name": "CPC",       "formula": "billed / clicks",             "format": "cost"},
    "ctr":         {"name": "CTR",       "formula": "clicks / impressions",        "format": "pct"},
    "rate_2s":     {"name": "2s rate",   "formula": "views_2s / views",            "format": "pct"},
    "rate_6s":     {"name": "6s rate",   "formula": "views_6s / views",            "format": "pct"},
    "complete":    {"name": "Complete",  "formula": "views_100 / views",           "format": "pct"},
    "avg_watch":   {"name": "Avg watch", "formula": "watch_time / views",          "format": "number"},
    "er":          {"name": "ER",        "formula": "engagements / impressions",   "format": "pct"},
}

# (key, default, what it does) -- in the order +show-config prints them
OPTIONS = [
    ("fee", 1.0,
     "A multiplier on spend for every cost: billed = spend x fee, and the cost\n"
     "metrics are formulas over billed. 1.15 adds an agency fee of 15%; 1 is the\n"
     "spend as the platform reports it. A formula that wants the raw spend uses\n"
     "spend. e.g. 1.15"),
    ("show", None,
     "The number columns shown, left to right: ids from metrics. null: the\n"
     "platform's own set (TikTok: views and follows; Meta: clicks and completes).\n"
     "H on the screen ticks them and writes this for the account shown."),
    ("sort", "-billed",
     "The starting sort: a column id or header; a leading - sorts largest first."),
    ("pin", None,
     "Name columns kept in view while a wide table scrolls sideways. null pins\n"
     "every name column but status. F on the screen picks them too."),
    ("icons", True,
     "Nerd Font icons in labels, buttons and menus; false for plain words."),
    ("settled_days", 30,
     "Old days do not change, so each is fetched once and kept; only days younger\n"
     "than this are fetched again (on opening, and with r)."),
    ("targets", [],
     "Rows judged against a limit: \"column=value metric <= limit\". The judged cost\n"
     "turns green at or under the limit and red over it, e.g.\n"
     "[\"objective=VV cpv6 <= 20\", \"objective=ENG cpf <= 1700\"]. The column is a\n"
     "name column (see names); the first target that matches a row is used."),
    ("colors", {},
     "Values that get a hue wherever they appear: blue, peach, flamingo, sky, teal,\n"
     "pink, lavender, mauve. Green, red and yellow mean a verdict, so none is\n"
     "offered. e.g. {\"US\": \"blue\", \"VV\": \"peach\"}"),
    ("highlight", [],
     "Values drawn yellow with a trophy, e.g. [\"Winner\"]."),
    ("names", {},
     "How names become columns. Out of the box each name is one column, as it is:\n"
     "Campaign, Ad group (Ad set on Meta), Ad -- always there to show (H) and what\n"
     "the levels group by (1 campaigns, 2 ad groups, 3 ads).\n"
     "Most people need nothing more. If your names are built from pieces\n"
     "(BRAND_US_VV_Launch), split them:\n"
     "  split     the separator, e.g. \"_\"\n"
     "  campaign  a column name for each piece, in order; \"\" skips a piece, and\n"
     "            the last named piece takes whatever is left\n"
     "  adgroup   the same for the ad group (ad set) name\n"
     "  ad        the same for the ad name\n"
     "  labels    short headers, e.g. {\"objective\": \"Obj\"}\n"
     "  columns   which columns show, in order (status: the ad's state)\n"
     "Advanced, for names a separator cannot read:\n"
     "  patterns  {\"campaign\": [regex, ...], \"adgroup\": [...], \"ad\": [...]}: in order, the\n"
     "            first that matches fills the columns named by its (?P<name>...)\n"
     "            groups. Replaces campaign / ad.\n"
     "  defaults  a value for a group that matched empty\n"
     "  months    columns whose January..December become 01..12\n"
     "  make      a column built from others: {\"flight\": \"{Year}-{Month}\"}\n"
     "  spaced    columns whose SpringTeaser becomes Spring Teaser\n"
     "examples/advanced.json in the repository uses all of it. The whole names\n"
     "object is replaced when you set it."),
    ("metrics", METRICS,
     "Number columns: a formula over the counts, and a format. Yours are added to\n"
     "these by id (the same id replaces one).\n"
     "  counts  spend  billed (spend x fee)  impressions  clicks\n"
     "          views (plays)  views_2s  views_6s  views_25  views_50  views_75\n"
     "          views_100  watch_time (seconds)  likes  comments  shares  follows\n"
     "          profile_visits  engagements\n"
     "  format  money  cost (one decimal)  pct  number  share (part of the spend)\n"
     "+ - * / and brackets only. A total or a group sums the counts first, then\n"
     "applies the formula, so its rate is the real one. A count a platform does\n"
     "not report (Meta has no 6-second view) is 0, and a rate over it shows -."),
]
DEFAULTS = {key: value for key, value, _ in OPTIONS}
NAME_KEYS = {"split", "campaign", "adgroup", "ad", "labels", "columns", "patterns", "defaults", "months",
             "make", "spaced"}
KINDS = {list: "a list [...]", dict: "an object {...}", str: "text", bool: "true or false",
         int: "a whole number", float: "a number"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strip_comments(text):
    return "\n".join("" if line.lstrip().startswith("//") else line for line in text.splitlines())


def account_path(platform, account_id):
    """The one account's file: accounts/tiktok-7621….json, act_ kept as Meta has it."""
    return ACCOUNTS / f"{platform}-{account_id}.json"


def account_files():
    try:
        return sorted(ACCOUNTS.glob("*.json"))
    except OSError:
        return []


def read(path=None):
    """(what the file says, [problems]). No file is {}, not a problem."""
    path = path or PATH
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}, []
    except OSError as e:
        return {}, [f"cannot open it ({e.strerror}) -- running on the defaults"]
    if not text.strip():
        return {}, []
    try:
        mine = json.loads(_strip_comments(text))
    except ValueError as e:
        return {}, [f"not JSON ({e}) -- running on the defaults"]
    if not isinstance(mine, dict):
        return {}, ["expected an object { ... } -- running on the defaults"]
    return mine, []


def _same_kind(value, default):
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):                    # fee: 1 or 1.15
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, type(default))


def _pieces(names, sep, problems, src):
    """A regex from piece names: BRAND_US_VV -> (?P<brand>…)_(?P<geo>…)_…; names
    with fewer pieces fill what they have, the last named piece takes the rest."""
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        problems.append(f"names.{src}: a list of column names, \"\" to skip a piece")
        return None
    bad = [n for n in names if n and not _IDENT.match(n)]
    if bad:
        problems.append(f"names.{src}: {', '.join(map(repr, bad))} -- letters, digits and _ only")
        return None
    if not any(names):
        return None
    if not sep or len(names) == 1:
        return f"^(?P<{names[0]}>.*)$" if names[0] else None
    s = re.escape(sep)
    last = max(i for i, n in enumerate(names) if n)
    out = ""
    for i in range(len(names) - 1, -1, -1):
        body = ".*" if i >= last else f"[^{s}]*"
        piece = f"(?P<{names[i]}>{body})" if names[i] else body
        out = piece + (f"(?:{s}{out})?" if out else "")
    return f"^{out}$"


def names_spec(names, problems):
    """The names option -> what style.Layout reads (patterns per source)."""
    if not isinstance(names, dict):
        problems.append("names: an object -- using the defaults")
        names = DEFAULTS["names"]
    unknown = set(names) - NAME_KEYS
    if unknown:
        problems.append(f"names: {', '.join(sorted(unknown))} not known ({', '.join(sorted(NAME_KEYS))})")
    spec = {k: names[k] for k in ("defaults", "months", "make", "spaced", "columns") if k in names}
    captured = []
    pats = names.get("patterns")
    if pats is not None:
        if not isinstance(pats, dict):
            problems.append("names.patterns: {\"campaign\": [...], \"ad\": [...]}")
            pats = {}
        for src in ("campaign", "adgroup", "ad"):
            spec[src] = pats.get(src) or []
            captured += [g for p in spec[src] if isinstance(p, str)
                         for g in re.findall(r"\(\?P<([A-Za-z_]\w*)>", p)]
    else:
        sep = names.get("split")
        if sep is not None and not isinstance(sep, str):
            problems.append("names.split: a string such as \"_\"")
            sep = None
        for src in ("campaign", "adgroup", "ad"):
            if src not in names:
                spec[src] = []
                continue
            cols = names.get(src)
            pattern = _pieces(cols, sep, problems, src)
            spec[src] = [pattern] if pattern else []
            for c in cols if isinstance(cols, list) and pattern else []:
                if c and c in captured:
                    problems.append(f"names: {c!r} is named twice -- the ad's wins")
                elif c:
                    captured.append(c)
    make = names.get("make") if isinstance(names.get("make"), dict) else {}
    auto = {c: c.replace("_", " ").capitalize() for c in [*captured, *make]}
    auto["status"] = "Status"
    labels = names.get("labels")
    spec["labels"] = {**auto, **(labels if isinstance(labels, dict) else {})}
    if "columns" not in spec:
        parts = {f for t in make.values() if isinstance(t, str) for f in re.findall(r"{(\w+)}", t)}
        months = set(names.get("months") or [])
        # nothing split: the names as they are; else what the settings cut from them
        cut = [c for c in dict.fromkeys([*captured, *make]) if c not in parts and c not in months]
        spec["columns"] = (cut or ["campaign_name", "adgroup_name", "ad_name"]) + ["status"]
    return spec


def load(platform=None, account_id=None):
    """(the options Layout reads, [problems]): settings.json over the defaults,
    then the account's own file over both. A file that is not there is simply
    skipped. Each problem names its file."""
    cfg, problems = copy.deepcopy(DEFAULTS), []
    layers = [("settings.json", PATH)]
    if platform and account_id:
        path = account_path(platform, account_id)
        layers.append((f"accounts/{path.name}", path))
    names_from = fee_from = "settings.json"
    files, layers = layers, []
    for where, path in files:
        mine, found = read(path)
        problems += [f"{where}: {p}" for p in found]
        if path == PATH:                            # its platform sections: layers of their own
            sections = {k: mine.pop(k) for k in PLATFORM_NAMES if k in mine}
            layers.append((where, mine))
            if isinstance(sections.get(platform), dict):
                layers.append((f"settings.json {platform}", sections[platform]))
        else:
            layers.append((where, mine))
    for where, mine in layers:
        if "names" in mine:
            names_from = where
        if "fee" in mine:
            fee_from = where
        own = []
        _apply(cfg, mine, own)
        problems += [f"{where}: {p}" for p in own]
    named = []
    cfg["names"] = {"default": names_spec(cfg["names"], named)}
    problems += [f"{names_from}: {p}" for p in named]
    if cfg["pin"] is None:
        cfg["pin"] = [c for c in cfg["names"]["default"]["columns"] if c != "status"]
    if not 0 < cfg["fee"] < 10:
        problems.append(f"{fee_from}: fee: {cfg['fee']} -- a multiplier above 0 and under 10 "
                        "(1.15 adds 15%); using 1")
        cfg["fee"] = 1.0
    return cfg, problems


def _apply(cfg, mine, problems):
    """One file's options over cfg: metrics merge by id, the rest replace."""
    unknown = set(mine) - set(DEFAULTS)
    if unknown:
        problems.append(f"{', '.join(sorted(unknown))}: not an option "
                        "(every option: adglance +show-config --default --docs)")
    for key, value in mine.items():
        if key not in DEFAULTS:
            continue
        default = DEFAULTS[key]
        if key == "metrics":
            if isinstance(value, dict):
                cfg["metrics"] = {**cfg["metrics"], **value}
            else:
                problems.append("metrics: an object of {id: {name, formula, format}}")
        elif key in ("pin", "show"):
            if value is None or isinstance(value, list):
                cfg[key] = value
            else:
                problems.append(f"{key}: a list, or null for the default")
        elif _same_kind(value, default):
            cfg[key] = value
        else:
            kind = KINDS.get(type(default), type(default).__name__)
            problems.append(f"{key}: {json.dumps(value)} should be {kind} -- "
                            f"using {json.dumps(default)}")


def settled_days(cfg):
    """(days, problem or None): a whole number from 1 to 3650; 30 otherwise."""
    value = cfg.get("settled_days", 30)
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3650:
        return value, None
    return 30, f"settled_days: {value!r} is not a whole number of days from 1 to 3650 -- using 30"


def ensure(platform=None, account_id=None, title=""):
    """The file to edit -- the account's own, else settings.json -- created
    nearly empty if there is none."""
    path = account_path(platform, account_id) if platform and account_id else PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        head = (f"// {title}: options here apply to this account only, over the defaults\n  "
                if platform else "// options here apply to every account\n  ")
        path.write_text("{\n  " + head + "// e.g. \"fee\": 1.15 -- every cost on spend x 1.15 (an agency fee)\n"
                        "  // every option: adglance +show-config --default --docs\n}\n")
    return path


def show(default=False, docs=False):
    """Every option as a settings file: the defaults, or what is in use now."""
    mine, _ = read()
    lines = ["{"]
    for i, (key, value, doc) in enumerate(OPTIONS):
        if not default and key in mine:
            value = mine[key]
        if docs:
            lines += ["", *(f"  // {d}" for d in doc.splitlines())]
        body = json.dumps(value, indent=2, ensure_ascii=False).replace("\n", "\n  ")
        lines.append(f'  "{key}": {body}{"," if i < len(OPTIONS) - 1 else ""}')
    lines.append("}")
    return "\n".join(lines)


def editor():
    return os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"


def _account_file(platform, account_id):
    """(the comment lines on top, the options) of an account's own file."""
    path = account_path(platform, account_id)
    try:
        text = path.read_text()
    except FileNotFoundError:
        ensure(platform, account_id)
        text = path.read_text()
    head = []
    for line in text.splitlines():
        if line.lstrip().startswith("//") or not line.strip():
            head.append(line)
        else:
            break
    inner = [l.strip() for l in text.splitlines() if l.lstrip().startswith("//")
             and l not in head]                   # the starter file keeps its comments inside
    mine = json.loads(_strip_comments(text) or "{}") if text.strip() else {}
    return path, head + inner, mine


def write_option(platform, account_id, key, value):
    """Set (or, with None, remove) one option in an account's own file. The
    comment lines are kept, on top; the options are written out in full.
    Raises ValueError when the file is not JSON (it is left as it is)."""
    path, comments, mine = _account_file(platform, account_id)
    if not isinstance(mine, dict):
        raise ValueError(f"{path.name} is not an object")
    if value is None:
        mine.pop(key, None)
    else:
        mine[key] = value
    body = json.dumps(mine, indent=2, ensure_ascii=False)
    path.write_text("\n".join(comments) + ("\n" if comments else "") + body + "\n")
    return path


def read_option(platform, account_id, key, default=None):
    path, _, mine = _account_file(platform, account_id)
    return mine.get(key, default) if isinstance(mine, dict) else default
