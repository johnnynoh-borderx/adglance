"""adglance -- every ad's spend at a glance, in the terminal. Read-only.

    adglance                  the screen, on the last period you looked at
    adglance setup            add, check and edit ad accounts (key, label)
    adglance +show-config --default --docs   every option, with what it does
    adglance +validate-config                check settings.json
    adglance yesterday | mtd | 14d     yesterday, this month, the last N days
    adglance 09-25            one day (this year; 2026-09-25 works too)
    adglance 09-01 09-30      a range, both days included
    adglance mtd --watch 60   redraw every minute
    adglance 7d --print       just print the table (also when piped)
    adglance 7d -s "us vv"    only rows containing every word
    adglance mtd --sort cpv6  by a column; -cpv6 for largest first
    adglance 7d --group Geo   merge rows by name columns

Accounts and tokens: ~/.config/adglance/accounts.json (600), written by
`adglance setup`. Everything else -- name columns, metrics, targets, colours --
is ~/.config/adglance/settings.json, empty until you change something (`,` on
the screen opens it in $EDITOR).

It cannot change anything: every request it makes is a GET.
"""
import os

# Over SSH, COLORTERM does not travel (macOS sshd accepts only LANG and LC_*),
# so a Ghostty session reaches here as TERM=xterm-ghostty alone and rich and
# textual fall back to 16 colours: Mocha's dark base becomes ANSI black, which
# Catppuccin paints #45475a -- a grey screen. These terminals all do truecolor.
if not os.environ.get("COLORTERM") and any(
        t in os.environ.get("TERM", "") for t in ("ghostty", "kitty", "wezterm", "alacritty", "foot")):
    os.environ["COLORTERM"] = "truecolor"

import argparse  # noqa: E402
import datetime as dt  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import logging.handlers  # noqa: E402
import pathlib  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from . import accounts, settings, store as daystore  # noqa: E402
from .platforms import PLATFORMS  # noqa: E402
from .platforms.base import PlatformError  # noqa: E402
from .style import (DATE, MAUVE, STATUS, OVERLAY, SUBTEXT, Layout, cells, combine,  # noqa: E402
                          derive, group, grouping, header_label, matches, ordered, prepare,
                          with_share)

# What adglance remembers between runs: the last platform and account picked and
# the periods typed. It is a note on this machine, not the ad account -- the
# read-only rule is about the account and .env, which stay untouched.
STATE = pathlib.Path.home() / ".config" / "adglance" / "state.json"
# Each ad's counts per day, fetched once and kept (daystore); the logs sit
# beside it. Notes on this machine, like state.json.
CACHE = daystore.CACHE
# an unsettled day fetched longer ago than this is fetched again when shown
STALE = 600
# What went wrong, in full, for when the one line on screen is not enough.
LOG = CACHE / "adglance.log"
log = logging.getLogger("adglance")


def start_log():
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(LOG, maxBytes=256_000, backupCount=1)
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def layout(src):
    """The settings for one account -- the defaults, settings.json, then the
    account's own file -- with their problems in .problems. A value of the wrong kind never stops
    adglance: that option runs on its default and says why."""
    platform, account = (src["platform"], src.get("account")) if isinstance(src, dict) else (src, None)
    cfg, problems = settings.load(platform, account)
    days, problem = settings.settled_days(cfg)
    problems += [problem] if problem else []
    own = settings.account_path(platform, account) if account else None
    where = f"accounts/{own.name}" if own and own.exists() else "settings.json"
    try:
        lay = Layout(cfg, platform, problems)
        # what Layout itself found (targets, metrics, colours, pins): the
        # account's own file, the one these options most likely came from
        lay.problems[len(problems):] = [f"{where}: {p}" for p in lay.problems[len(problems):]]
    except Exception as e:                         # a shape the parser did not expect
        log.exception("settings.json")
        defaults = settings.names_spec(settings.DEFAULTS["names"], [])
        lay = Layout({**settings.DEFAULTS, "names": {"default": defaults}, "pin": []}, platform,
                     problems + [f"{where}: cannot use it ({e}) -- running on the defaults"])
    lay.settled_days = days
    return lay


