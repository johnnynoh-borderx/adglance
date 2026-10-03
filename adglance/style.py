"""adglance's core, shared by the screen and --print: names, counts, metrics, groups, colours.

Three layers, each knowing nothing of the one above it:

    counts    a platform's view module turns its report into adglance's counts
              (PARTS) plus the campaign and ad names -- no reading of names, no rates
    layout    settings.json: which "_"-separated piece of a name is which column,
              which metrics exist (a formula over the counts and a format), and
              which of them are shown
    drawing   this module: slice, filter, group, total, colour

A total or a group sums the counts first and applies each formula after, so its
CPV is the real one, never an average of its rows' CPVs. Colour carries meaning:
values settings.json names have a hue, a highlight value is yellow, and the cost a
target (settings.json) judges is green at or under it and red over, the other
costs on that row dimmed.
"""
import ast
import functools
import operator
import re
import string
from collections import namedtuple

from rich.text import Text

# Catppuccin Mocha
TEXT, SUBTEXT, OVERLAY = "#cdd6f4", "#a6adc8", "#7f849c"
BLUE, RED, PEACH, GREEN = "#89b4fa", "#f38ba8", "#fab387", "#a6e3a1"
YELLOW, MAUVE, LAVENDER, TEAL, PINK, SKY = "#f9e2af", "#cba6f7", "#b4befe", "#94e2d5", "#f5c2e7", "#89dceb"
SURFACE = "#45475a"
WHITE = "#ffffff"                    # TOTAL's figures: brighter than any row

# a value that means something gets its own hue, wherever it appears
# (settings.json `colors`). Green, red and yellow are kept for verdicts (inside /
# over a gate, a `highlight` value), so no value may wear them.
FLAMINGO = "#f2cdcd"
HUES = {"blue": BLUE, "peach": PEACH, "flamingo": FLAMINGO, "sky": SKY, "teal": TEAL,
        "pink": PINK, "lavender": LAVENDER, "mauve": MAUVE}
VALUE = {}                           # value -> hue, from settings.json
HIGHLIGHT = set()                    # values drawn yellow with a trophy (Winner), from settings.json

# adglance's counts. Every one adds up across ads; reach and average watch time do
# not, so they are not here (watch_time is the total, which does).
PARTS = ["spend", "billed", "impressions", "clicks", "views", "views_2s", "views_6s",
         "views_25", "views_50", "views_75", "views_100", "watch_time", "likes",
         "comments", "shares", "follows", "profile_visits", "engagements"]
FORMATS = ("money", "cost", "pct", "number", "share")
DEFAULT_SHOW = ["billed", "share", "cpm", "cpv", "cpv6", "cpf"]

# key, header, kind, formula. kind: name, count, or one of FORMATS
Column = namedtuple("Column", "key header kind formula")
COUNT = Column("count", "Ads", "count", None)
STATUS = "status"                    # the ad's state now (Live, Off, ...), from the platform
# the names as they are, always there whatever settings split them into: the
# levels (1 campaigns, 2 ad groups, 3 ads) group by these
RAW = {"campaign_name": "Campaign", "adgroup_name": "Ad group", "ad_name": "Ad"}
SOURCES = {"campaign": "campaign_name", "adgroup": "adgroup_name", "ad": "ad_name"}
DATE = "date"                        # the day column Daily adds (shown as Date)
ALL = "*"                            # the group that holds everything


# ---- formulas ---------------------------------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv}


@functools.lru_cache(maxsize=None)
def compile_formula(text):
    """Parse a formula over PARTS (+ - * / and brackets, numbers), or raise
    ValueError saying what is wrong. Arithmetic only -- this is not eval."""
    try:
        tree = ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        raise ValueError(f"cannot read {text!r}")

    def check(node):
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            check(node.left), check(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            check(node.operand)
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            pass
        elif isinstance(node, ast.Name):
            if node.id not in PARTS:
                raise ValueError(f"{node.id!r} is not a count -- the counts are {', '.join(PARTS)}")
        else:
            raise ValueError(f"{text!r}: only counts, numbers, + - * / and brackets")
    check(tree)
    return tree


def evaluate(tree, counts):
    """The formula's value for these counts; None when it divides by zero or
    by less: a count TikTok has corrected below zero (follows -1 today) makes a
    cost per follow meaningless, not negative."""
    def run(node):
        if isinstance(node, ast.BinOp):
            right = run(node.right)
            if isinstance(node.op, ast.Div) and right <= 0:
                raise ZeroDivisionError
            return _OPS[type(node.op)](run(node.left), right)
        if isinstance(node, ast.UnaryOp):
            return -run(node.operand)
        if isinstance(node, ast.Constant):
            return float(node.value)
        return counts.get(node.id) or 0.0
    try:
        return run(tree)
    except ZeroDivisionError:
        return None


# ---- layout: what settings.json asks for ----------------------------------------
# a target: "objective=VV cpv6 <= 20" (the column may be a label: Obj=VV)
_TARGET = re.compile(r"^\s*([A-Za-z_][\w ]*?)\s*=\s*(.+?)\s+([A-Za-z_]\w*)\s*(?:<=|≤)\s*([\d.,]+)\s*$")
_MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
# where a run of letters becomes a new word: Spring|Teaser, Big|PPV, 7|Days
_WORD_BREAK = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[0-9])(?=[A-Za-z])")