def sources():
    """Every account in accounts.json, as dicts the screen and the printer
    share; problems with the file ride along as the second value."""
    cfg, _ = settings.load()
    settled, _ = settings.settled_days(cfg)
    accs, problems = accounts.load()
    if accounts.loose():
        problems.append(f"{accounts.PATH} can be read by others: chmod 600 it")
    out = []
    for acc in accs:
        module = PLATFORMS.get(acc["platform"])
        if module is None:
            problems.append(f"accounts.json: unknown platform {acc['platform']!r} "
                            f"(one of {', '.join(PLATFORMS)}) -- skipped")
            continue
        out.append({"key": f"{module.NAME}:{acc['id']}", "platform": module.NAME,
                    "platform_label": module.TITLE, "account": acc["id"],
                    "account_label": acc["label"], "label": f"{module.TITLE} · {acc['label']}",
                    "currency": acc.get("currency", ""),
                    "timezone": acc.get("timezone") or None,
                    "store": daystore.Store(module, acc, settled, acc.get("timezone") or None),
                    "link": (lambda m, a: lambda c, n, s, e: m.ad_link(c, n, s, e, a))(module, acc)})
    if out:
        daystore.set_zone(out[0]["timezone"])   # its days, not this machine's
    return out, problems


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=1))
    except OSError:
        pass                                       # remembering is a convenience, never an error


def _day(text, today):
    """09-25 (this year) or 2026-09-25."""
    try:
        if len(text) <= 5:
            return dt.datetime.strptime(f"{today.year}-{text}", "%Y-%m-%d").date()
        return dt.date.fromisoformat(text)
    except ValueError:
        raise SystemExit(f"adglance: {text!r} is not a date -- use 09-25 or 2026-09-25")


def window(words):
    """The window the arguments name, as (start, end) ISO dates.

    none          today          yesterday / mtd / lm (last month)
    Nd            the last N days, today included (7d, 14d, 30d)
    DAY           that day                      (09-25 or 2026-09-25)
    DAY DAY       from the first to the second
    """
    today = daystore.today()
    if not words or words == ["today"]:
        return str(today), str(today)
    if len(words) == 1:
        w = words[0]
        if w == "yesterday":
            day = today - dt.timedelta(days=1)
            return str(day), str(day)
        if w == "mtd":
            return str(today.replace(day=1)), str(today)
        if w in ("lm", "lastmonth"):
            end = today.replace(day=1) - dt.timedelta(days=1)
            return str(end.replace(day=1)), str(end)
        if w.endswith("d") and w[:-1].isdigit() and int(w[:-1]) > 0:
            return str(today - dt.timedelta(days=int(w[:-1]) - 1)), str(today)
        day = _day(w, today)
        return str(day), str(day)
    if len(words) == 2:
        start, end = _day(words[0], today), _day(words[1], today)
        if start > end:
            raise SystemExit(f"adglance: {start} is after {end}")
        return str(start), str(end)
    raise SystemExit("adglance: give a period, one day, or two days (start end)")


def table(words, console, layout, src, search="", sort=None, by=(), daily=False, stale=STALE):
    start, end = window(words)
    span = start if start == end else f"{start} .. {end}"
    # the spinner shows while the one request is out, and leaves no trace
    store = src["store"]
    need = store.missing(start, end, recent_since=time.time() - stale)
    if need:
        # the spinner shows while the days still missing are fetched, and leaves no trace
        with console.status(f"[{MAUVE}]fetching {len(need)} day{'s' if len(need) != 1 else ''} "
                            f"of {src['platform_label']}…", spinner="dots", spinner_style=MAUVE):
            store.fetch(need)
    statuses = None
    if STATUS in layout.names:                     # the ads' state now: a second, slower call
        try:
            with console.status(f"[{MAUVE}]fetching ad statuses…", spinner="dots", spinner_style=MAUVE):
                store.fetch_statuses()
            statuses = store.statuses() or {}
        except PlatformError as e:
            Console(stderr=True).print(f"[yellow]!! statuses: {e}[/]")
            statuses = {}
    recs = [r for r in prepare(store.rows(start, end, daily, layout.fee), layout, statuses)
            if matches(r, search)]
    # every derive on a copy: a record's counts are never written over
    total = derive(combine(recs, label="TOTAL"), layout.metrics)
    total["share"] = 1.0 if recs else None
    by = grouping(by, daily)
    shown = with_share([derive(dict(r), layout.metrics) for r in group(recs, by)], total["billed"])
    # [(key, reverse), ...]: the first decides, the rest break ties (sorted last
    # first, each sort stable, so the first wins)
    for key, reverse in reversed(sort or [(f"n:{DATE}", False) if daily else layout.sort]):
        shown = ordered(shown, "billed" if key == "share" else key, reverse)
    cols = layout.columns(shown, by, daily)
    words_shown = f"  ·  '{search}'" if search.split() else ""
    merged = f"  ·  by {' + '.join(k for k in by if k != '*') or 'all'}" if by else ""
    # the time the numbers were fetched, not the time printed: a cached answer says so
    fetched, _ = daystore.freshness(store, start, end)
    t = Table(title=f"{span}{words_shown}{merged}  ·  {fetched or 'not fetched'}",
              title_justify="left", title_style=SUBTEXT, header_style=f"bold {MAUVE}",
              border_style=OVERLAY, pad_edge=False)
    gate_limits, currency = layout.limits, src["currency"]
    for col in cols:
        label = header_label(col, gate_limits, currency)
        t.add_column(label, justify="left" if col.kind == "name" else "right", no_wrap=True,
                     min_width=len(label))
    for r in shown:
        t.add_row(*cells(cols, r, layout))
    t.add_section()
    t.add_row(*cells(cols, total, layout))
    return t