class Layout:
    """settings.json read for one platform: how names become columns, the metric
    columns shown, the group choices and the starting sort.

    Nothing in the file stops adglance: a part it cannot use is skipped (or left
    at its default) and said in .problems, which the screen shows in a banner
    until the file is fixed."""

    def __init__(self, cfg, platform, problems=()):
        self.problems = list(problems)
        names = cfg.get("names", {})
        spec = names.get(platform) or names.get("default") or {}
        if not spec:
            self.problems.append("names: nothing to read names with -- they are not split")
        self.patterns = {src: self._patterns(spec.get(src), src) for src in SOURCES}
        captured = {g for pats in self.patterns.values() for p in pats for g in p.groupindex}
        made = dict(spec.get("make") or {})
        self.make = {}
        for col, template in made.items():
            try:
                fields = {f for _, f, _, _ in string.Formatter().parse(template) if f}
                template.format_map({f: "x" for f in fields})   # a bad spec fails here, not mid-draw
            except (ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
                self.problems.append(f"names.make.{col}: {e}")
                continue
            self.make[col] = (template, fields)
        # every value a name gives is text: a number in defaults would trip
        # the word splitting (spaced) mid-draw
        self.defaults = {k: str(v) for k, v in dict(spec.get("defaults") or {}).items()}
        self.months = set(spec.get("months") or [])
        self.spaced = set(spec.get("spaced") or [])
        # status comes from the platform, the raw names from the report, not a pattern
        known = captured | set(self.make) | {STATUS} | set(RAW)
        wanted = spec.get("columns") or sorted(known)
        self.names = []
        for c in wanted:
            if c not in known:
                self.problems.append(f"names.columns: {c!r} is not captured or made")
            elif c in self.names:                    # a column twice would crash the table
                self.problems.append(f"names.columns: {c!r} is listed twice -- shown once")
            else:
                self.names.append(c)
        # every other column a name gives can be shown too (H); the pieces only
        # used to build one (Month, Year) are not columns of their own
        parts = {f for _, fields in self.make.values() for f in fields}
        self.shown_names = list(self.names)
        self.names += sorted(c for c in known - set(self.names) - parts - self.months)
        # the short header each column shows under; its name stays the mart's
        raw = dict(RAW, adgroup_name="Ad set" if platform == "meta" else "Ad group")
        self.labels = {DATE: "Date", **{c: c for c in self.names}, **raw, **(spec.get("labels") or {})}
        # the columns cut from the ad name, drawn bright -- what the row is about
        self.from_ad = {g for p in self.patterns["ad"] for g in p.groupindex} | {"ad_name"}
        self._cut = functools.lru_cache(maxsize=None)(self._cut_uncached)

        metrics = cfg.get("metrics", {})
        if not isinstance(metrics, dict):
            self.problems.append("[metrics] must be a table of metrics")
            metrics = {}
        show = cfg.get("show", [])
        if not isinstance(show, list):
            # a string would be read letter by letter: say so, show the defaults
            self.problems.append(f"show: {show!r} must be a list, e.g. [\"billed\", \"cpm\"] -- "
                                 "showing the defaults")
            show = DEFAULT_SHOW
        # the whole catalogue is available (H shows any of it); show is what is
        # visible by default, and comes first, in its order
        order, seen = [], set()
        for mid in list(show) + [m for m in metrics if m not in show]:
            if mid in seen:
                if mid in show:                      # a column twice would crash the table
                    self.problems.append(f"show: {mid!r} is listed twice -- shown once")
                continue
            seen.add(mid)
            order.append(mid)
        self.catalog = []
        for mid in order:
            m = metrics.get(mid)
            listed = mid in show
            if not isinstance(m, dict):
                if listed:
                    self.problems.append(f"show: {mid!r} is not in [metrics]")
                continue
            if mid in PARTS and (m.get("formula") or "").replace(" ", "") != mid:
                # its value would be written over the count of the same name and
                # fed back into every formula on the next redraw
                self.problems.append(f"metrics.{mid}: an id that is a count must be that count "
                                     f"(formula = \"{mid}\"); give this one another id")
                continue
            kind = m.get("format", "number")
            formula = m.get("formula")
            try:
                if kind not in FORMATS:
                    raise ValueError(f"format {kind!r} -- use one of {', '.join(FORMATS)}")
                if kind != "share":
                    if not formula:
                        raise ValueError("no formula")
                    compile_formula(formula)
            except ValueError as e:
                self.problems.append(f"metrics.{mid}: {e}")
                continue
            self.catalog.append(Column(mid, m.get("name", mid), kind,
                                       None if kind == "share" else formula))
        # what is drawn: these keys (n:name for a name column, the id for a
        # metric); the screen replaces it with what h / H picked
        self.default_visible = ({f"n:{c}" for c in self.shown_names}
                                | {c.key for c in self.catalog if c.key in show})
        self.visible = set(self.default_visible)
        sort = cfg.get("sort", "-billed")
        key = self.key_of(sort.lstrip("-"))
        if not key:
            self.problems.append(f"sort: {sort!r} is not a column")
        self.sort = (key or "billed", sort.startswith("-") if key else True)
        ICONS["on"] = bool(cfg.get("icons", True))
        VALUE.clear()
        colors = cfg.get("colors", {})
        for value, hue in (colors.items() if isinstance(colors, dict) else []):
            if hue in HUES:
                VALUE[str(value)] = HUES[hue]
            else:
                self.problems.append(f"colors.{value}: {hue!r} -- use one of {', '.join(HUES)}")
        self.targets = self._targets(cfg.get("targets", []))
        self.fee = float(cfg.get("fee", 1))           # billed = spend x fee, for every cost
        HIGHLIGHT.clear()
        highlight = cfg.get("highlight", [])
        HIGHLIGHT.update(str(v) for v in (highlight if isinstance(highlight, list) else []))
        pin = cfg.get("pin", [])
        if not isinstance(pin, list):
            self.problems.append(f"pin: {pin!r} must be a list of name columns, e.g. [\"content\"]")
            pin = []
        self.pin = []
        for word in pin:
            col = self.column(str(word))
            if col in self.names and col not in self.pin:
                self.pin.append(col)
            else:
                self.problems.append(f"pin: {word!r} is not a shown name column")

    def _targets(self, given):
        """["objective=VV cpv6 <= 20", ...] -> [(column, value, metric id, limit)]."""
        out, ids = [], {c.key for c in self.catalog}
        for text in given if isinstance(given, list) else []:
            m = _TARGET.match(str(text))
            col = self.column(m.group(1)) if m else None
            if not m:
                self.problems.append(f"targets: {text!r} -- write \"column=value metric <= limit\"")
            elif col not in self.names:
                self.problems.append(f"targets: {m.group(1)!r} is not a name column")
            elif m.group(3) not in ids:
                self.problems.append(f"targets: {m.group(3)!r} is not a metric")
            else:
                out.append((col, m.group(2).strip(), m.group(3), float(m.group(4).replace(",", ""))))
        return out

    @property
    def limits(self):
        """{metric id: limit} for the headers -- only where every target on a
        metric shares one limit, so a header never states the wrong one."""
        seen = {}
        for _, _, metric, limit in self.targets:
            seen.setdefault(metric, set()).add(limit)
        return {m: lims.pop() for m, lims in seen.items() if len(lims) == 1}

    def target(self, names):
        """(metric id, limit) of the first target this row's names meet."""
        for col, value, metric, limit in self.targets:
            if str(names.get(col, "")).casefold() == value.casefold():
                return metric, limit
        return None

    def _patterns(self, given, src):
        if isinstance(given, str):
            given = [given]
        out = []
        for i, text in enumerate(given or []):
            try:
                out.append(re.compile(text))
            except (re.error, TypeError) as e:
                self.problems.append(f"names.{src}[{i + 1}]: {e}")
        return out

    def _cut_uncached(self, src, name):
        """The columns one name gives: the first pattern that matches."""
        for pattern in self.patterns[src]:
            m = pattern.search(name)
            if m:
                out = {}
                for col, v in m.groupdict().items():
                    v = v or self.defaults.get(col, "")
                    if col in self.months:
                        v = _MONTHS.get(v, v)
                    if col in self.spaced:
                        v = _WORD_BREAK.sub(" ", v)
                    out[col] = v
                return out
        return {}

    def cut(self, campaign, ad, adgroup=""):
        """{column: value} for one row, from its campaign, ad group and ad names
        -- what the patterns cut from them, and the names as they are."""
        got = {**self._cut("campaign", campaign), **self._cut("adgroup", adgroup),
               **self._cut("ad", ad), "campaign_name": campaign, "adgroup_name": adgroup,
               "ad_name": ad}
        for col, (template, fields) in self.make.items():
            if all(got.get(f) for f in fields):       # else keep what a pattern gave, if any
                got[col] = template.format_map(got)
        return {c: got.get(c, "") for c in self.names}

    def parse_group(self, text):
        """'Type+Obj' -> ("type", "objective") in column order, 'All' -> (ALL,),
        '' -> (); None when a word is not a column."""
        words = [self.column(w) for w in (text or "").split("+") if w.strip()]
        if words == [ALL]:
            return (ALL,)
        if any(w not in self.names for w in words):
            return None
        return self.ordered_group(words)

    def column(self, word):
        """A column by its name or its label, any case: Obj, obj, objective -> objective."""
        word = word.strip()
        if word.lower() == "all":
            return ALL
        for col, label in self.labels.items():
            if word.lower() in (col.lower(), label.lower()):
                return col
        return word

    def label(self, col):
        return "All" if col == ALL else self.labels.get(col, col)

    def ordered_group(self, cols):
        """The columns of a group in the table's order, whatever order they were picked."""
        if ALL in cols:
            return (ALL,)
        return tuple(c for c in self.names if c in cols)

    @property
    def metrics(self):
        """The metric columns drawn, in the catalogue's order."""
        return [c for c in self.catalog if c.key in self.visible]

    def every_column(self):
        """[(key, label)] of every column there is, names then metrics -- for H."""
        return ([(f"n:{c}", self.label(c)) for c in self.names]
                + [(c.key, c.header) for c in self.catalog])

    def key_of(self, word):
        """A sort word -> a column key: a metric id or header, or a name column."""
        for c in self.catalog:
            if word in (c.key, c.header):
                return c.key
        col = self.column(word)
        if col in self.names or col == DATE:
            return f"n:{col}"
        return "count" if word in ("count", "Ads") else None

    def columns(self, recs=(), by=(), daily=False):
        """The columns to draw: Date when daily, the name columns -- while
        grouped, only those that hold one value in every group, then Ads -- and
        the metrics."""
        # a level's names (Campaign, Ad group) lead: they are what the rows are
        lead = [c for c in RAW if c in by]
        names = [col for col in ([DATE] if daily else []) + lead + [c for c in self.names if c not in lead]
                 if (col == DATE or f"n:{col}" in self.visible or col in by)
                 and (not by or col in by or not any(col in r.get("mixed", ()) for r in recs))]
        cols = [Column(f"n:{col}", self.label(col), "name", None) for col in names]
        return cols + ([COUNT] if by else []) + self.metrics


LOADING = "\u2026"
SPIN = ["\u280b"]                    # the frame the Status cells draw; the screen turns it                   # a status not here yet: the cell shows the spinner


def prepare(raw, layout, statuses=None):
    """A platform's rows -> records: the counts, the name columns, and the gate
    its target judges. statuses: {ad id: (word, raw)}, or None while
    they are still on their way (the Status cells show loading)."""
    out = []
    for r in raw:
        campaign, ad = r.get("campaign_name", ""), r.get("ad_name", "")
        names = layout.cut(campaign, ad, r.get("adgroup_name", ""))
        raw_status = None
        if STATUS in names:
            if statuses is None:
                names[STATUS] = LOADING
            else:
                raw_status = statuses.get(r.get("ad_id"))     # (word, the platform's own)
                names[STATUS] = raw_status[0] if raw_status else "–"
        if r.get("day"):                     # a Daily row: its day is a column like any name
            names = {DATE: r["day"], **names}
        gate = layout.target(names)
        rec = {p: r.get(p) or 0.0 for p in PARTS}
        rec.update(names=names, campaign_name=campaign, adgroup_name=r.get("adgroup_name", ""), ad_name=ad,
                   judged=gate[0] if gate else None, limit=gate[1] if gate else None,
                   ids=frozenset([r.get("ad_id") or ad]), n=1, raw_status=raw_status)
        out.append(rec)
    return out


# ---- filter, group, total ----------------------------------------------------
def matches(r, query):
    """Keep a row when every plain word is in it and no -word is: "vv -ca" is
    VV outside Canada. Up to two letters a word must be a whole name ("-ca"
    drops CA, not "Can Yall Tell Me"); from three it may sit anywhere in one
    ("winner" finds September2026Winner)."""
    tokens = [str(v).lower() for v in r["names"].values() if v]

    def hit(word):
        return any(t == word or (len(word) >= 3 and word in t) for t in tokens)

    for word in query.lower().split():
        if word.startswith("-") and len(word) > 1:
            if hit(word[1:]):
                return False
        elif not hit(word):
            return False
    return True


def combine(recs, label=None):
    """One record for a set of records: counts summed; a name all of them share
    kept, the others blank and listed in "mixed". Judged only when they share
    one yardstick."""
    rec = {p: sum(r[p] for r in recs) for p in PARTS}
    names, mixed = {}, set()
    for col in (recs[0]["names"] if recs else {}):
        values = {r["names"].get(col) for r in recs}
        if len(values) == 1:
            names[col] = values.pop()
        else:
            names[col] = ""
            mixed.add(col)
    judged = {(r.get("judged"), r.get("limit")) for r in recs}
    rec["judged"], rec["limit"] = judged.pop() if len(judged) == 1 else (None, None)
    # Ads counts distinct ads, not rows: with Daily, one ad on five days is one ad
    ids = frozenset().union(*(r.get("ids", ()) for r in recs))
    rec.update(names=names, mixed=mixed, ids=ids, n=len(ids), members=recs)
    if label:
        rec["total"] = label
    return rec


def grouping(by, daily):
    """What the rows are merged by: the group's columns, and the day when Daily
    is on. All alone merges everything; All with Daily is one row a day."""
    if not by:
        return ()
    keys = tuple(k for k in by if k != ALL) + ((DATE,) if daily else ())
    return keys or (ALL,)


def group(recs, by):
    """Records merged by the name columns in `by`; with none, as they are.
    ALL puts every record in one group."""
    if not by:
        return recs
    buckets = {}
    for r in recs:
        buckets.setdefault(tuple(r["names"].get(k) for k in by if k != ALL), []).append(r)
    return [combine(members) for members in buckets.values()]


def derive(rec, metrics):
    """Fill each metric from the record's counts, and whether its judged cost
    is inside the gate. It writes into rec: pass a copy (dict(r)) of anything
    whose counts must stay as they are -- every caller does."""
    for c in metrics:
        if c.formula:
            rec[c.key] = evaluate(compile_formula(c.formula), rec)
    v = rec.get(rec.get("judged")) if rec.get("judged") else None
    rec["passed"] = None if v is None or rec.get("limit") is None else v <= rec["limit"]
    return rec


def with_share(recs, total_billed):
    """Each record's part of the spend shown, and its bar: the largest record
    fills the bar, so the bars compare rows rather than all being slivers."""
    for r in recs:
        r["share"] = r["billed"] / total_billed if total_billed else None
    top = max((r["share"] or 0 for r in recs), default=0)
    for r in recs:
        r["bar"] = (r["share"] or 0) / top if top else 0
    return recs


def value(r, key):
    if key.startswith("n:"):
        return r["names"].get(key[2:])
    return r.get("n") if key == "count" else r.get(key)   # Ads is the number of ads, n


def ordered(recs, key, reverse):
    """Sorted by one column. Names A-Z with blanks last; numbers with a missing
    value (no 6s views, no follows) last either way."""
    if key.startswith("n:"):
        return sorted(recs, key=lambda r: ((value(r, key) or "") == "", (value(r, key) or "").lower()),
                      reverse=reverse)
    sign = -1 if reverse else 1
    return sorted(recs, key=lambda r: (value(r, key) is None, (value(r, key) or 0) * sign))


# ---- icons --------------------------------------------------------------------
# Nerd Font glyphs (Ghostty carries them built in; over SSH they are drawn by
# the terminal on this side). `icons = false` in settings.json turns them off and
# leaves plain words. One cell wide each; icon() adds the space after.
GLYPHS = {
    "source": "\U000f01bc",    # 󰆼 database
    "period": "\U000f00f0",    # 󰃰 calendar range
    "filter": "\uf0b0",        #  filter
    "group": "\uf247",         #  object group
    "calendar": "\U000f00ed",  # 󰃭 calendar
    "daily": "\U000f00f6",     # 󰃶 calendar today
    "winner": "\uf091",        #  trophy
    "error": "\uf057",         #  times circle
    "warning": "\uf071",       #  warning
    "clock": "\uf017",         #  clock
    "lock": "\uf023",          #  lock: settled days
    "copy": "\uf0c5",          #  copy
    "id": "\uf292",            #  hashtag
    "campaign": "\uf0a1",      #  bullhorn
    "link": "\uf08e",          #  external link
    "row": "\uf0ce",           #  table
    "ads": "\uf03a",           #  list
    "keys": "\uf11c",          #  keyboard
    "pin": "\U000f0403",       # 󰐃 pin: a pinned column
    # Status: told apart by shape, never by colour (green / red / yellow are verdicts)
    "st_live": "",       #  play
    "st_off": "",        #  pause
    "st_campoff": "",    #  stop
    "st_review": "",     #  hourglass
    "st_rejected": "",   #  times
    "st_budget": "",     #  dollar
    "st_scheduled": "",  #  clock
    "st_ended": "",      #  chequered flag
}
STATUS_GLYPH = {"Live": "st_live", "Off": "st_off", "Camp off": "st_campoff",
                "Review": "st_review", "Rejected": "st_rejected", "No budget": "st_budget",
                "Scheduled": "st_scheduled", "Ended": "st_ended"}
ICONS = {"on": True}


def icon(name):
    """The glyph and a space, or nothing when icons are off."""
    return f"{GLYPHS[name]} " if ICONS["on"] and name in GLYPHS else ""


# ---- drawing ----------------------------------------------------------------
SYMBOL = {"KRW": "₩", "USD": "$", "CAD": "C$", "JPY": "¥", "EUR": "€", "GBP": "£"}
NAME_WIDTH, CONTENT_WIDTH = 18, 28   # name cells past this end in …
# one chevron family for every way through: right = in / next, left = back /
# previous. Font Awesome's (Nerd Font); ❯ ❮ when icons are off
CHEVRONS = {True: ("\uf054", "\u276f"), False: ("\uf053", "\u276e")}


def chevron(right=True):
    """, the Nerd Font chevron (❯ / ❮ with icons off)."""
    return CHEVRONS[right][0 if ICONS["on"] else 1]

SHARE_WIDTH = 10                     # "█████ 100%"


def header_label(col, limits, currency=""):
    """A money column shows its currency, a judged cost its gate:
    "Spend×1.15 ₩", "6s CPV ₩ ≤20", "CPF ₩ ≤1,700"."""
    header = col.header
    if col.kind in ("money", "cost") and currency:
        header = f"{header} {SYMBOL.get(currency, currency)}"
    return f"{header} ≤{limits[col.key]:,.0f}" if col.key in limits else header


def bar(fill, width=5):
    """A bar in eighths of a cell: 1.0 is full, 0.48 is '██▍  '."""
    eighths = round(max(0.0, min(1.0, fill)) * width * 8)
    full, part = divmod(eighths, 8)
    return ("█" * full + (" ▏▎▍▌▋▊▉"[part] if part else "")).ljust(width)


def number(v, kind):
    if kind == "pct":
        return f"{v * 100:,.2f}%"
    if kind == "money":
        return f"{v:,.0f}"
    if kind == "cost":                       # a decimal only where it matters: 25.7, not 51,201.7
        return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.1f}"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"