def main(argv=None):
    p = argparse.ArgumentParser(prog="adglance", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("when", nargs="*", metavar="WHEN",
                   help="today | yesterday | mtd | 7d | 09-25 | 09-01 09-30")
    p.add_argument("-s", "--search", default="", metavar="WORDS",
                   help='keep rows containing every word, drop any with a -word: -s "vv -ca"')
    p.add_argument("--sort", metavar="COLUMN",
                   help="a metric id or a name column, -id for largest first; several joined by , break ties")
    p.add_argument("--group", metavar="COLS", help="merge rows by name columns: Geo, Type+Obj, ... or All")
    p.add_argument("--daily", action="store_true", help="one row per day (with --group: per group per day)")
    p.add_argument("--watch", type=int, default=0, metavar="SECONDS")
    p.add_argument("--print", action="store_true",
                   help="print the table and exit (the default when output is not a terminal)")
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["setup"]:
        from .setup import run_setup
        run_setup(first=False)
        return 0
    if argv[:1] == ["+show-config"]:
        print(settings.show(default="--default" in argv, docs="--docs" in argv))
        return 0
    if argv[:1] == ["+validate-config"]:
        # settings.json alone, then each account's file over it
        accs, account_problems = accounts.load()
        problems = list(account_problems)
        mine = {settings.account_path(a["platform"], a["id"]): a for a in accs}
        for path in settings.account_files():
            if path not in mine:
                problems.append(f"accounts/{path.name}: no account in accounts.json has this "
                                "platform and id -- not read")
        for src in [{"platform": "default"}] + [{"platform": a["platform"], "account": a["id"]}
                                                for p, a in mine.items() if p.exists()]:
            try:
                problems += layout(src).problems
            except Exception as e:                 # a shape the parser did not expect
                problems.append(f"{src.get('account') or 'settings.json'}: {e}")
        for problem in dict.fromkeys(problems):
            print(problem)
        if not problems:
            files = ["settings.json"] * settings.PATH.exists() + [f"accounts/{p.name}" for p in settings.account_files()]
            print(f"{settings.HOME}: {', '.join(files) or 'no files -- all defaults'} ok")
        return 1 if problems else 0
    args = p.parse_args(argv)
    console, errors = Console(), Console(stderr=True)
    start_log()
    srcs, problems = sources()
    for problem in problems:
        errors.print(f"[yellow]!! {problem}[/]")
    if not srcs:
        if not sys.stdout.isatty():
            errors.print("[red]!! no ad accounts yet -- run `adglance setup` in a terminal[/]")
            return 1
        from .setup import run_setup
        if not run_setup(first=True):
            return 0
        srcs, _ = sources()
        if not srcs:
            return 0
    lay = layout(srcs[0])
    by = None
    if args.group:
        by = lay.parse_group(args.group)
        if by is None:
            errors.print(f"[red]!! --group: name columns joined by +, from {', '.join(lay.names)}, or All[/]")
            return 1
    sort = None
    if args.sort:
        sort = []                                  # --sort=-billed,cpf: billed, then cpf
        for word in (w.strip() for w in args.sort.split(",") if w.strip()):
            key = lay.key_of(word.lstrip("-"))
            if not key:
                errors.print(f"[red]!! --sort: {word!r} is not a column[/]")
                return 1
            sort.append((key, word.startswith("-")))
    if not (args.print or args.watch) and sys.stdout.isatty():
        # the interactive screen: fetch once, then filter, group and sort in place;
        # --group / --sort / --daily open it that way, over what it remembered
        from .tui import AdView
        AdView(window, srcs, load_state(), save_state, layout, args.when, args.search,
               by=by, sort=sort, daily=args.daily or None, more_sources=lambda: sources()[0]).run()
        return 0
    for problem in lay.problems:                   # said, then carry on with the rest
        errors.print(f"[yellow]!! {problem}[/]")
    by = by or ()
    try:
        while True:
            # --watch N fetches the unsettled days at least every N seconds
            stale = min(STALE, args.watch) if args.watch else STALE
            console.print(table(args.when, console, lay, srcs[0], args.search, sort, by, args.daily,
                                stale))
            if not args.watch:
                return 0
            time.sleep(args.watch)
            console.clear()
    except KeyboardInterrupt:
        return 0
    except PlatformError as e:
        log.exception("fetch failed")
        errors.print(f"[red]!! {e}[/]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