def _name_style(v, col_index, from_ad):
    """A hue only where settings.json gives one (colors, highlight), the columns
    cut from the ad name in bright text; everything else quiet."""
    if v in VALUE:
        return f"bold {VALUE[v]}"
    if v in HIGHLIGHT:
        return f"bold {YELLOW}"
    return f"bold {TEXT}" if from_ad else SUBTEXT


def cells(cols, r, layout):
    """One row's cells, coloured by what they mean. A TOTAL row puts its label
    in the first column and is drawn brighter."""
    total = r.get("total")
    from_ad = layout.from_ad
    out = []
    for i, c in enumerate(cols):
        if c.kind == "name":
            name = c.key[2:]
            v = r["names"].get(name, "")
            if total:
                # the header is mauve on dark; TOTAL is bright white, labelled Σ
                out.append(Text(f"Σ {total}" if i == 0 else "", style=f"bold {WHITE}"))
            elif not v:
                out.append(Text("·", style=SURFACE))
            elif name == STATUS:
                if v == LOADING:
                    out.append(Text(SPIN[0], style=MAUVE))
                else:
                    # green, red and yellow are verdicts: Live is just bright text
                    style = {"Live": f"bold {TEXT}"}.get(v, SUBTEXT)
                    glyph = icon(STATUS_GLYPH[v]) if v in STATUS_GLYPH else ""
                    out.append(Text(f"{glyph}{v}", style=style))
            else:
                # a long project name would widen its whole column; the detail
                # line under the table has it in full
                limit = CONTENT_WIDTH if name in from_ad or name in RAW else NAME_WIDTH
                shown = v if len(v) <= limit else v[:limit - 1] + "…"
                shown = f"{icon('winner')}{shown}" if v in HIGHLIGHT else shown
                cell = Text(shown, style=_name_style(v, i, name in from_ad))
                if r.get("members") and i == 0:          # a group row (TOTAL returned above)
                    cell = Text.assemble((f"{chevron()} ", f"bold {MAUVE}"), cell)   # Enter opens it
                out.append(cell)
        elif c.kind == "count":
            out.append(Text(f"{r.get('n', 1):,}", style=SUBTEXT, justify="right"))
        elif c.kind == "share":
            v = r.get("share")
            out.append(Text("") if v is None else Text.assemble(
                (bar(1.0 if total else r.get("bar", v)), MAUVE if total else LAVENDER), " ",
                (" <1%" if 0 < v < 0.005 else f"{v * 100:>3.0f}%", SUBTEXT)))
        else:
            v = r.get(c.key)
            if v is None:
                out.append(Text("–", style=OVERLAY, justify="right"))
                continue
            if c.key == "billed":
                style = f"bold {WHITE}" if total else f"bold {TEXT}"
            elif r.get("judged") == c.key and r.get("passed") is not None:
                style = f"bold {GREEN}" if r["passed"] else f"bold {RED}"
            elif c.kind == "cost" and r.get("judged") and not total:
                style = SUBTEXT          # another cost: not this row's yardstick, but readable
            else:
                style = f"bold {WHITE}" if total else TEXT
            out.append(Text(number(v, c.kind), style=style, justify="right"))
    if total and cols and cols[0].kind != "name":
        out[0] = Text(total, style=f"bold {MAUVE}")
    return out


def detail(r, currency="", col=None, labels=None):
    """The two lines under the table. First what the row is: an ad's content
    and campaign; a group or TOTAL summed up (AO · VV · 12 ads), not its ad
    names. Then the cursor's cell explained: a metric as its formula with this
    row's counts in it -- Spend as raw spend × the fee -- and, on the judged
    cost, its verdict in words (the cursor's colour hides the green or red);
    a name column in full."""
    labels = labels or {}
    ads = f"{r.get('n', 1)} ad{'s' if r.get('n', 1) != 1 else ''}"
    if r.get("total"):
        head = f"Σ everything shown  ·  {ads}"
    elif r.get("members"):
        shared = [v for k, v in r["names"].items() if v and k not in r.get("mixed", ())]
        head = "  ·  ".join(shared + [ads])
    else:
        content = r["names"].get("content") or r.get("ad_name", "")
        head = f"{content}  ·  {r.get('campaign_name', '')}"
    sym = SYMBOL.get(currency, currency)
    line = ""
    if col is not None and col.key == "billed" and r.get("spend"):
        line = (f"{col.header} = raw spend {sym}{r['spend']:,.0f} × {r['billed'] / r['spend']:.2f}"
                f"  =  {sym}{r['billed']:,.0f}")
    elif col is not None and col.formula:
        def value(m):
            v = r.get(m.group(0))
            return f"{v:,.0f}" if isinstance(v, (int, float)) else m.group(0)
        filled = re.sub(r"[a-z_][a-z0-9_]*", value, col.formula)
        result = r.get(col.key)
        shown = "–" if result is None else number(result, col.kind)
        line = f"{col.header} = {col.formula}  =  {filled}  =  {shown}"
        if r.get("judged") == col.key and r.get("limit") is not None and result is not None:
            verdict = "within" if result <= r["limit"] else "over"
            line += f"  ·  target ≤{r['limit']:,.0f}: {verdict}"
    elif col is not None and col.kind == "share" and r.get("share") is not None:
        line = f"Share = billed / billed shown  =  {r['share'] * 100:.1f}%"
    elif col is not None and col.kind == "name":
        key = col.key[2:]
        line = f"{labels.get(key, key)}: {r['names'].get(key) or '–'}"
    elif col is not None and col.kind == "count":
        line = f"Ads: {r.get('n', 1):,} in this row"
    return f"{head}\n{line}"
