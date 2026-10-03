"""adglance's interactive screen (textual): pick a source and a period, then filter and sort.

    platform      which ad platform (TikTok, Meta)
    account       which of its ad accounts (accounts.json); both pickers
                  always show, preset to the last ones picked

    From / To     two date boxes (2026-10-01 or 10-01), Enter in either fetches;
                  From also takes a whole period (7d · mtd · lm · 09-01 09-30);
                  or the Calendar button (c), a button beside them, or [ ]
    filter box    keep rows whose geo/obj/type/flight/ad contain every word,
                  and drop any that contain a -word ("vv -ca", "-promo -srch")
    Group         G (or the button): tick any name columns, or All -- rows merge
                  as you tick; g turns the last grouping off and on again;
                  columns whose values differ inside a group drop out, Ads counts
    a header      click to sort by it; click again to reverse
    a row         the line under the table shows its full names and its counts
    → / ←         on the table: into the group under the cursor -- its ads, to
                  filter, sort, group again (on an ad: its menu) / back up, as it
                  was. The path shows on top and a crumb jumps there; backspace,
                  or esc on an empty filter, goes back too. shift+← → scrolls
    keys          t today  y yesterday  w 7d  m mtd  l last month  [ ] step
                  p from box  c calendar  / filter
                  s source  a account  g group -- the next choice; S A G open the list
                  d daily (one row a day)  esc clear filter
                  r re-read settings.json, fetch the unsettled days again
                  R fetch every day of the period again  q quit

What it shows -- name columns, metrics, sort -- is settings.json; the accounts
are accounts.json (adglance setup). Numbers come from store.py: each day fetched once and
kept, the unsettled last days fetched again, so any period -- a button, a wide
range, Daily -- is summed on this machine; only days the store lacks are fetched.
After the first fetch this year's days are filled in behind the screen.

Opened bare (`adglance`), it opens on the last period (or waits for one); opened with one (`adglance 7d`),
it fetches straight away. Changing the period fetches again and keeps the filter
and the sort. Nothing here can write: every platform request is a GET.
"""
import datetime as dt
import logging
import re
import shlex
import subprocess
import time

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult, SuspendNotSupported
from rich.style import Style
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.theme import Theme
from textual.widgets import (Button, DataTable, Footer, Input, OptionList, Select, SelectionList,
                             Static)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from . import settings
from .actions import ActionScreen
from .datepicker import CalendarScreen
from .store import days, freshness, set_zone, today as account_today
from .style import (ALL, CHEVRONS, PARTS, SYMBOL, compile_formula, evaluate, number, DATE, GLYPHS, MAUVE, LOADING, SPIN, STATUS, SHARE_WIDTH, cells, combine, derive, detail, group, grouping, icon, chevron,
                          header_label, matches, ordered, prepare, with_share)

COMMON = ["today", "yesterday", "mtd", "lm", "7d", "14d", "30d"]
# the period buttons under the period box: (label, what it types)
CHIPS = [("Today", "today"), ("Yesterday", "yesterday"), ("7d", "7d"), ("14d", "14d"),
         ("30d", "30d"), ("MTD", "mtd"), ("Last month", "lm")]
SHORT = {"yesterday": "Yday", "lm": "LM"}             # the words when the screen is narrow

# Catppuccin Mocha as the terminal theme paints it: base #1e1e2e behind
# everything, mantle #181825 for boxes and bars, text #cdd6f4. Textual's own
# "catppuccin-mocha" puts surface/panel greys (#313244, #45475a) under inputs,
# headers and stripes, which reads as a grey slab; this one does not.
MOCHA = Theme(name="adglance-mocha", dark=True,
              primary="#cba6f7", secondary="#89b4fa", accent="#fab387",
              foreground="#cdd6f4", background="#1e1e2e", surface="#181825",
              panel="#181825", boost="#313244",
              success="#a6e3a1", warning="#f9e2af", error="#f38ba8")
DAY = re.compile(r"(\d{4}-)?\d{2}-\d{2}")         # a date typed in From
log = logging.getLogger("adglance")
# the Status column's width from the start: the longest word and its icon
# ("No budget"), so statuses arriving never widen it and push the rest aside
STATUS_WIDTH = 11
ADD_ACCOUNT = "+add"                 # the account picker's last row: the key form, over the numbers
# 1 campaigns, 2 ad groups (in their campaigns), 3 the ads themselves
LEVELS = {1: ("campaign_name",), 2: ("campaign_name", "adgroup_name"), 3: ()}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
STALE = 600                                       # an unsettled day older than this is fetched again
HISTORY = 10                                      # periods remembered


# every key, for the ? screen -- the footer shows only the everyday ones
KEYS = [
    ("Move and act", [("← → ↑ ↓", "move cell by cell; the cursor's row is banded"),
                      ("1  2  3", "campaigns  /  ad groups  /  ads -- enter goes a level down"),
                      ("cmd + ← → ↑ ↓", "to the edge: first / last column, first / last row"),
                      ("", "  (the terminal may keep cmd+↑↓: ctrl + arrows arrive the same;"),
                      ("", "   or Ghostty: keybind = cmd+down=text:\\x1b[1;5B, cmd+up …5A)"),
                      ("enter", "open: a group (❯ marks one) goes a level in; an ad, its menu"),
                      ("esc", "back: clears the filter first, then goes up a level"),
                      ("ctrl + c", "copy the cursor's cell, as shown"),
                      ("s  /  S", "sort by the cursor's column (again: flip)  /  the sort panel"),
                      ("/", "filter: every word must match; -word drops (-ca drops CA)")]),
    ("Period", [("t  y  w  m  l", "Today · Yesterday · 7d · MTD · Last month"),
                ("[  ]", "the window just before / after"),
                ("p  /  c", "type a period (2026-10-01, 7d, lm)  /  the calendar")]),
    ("View", [("g  /  G", "grouping off and back on  /  tick the columns to group by"),
              ("d", "Daily: one row a day, or the period summed"),
              ("h  /  H", "hide the cursor's column  /  every column: search, tick, n a new metric"),
              ("shift + ← →", "move the cursor's column (names among names, metrics among metrics)"),
              ("f  /  F", "pinned columns off and on  /  pick which")]),
    ("Data", [("r  /  R", "refresh the last 30 days  /  every day of this period"),
              ("a  /  A", "the next ad account  /  the list (its last row adds one)"),
              (",", "this account's own options in $EDITOR (over the defaults)"),
              ("q", "quit")]),
]


class KeysScreen(ModalScreen):
    """Every key adglance answers to. Any of esc, ? or q closes it."""
    DEFAULT_CSS = """
    KeysScreen { align: center middle; background: #11111b 50%; }
    KeysScreen > Static { width: auto; height: auto; padding: 1 3;
                          background: #181825; border: round #cba6f7; }
    """
    BINDINGS = [("escape", "dismiss", "Close"), ("question_mark", "dismiss", "Close"),
                ("q", "dismiss", "Close")]

    def compose(self):
        from . import version
        out = Text(f"adglance {version()}  ·  esc or ? closes\n\n", style="#7f849c")   # on top: never scrolled away
        for i, (section, keys) in enumerate(KEYS):
            out.append(("\n" if i else "") + section + "\n", style="bold #cba6f7")
            for key, what in keys:
                out.append(f"  {key:<16}", style="bold #fab387")
                out.append(what + "\n", style="#cdd6f4")

        yield Static(out)


class Table(DataTable):
    """The data table, moved through cell by cell, like a spreadsheet:
    ← → ↑ ↓ the cell; space into the group of its row (back: backspace);
    home / end the first / last column. Sideways it scrolls by columns, done by
    adglance (AdView.cell_step): unpinned columns hide on the left as the cursor
    goes right, and come back as it returns, so pinned ones stay in their place.
    The cursor's row wears a band (_get_row_style), its cell the cursor colour.
    Bound here, not on the app, so the arrows still move the cursor in a box."""
    BINDINGS = [Binding("right", "app.cell_step(1)", show=False),
                Binding("left", "app.cell_step(-1)", show=False),
                # home / end would be a second key for the edges (Cmd+arrows); they
                # are taken off textual's sideways scroll, which our columns replace
                Binding("home", "app.nothing", show=False),
                Binding("end", "app.nothing", show=False),
                Binding("enter", "select_cursor", "Open"),
                Binding("shift+right", "app.move_col(1)", show=False),
                Binding("shift+left", "app.move_col(-1)", show=False),
                Binding("ctrl+c", "app.copy_cell", "Copy", show=False),
                # to the edge, as Cmd+arrows do in Google Sheets. A macOS terminal
                # seldom passes Cmd through: Ghostty sends Cmd+←/→ as ctrl+a /
                # ctrl+e; with the kitty keyboard protocol it can arrive as super+;
                # ctrl+arrows (Sheets on Windows) work everywhere
                Binding("super+right,ctrl+right,ctrl+e", "app.cell_step(99)", show=False),
                Binding("super+left,ctrl+left,ctrl+a", "app.cell_step(-99)", show=False),
                Binding("super+down,ctrl+down", "app.row_edge(1)", show=False),
                Binding("super+up,ctrl+up", "app.row_edge(-1)", show=False),
                # the levels: on the table only, so digits typed in a box stay digits
                Binding("1", "app.level(1)", show=False), Binding("2", "app.level(2)", show=False),
                Binding("3", "app.level(3)", show=False)]
    ROW_BAND = "#313048"
    TOTAL_LIT = "#7a68b0"                             # TOTAL's band, brighter, under the cursor

    def _get_row_style(self, row_index, base_style):
        """The cursor's row: a band under every cell (its cell keeps the cursor
        colour on top). Overrides a private textual method -- if that ever
        changes, the row simply goes unbanded."""
        style = super()._get_row_style(row_index, base_style)
        if row_index == self.cursor_row and row_index >= 0:
            # TOTAL (the fixed row) has its own band: the cursor lights it up
            band = self.TOTAL_LIT if row_index < self.fixed_rows else self.ROW_BAND
            style = style + Style(bgcolor=band)
        return style

    def watch_cursor_coordinate(self, old, new):
        super().watch_cursor_coordinate(old, new)
        if old.row != new.row:                        # the band moves with the row: repaint
            self.refresh_row(old.row)                 # just the two rows (their lines are
            self.refresh_row(new.row)                 # cached by cursor place already)


SUPERSCRIPT = "⁰¹²³⁴⁵⁶⁷⁸⁹"


class SortScreen(ModalScreen):
    """O: the sort now, on top, and every column shown below to change it.
    enter  sort by that column alone        + / space  add it as the next key
           (again: the other way)                      (already in: flip it)
    x      take it out                      esc / O    close
    Each change shows on the table behind at once."""
    DEFAULT_CSS = """
    SortScreen { align: center middle; background: #11111b 40%; }
    SortScreen > Vertical { width: auto; height: auto; padding: 1 2;
                            background: #181825; border: round #cba6f7; }
    SortScreen .title { color: #cba6f7; text-style: bold; }
    SortScreen #now { color: #cdd6f4; margin: 0 0 1 0; }
    SortScreen OptionList { background: #181825; border: none; height: auto; width: 36; }
    SortScreen OptionList:focus { border: none; background-tint: transparent; }
    SortScreen OptionList > .option-list--option-highlighted,
    SortScreen OptionList:focus > .option-list--option-highlighted { background: #313244; }
    SortScreen .hint { color: #7f849c; margin: 1 0 0 0; }
    """
    BINDINGS = [("escape", "dismiss", "Close"),
                Binding("space", "add", show=False),
                Binding("x", "remove", show=False)]

    def __init__(self, view, choices):
        super().__init__()
        self.view, self.choices = view, choices        # [(label, key)] in column order

    def compose(self):
        with Vertical():
            yield Static("Sort by", classes="title")
            yield Static("", id="now")
            yield OptionList()
            yield Static("enter  only this\nspace  add, or flip\nx  take out  ·  esc  close",
                         classes="hint")

    def on_mount(self):
        self._fill()
        options = self.query_one(OptionList)
        options.focus()
        keys = [k for _, k in self.choices]
        here = self.view.cur_key if self.view.cur_key in keys else self.view.sort_key
        if here in keys:                              # opens on the cursor's column
            options.highlighted = keys.index(here)

    def _fill(self):
        """The sort now (numbered) and the columns, each marked with its place."""
        order = self.view._sort_order()
        labels = dict((k, label) for label, k in self.choices)
        now = Text()
        for key, (place, rev) in sorted(order.items(), key=lambda kv: kv[1][0]):
            now.append(f"{place}. ", style="#7f849c")
            now.append(f"{labels.get(key, key).strip()} {'↓' if rev else '↑'}\n", style="bold #cba6f7")
        self.query_one("#now", Static).update(now)
        options = self.query_one(OptionList)
        at = options.highlighted
        options.clear_options()
        for label, key in self.choices:
            mark = (f"  {'↓' if order[key][1] else '↑'}{order[key][0]}" if key in order else "")
            options.add_option(Option(Text.assemble(label, (mark, "bold #cba6f7")), id=key))
        if at is not None:
            options.highlighted = at

    def _key(self):
        options = self.query_one(OptionList)
        return None if options.highlighted is None else options.get_option_at_index(options.highlighted).id

    def on_option_list_option_selected(self, event):
        self.view._sort_by(event.option.id)
        self._fill()

    def action_add(self):
        if self._key():
            self.view.sort_add(self._key())
            self._fill()

    def action_remove(self):
        if self._key():
            self.view.sort_remove(self._key())
            self._fill()


class ColumnsScreen(ModalScreen):
    """H: every column there is, ticked when shown, with a search on top. A
    tick applies at once; the number columns are written to the account's own
    file (show). n makes a new metric; r puts back the settings' columns."""
    DEFAULT_CSS = """
    ColumnsScreen { align: center middle; background: #11111b 40%; }
    ColumnsScreen > Vertical { width: 64; height: auto; max-height: 90%; padding: 1 2;
                               background: #181825; border: round #cba6f7; }
    ColumnsScreen .title { color: #cba6f7; text-style: bold; margin: 0 0 1 0; }
    ColumnsScreen #q { margin: 0 0 1 0; }
    ColumnsScreen SelectionList { background: #181825; border: none; height: auto;
                                  max-height: 22; width: 100%; }
    ColumnsScreen SelectionList:focus { border: none; background-tint: transparent; }
    ColumnsScreen .hint { color: #7f849c; margin: 1 0 0 0; }
    """
    BINDINGS = [("escape", "dismiss", "Close"), ("H", "dismiss", "Close"),
                Binding("r", "defaults", show=False), Binding("n", "new", show=False)]

    def __init__(self, title, columns, visible, on_change, on_reset, on_new):
        """columns: [(key, label, what it is, available)]"""
        super().__init__()
        self.title_text, self.choices, self.shown = title, columns, set(visible)
        self.on_change, self.on_reset, self.on_new = on_change, on_reset, on_new

    def compose(self):
        with Vertical():
            yield Static(self.title_text, classes="title")
            yield Input(placeholder="Search columns…  (cpv, click, complete…)", id="q")
            yield SelectionList()
            yield Static("↓ to the list  ·  space ticks  ·  n new metric  ·  r the defaults  ·  esc closes",
                         classes="hint")

    def on_mount(self):
        self._fill("")
        self.query_one("#q").focus()

    def _fill(self, query):
        words = query.lower().split()
        box = self.query_one(SelectionList)
        box.clear_options()
        for key, label, what, available in self.choices:
            hay = f"{key} {label} {what}".lower()
            if words and not all(w in hay for w in words):
                continue
            text = Text(f"{label:<16}", style="" if available else "#585b70")
            text.append(what, style="#6c7086" if available else "#45475a")
            box.add_option(Selection(text, key, key in self.shown, disabled=not available))

    def on_input_changed(self, event):
        if event.input.id == "q":
            self._fill(event.value)

    def _to_list(self):
        box = self.query_one(SelectionList)
        box.focus()
        if box.highlighted is None and box.option_count:
            box.highlighted = 0                       # space ticks the first match at once

    def on_input_submitted(self, event):
        self._to_list()

    def on_key(self, event):
        if event.key == "down" and self.focused is self.query_one("#q"):
            self._to_list()
            event.stop()

    def on_selection_list_selection_toggled(self, event):
        key = event.selection.value
        self.shown ^= {key}
        if not self.shown:                            # the last column stays
            self.shown.add(key)
            self.query_one(SelectionList).select(key)
            return
        self.on_change(set(self.shown))

    def action_defaults(self):
        self.dismiss()
        self.on_reset()

    def action_new(self):
        self.dismiss()
        self.on_new()


class MetricScreen(ModalScreen):
    """A new number column: a name, a formula over the counts, a format. The
    formula is checked as it is typed, and worked out on what is shown now."""
    DEFAULT_CSS = """
    MetricScreen { align: center middle; background: #11111b 40%; }
    MetricScreen > Vertical { width: 70; height: auto; padding: 1 2; background: #181825;
                              border: round #cba6f7; }
    MetricScreen .m-title { color: #cba6f7; text-style: bold; }
    MetricScreen .m-label { color: #a6adc8; margin: 1 0 0 0; }
    MetricScreen .m-hint { color: #7f849c; }
    MetricScreen Input { width: 100%; }
    MetricScreen Select { width: 30; }
    MetricScreen #preview { margin: 1 0 0 0; height: auto; }
    """
    BINDINGS = [("escape", "dismiss", "Close")]
    FORMATS = [("cost (currency, .1)", "cost"), ("money (currency, whole)", "money"),
               ("pct (x 100, %)", "pct"), ("number", "number")]

    def __init__(self, total, provides, platform, currency, on_save):
        super().__init__()
        self.total, self.provides, self.platform, self.currency = total, provides, platform, currency
        self.on_save = on_save

    def compose(self):
        with Vertical():
            yield Static("New metric", classes="m-title")
            yield Static("written to this account's own file (, opens it)", classes="m-hint")
            yield Static("Name", classes="m-label")
            yield Input(placeholder="3s CPV", id="name")
            yield Static("Formula: counts, numbers, + - * / and brackets", classes="m-label")
            yield Input(placeholder="billed / views_2s", id="formula")
            yield Static("  " + "  ".join(PARTS), classes="m-hint")
            yield Static("Format", classes="m-label")
            yield Select(self.FORMATS, value="cost", allow_blank=False, compact=True, id="format")
            yield Static("", id="preview")
            yield Static("enter saves  ·  esc closes", classes="m-hint")

    def on_mount(self):
        self.query_one("#name").focus()

    def _check(self):
        """(name, formula, format) when they will do, else None; the preview says why."""
        preview = self.query_one("#preview", Static)
        name = self.query_one("#name", Input).value.strip()
        formula = self.query_one("#formula", Input).value.strip()
        kind = self.query_one("#format", Select).value
        if not formula:
            preview.update(Text("type a formula, e.g. billed / views_2s", style="#7f849c"))
            return None
        try:
            tree = compile_formula(formula)
        except ValueError as e:
            preview.update(Text(f"✗ {e}", style="#f38ba8"))
            return None
        missing = sorted(set(re.findall(r"[a-z_][a-z0-9_]*", formula)) - self.provides)
        value = evaluate(tree, self.total) if self.total else None
        shown = "–" if value is None else number(value, kind)
        line = Text(f"= {shown}  on everything shown now", style="#a6e3a1")
        if missing:
            line.append(f"\n{self.platform} does not report {', '.join(missing)}: it would show –",
                        style="#f9e2af")
        preview.update(line)
        return (name, formula, kind) if name else None

    def on_input_changed(self, event):
        self._check()

    def on_select_changed(self, event):
        self._check()

    def on_input_submitted(self, event):
        if event.input.id == "name":
            self.query_one("#formula").focus()
            return
        got = self._check()
        if got is None:
            if not self.query_one("#name", Input).value.strip():
                self.query_one("#name").focus()
            return
        self.dismiss()
        self.on_save(*got)


class PinScreen(ModalScreen):
    """F: tick the name columns to pin on the left; each tick applies at once."""
    DEFAULT_CSS = """
    PinScreen { align: center middle; background: #11111b 40%; }
    PinScreen > Vertical { width: auto; height: auto; padding: 1 2;
                           background: #181825; border: round #cba6f7; }
    PinScreen .title { color: #cba6f7; text-style: bold; margin: 0 0 1 0; }
    PinScreen SelectionList { background: #181825; border: none; height: auto; width: 30; }
    PinScreen SelectionList:focus { border: none; background-tint: transparent; }
    PinScreen .hint { color: #7f849c; margin: 1 0 0 0; }
    """
    BINDINGS = [("escape", "dismiss", "Close"), ("F", "dismiss", "Close")]

    def __init__(self, names, labels, pinned, on_change):
        super().__init__()
        self.names, self.labels, self.pinned, self.on_change = names, labels, set(pinned), on_change

    def compose(self):
        with Vertical():
            yield Static("Pin on the left", classes="title")
            yield SelectionList(*[Selection(self.labels.get(n, n), n, n in self.pinned)
                                  for n in self.names])
            yield Static("space · enter · click ticks\nkept for next time  ·  esc closes",
                         classes="hint")

    def on_mount(self):
        self.query_one(SelectionList).focus()

    def on_selection_list_selection_toggled(self, event):
        self.on_change(list(self.query_one(SelectionList).selected))


class GroupScreen(ModalScreen):
    """Tick the name columns to group by, or All. Each tick regroups the table
    behind the list at once (on_change); esc, g or G closes it."""
    DEFAULT_CSS = """
    GroupScreen { align: center middle; background: #11111b 40%; }
    GroupScreen > Vertical { width: auto; height: auto; padding: 1 2;
                             background: #181825; border: round #cba6f7; }
    GroupScreen .title { color: #cba6f7; text-style: bold; margin: 0 0 1 0; }
    GroupScreen SelectionList { background: #181825; border: none; height: auto;
                                width: 34; padding: 0; }
    GroupScreen SelectionList:focus { border: none; background-tint: transparent; }
    GroupScreen .hint { color: #7f849c; margin: 1 0 0 0; }
    """
    BINDINGS = [("escape", "dismiss", "Close"), ("g", "dismiss", "Close"), ("G", "dismiss", "Close")]

    def __init__(self, names, labels, by, on_change):
        super().__init__()
        self.names, self.labels, self.by, self.on_change = names, labels, set(by), on_change

    def compose(self):
        with Vertical():
            yield Static(f"{icon('group')}Group by", classes="title")
            items = [Selection(self.labels.get(n, n), n, n in self.by) for n in self.names]
            items.append(Selection("All (one row)", ALL, ALL in self.by))
            yield SelectionList(*items)
            yield Static("space · enter · click ticks\nesc closes", classes="hint")

    def on_mount(self):
        self.query_one(SelectionList).focus()

    def on_selection_list_selection_toggled(self, event):
        """All and the columns exclude each other: ticking one clears the other."""
        picks = self.query_one(SelectionList)
        value = event.selection.value
        if value in picks.selected:
            if value == ALL:
                for n in self.names:
                    picks.deselect(n)
            else:
                picks.deselect(ALL)
        self.on_change(list(picks.selected))


class AdView(App):
    TITLE = "adglance"
    CSS = """
    Screen#_default { background: #1e1e2e; }      /* not the calendar: the table shows through */
    /* the top: what to fetch (source, period), then what to show of it (filter) */
    #top { dock: top; height: auto; padding: 1 1 1 1; background: #1e1e2e; }
    .row { height: 1; margin: 0 0 1 0; }
    #row-filter { margin: 0; }
    .label { width: 10; color: #7f849c; text-style: bold; }
    #platform { width: 14; }
    #account { width: 26; margin: 0 0 0 2; }      /* widened to its longest name: _fit_account */
    #from, #to { width: 11; }         /* 2026-10-02 and the cursor, nothing more */
    .arrow { width: 3; color: #7f849c; content-align: center middle; }
    #cal { margin: 0 2 0 1; }
    #filter { width: 1fr; }
    .sublabel { width: auto; color: #7f849c; text-style: bold; margin: 0 1 0 2; }
    #group { width: auto; min-width: 0; padding: 0 1; margin: 0 0 0 2;
             background: #181825; color: #cdd6f4; text-style: none; }
    #group:hover { background: #313244; }
    #group.on { color: #cba6f7; text-style: bold; }
    Input, SelectCurrent { background: #181825; color: #cdd6f4; }
    Input:focus, Select:focus > SelectCurrent { background: #313244; }
    Input > .input--placeholder, Input > .input--suggestion { color: #6c7086; }
    SelectOverlay { background: #181825; border: tall #cba6f7; }
    #period-line { height: auto; margin: 0 0 1 0; }
    #period-line.stacked { layout: vertical; }
    #row-period, #row-chips { width: auto; height: 1; }
    /* the buttons keep their distance from the date boxes: a gap beside them,
       a blank line above them once they move under */
    #row-chips { margin: 0 0 0 3; }
    #period-line.stacked #row-chips { margin: 1 0 0 0; }
    #chip-pad { display: none; }
    #period-line.stacked #chip-pad { display: block; }
    #period-line.tight #chip-pad { display: none; }
    #period-line.tight #row-chips Button { padding: 0 0; }
    #row-period Button, #row-chips Button { width: auto; min-width: 0; padding: 0 1; margin: 0 1 0 0;
                         background: #181825; color: #cdd6f4; text-style: none; }
    #row-period Button:hover, #row-chips Button:hover { background: #313244; }
    #row-chips Button.active { background: #cba6f7; color: #11111b; text-style: bold; }
    #row-chips Button.step { color: #fab387; }
    DataTable { height: 1fr; background: #1e1e2e; color: #cdd6f4; }
    DataTable > .datatable--header { background: #181825; color: #cba6f7; text-style: bold; }
    DataTable > .datatable--even-row { background: #1e1e2e; }
    DataTable > .datatable--odd-row { background: #232334; }
    /* the cursor's cell: mauve, dark text -- unmissable on its banded row */
    DataTable > .datatable--cursor { background: #cdd6f4; color: #11111b; text-style: bold; }
    DataTable > .datatable--hover { background: #313244; }
    /* TOTAL, the fixed row: a mauve-tinted band, so it reads apart from the
       header above it (#181825) and the striped rows below (#1e1e2e / #232334) */
    /* TOTAL, the fixed row (pins are adglance's own, not textual's fixed
       columns, so this class is TOTAL's alone): its own mauve band, white Σ */
    DataTable > .datatable--fixed { background: #5a4a86; }
    DataTable > .datatable--fixed-cursor { background: #cdd6f4; color: #11111b; text-style: bold; }
    /* problems: config until fixed (top strip), the rest in the status line */
    #banner { display: none; height: auto; padding: 0 1; margin: 0 0 1 0;
              background: #3b2533; color: #f38ba8; }
    #banner.shown { display: block; }
    Input.invalid { background: #3b2533; color: #f38ba8; }
    #crumbs { display: none; height: 1; margin: 0 0 1 0; }
    /* the summary: what is shown, summed, and its change on the period before */
    #cards { height: 4; margin: 0 0 1 0; padding: 0 1; }
    #cards .card { width: 1fr; max-width: 34; height: 4; padding: 0 2; margin: 0 1 0 0;
                   background: #181825; border-left: tall #cba6f7; }
    #crumbs.shown { display: block; }
    #crumbs Button { width: auto; min-width: 0; padding: 0 1; margin: 0; background: #181825;
                     color: #b4befe; text-style: none; }
    #crumbs Button:hover { background: #313244; }
    #crumbs Button.here { color: #cba6f7; text-style: bold; background: #1e1e2e; }
    #crumbs Static { width: auto; color: #6c7086; padding: 0 0; }
    #message { display: none; height: 1fr; content-align: center middle; color: #a6adc8; }
    #detail { height: 2; padding: 0 1; background: #1e1e2e; color: #7f849c;
              text-wrap: nowrap; text-overflow: ellipsis; }
    #summary { height: 1; padding: 0 1; background: #181825; color: #a6adc8; }
    Footer { background: #181825; }
    FooterKey .footer-key--key { color: #fab387; background: #181825; }
    FooterKey .footer-key--description { color: #a6adc8; background: #181825; }
    """
    # t y w m l are the buttons' own keys; the buttons show them, the footer need not
    BINDINGS = [Binding("t", "quick('today')", show=False),
                Binding("y", "quick('yesterday')", show=False),
                Binding("w", "quick('7d')", show=False), Binding("m", "quick('mtd')", show=False),
                Binding("l", "quick('lm')", show=False),
                Binding("left_square_bracket", "step(-1)", show=False),
                Binding("right_square_bracket", "step(1)", show=False),
                Binding("p", "focus_period", show=False), Binding("c", "calendar", show=False),
                ("slash", "focus_filter", "Filter"),
                # each picker: lower case steps to the next choice, upper case opens the list
                Binding("a", "next_account", show=False), Binding("A", "open('account')", show=False),
                ("g", "toggle_group", "Group on/off"), Binding("G", "pick_group", show=False),
                Binding("escape", "clear", "Back"),
                ("s", "sort_here", "Sort"), Binding("S", "pick_sort", show=False),
                Binding("f", "toggle_pin", show=False), Binding("F", "pick_pin", show=False),
                Binding("h", "hide_here", show=False), Binding("H", "pick_columns", show=False),
                ("d", "daily", "Daily"), ("r", "reload", "Refresh"),
                Binding("comma", "settings", show=False),
                Binding("R", "refetch_all", show=False), ("question_mark", "help", "Keys"),
                ("q", "quit", "Quit")]

    def __init__(self, window, sources, state, save, layout, words=(), query="",
                 by=None, sort=None, daily=None, more_sources=None):
        """window(words) -> (start, end), raising SystemExit on a bad period;
        sources: [{key, label, rows(start, end)}] -- every platform's accounts;
        state / save: what was picked and typed before, and how to keep it;
        layout(source): its settings (defaults, settings.json, its own file), problems in .problems.
        All from adglance, so this screen and --print read periods the same way."""
        super().__init__()
        self.window, self.sources, self.state, self.save = window, sources, dict(state), save
        self.more_sources = more_sources               # every source again, for one just added
        by_key = {src["key"]: src for src in sources}
        self.source = by_key.get(self.state.get("source")) or sources[0]
        set_zone(self.source.get("timezone"))          # the remembered account's days
        self.history = [h for h in self.state.get("periods", []) if isinstance(h, str)]
        self.words, self.query_text = list(words), query
        self.raw, self.rows, self.span, self.dates = [], [], "", None
        self.prev_asked = set()                       # periods-before already asked for
        self.make_layout = layout
        self.layout = layout(self.source)
        self.problem, self.note = None, ""
        self.busy, self.busy_label, self.spinner, self.frame = None, "Fetching", None, 0
        self._restore_view()
        self.recs = []                                # what the table shows, row by row
        # drill-down: the rows must match `where`; `levels` holds each level above
        # (its where, grouping, filter, sort, cursor and crumb) to go back to
        self.where, self.levels = {}, []
        self.hidden = 0                               # unpinned columns scrolled off on the left
        self.cur_key = None                           # the column the cursor is on
        self.typing = None                            # the filter's pending redraw
        self.status_timer, self.status_failed, self.status_full = None, False, False
        self.failed = None                            # (dates, message) of a fetch that failed
        self.filters = []                             # the filters of the levels above, all still on
        self.backfilled = False
        self.daily = bool(self.state.get("daily"))
        # what the command line asked for wins over what was remembered
        if by is not None:
            self.by = by
            self.last_by = by or self.last_by          # g turns it off and back to it
        if daily is not None:
            self.daily = daily
            if daily and sort is None:
                self.sort_key, self.reverse = f"n:{DATE}", False   # as d does
                self.then = []
        if sort is not None:                           # [(key, reverse), ...] from --sort
            (self.sort_key, self.reverse), self.then = sort[0], list(sort[1:])
        # bare: open on the last period if there is one, else wait in the From box
        if not self.words and self.history and self.history[0].strip():
            self.words = self.history[0].split()
        self.AUTO_FOCUS = "DataTable" if self.words else "#from"

    def compose(self) -> ComposeResult:
        with Vertical(id="top"):
            yield Static("", id="banner")
            # 1. which account -- what the data is
            with Horizontal(classes="row", id="row-source"):
                yield Static(f"{icon('source')}Source", classes="label")
                platforms = {src["platform"]: src["platform_label"] for src in self.sources}
                yield Select([(label, name) for name, label in platforms.items()],
                             value=self.source["platform"], allow_blank=False,
                             compact=True, id="platform")
                yield Select(self._account_options(self.source["platform"]),
                             value=self.source["key"], allow_blank=False,
                             compact=True, id="account")
            # 2. which days -- changing it fetches again
            # the boxes, then the buttons: one line when they fit, the buttons on a
            # line of their own when not, shorter words when even that is too tight
            with Horizontal(id="period-line"):
              with Horizontal(id="row-period"):
                yield Static(f"{icon('period')}Period", classes="label")
                suggest = SuggestFromList(
                    self.history + [c for c in COMMON if c not in self.history],
                    case_sensitive=False)
                yield Input(value=" ".join(self.words), id="from", suggester=suggest,
                            compact=True, placeholder="YYYY-MM-DD")
                yield Static("→", classes="arrow")
                yield Input(id="to", compact=True, placeholder="YYYY-MM-DD")
                yield Button(f"{icon('calendar')}Calendar", id="cal", compact=True)
              with Horizontal(id="row-chips"):
                yield Static("", id="chip-pad", classes="label")
                yield Button(chevron(False), id="step-back", compact=True, classes="step")
                for label, period in CHIPS:
                    yield Button(label, id=f"chip-{period}", compact=True)
                yield Button(chevron(), id="step-next", compact=True, classes="step")
            # 3. what to show of it -- instant, no fetch
            with Horizontal(classes="row", id="row-filter"):
                yield Static(f"{icon('filter')}Filter", classes="label")
                yield Input(value=self.query_text, id="filter", compact=True,
                            placeholder="Filter…")
                yield Button(self._group_label(), id="group", compact=True)
        yield Horizontal(id="cards")              # the summary: totals and their change
        yield Horizontal(id="crumbs")             # where a drill-down has gone: All ❯ US ❯ ...
        table = Table(zebra_stripes=True, cursor_type="cell", cell_padding=1)
        # the cursor's cell takes the cursor's colours (its row keeps theirs)
        table.cursor_foreground_priority = "css"
        table.fixed_rows = 0                          # TOTAL's fixed row is set once rows are in
        yield table
        yield Static("", id="message")
        yield Static("", id="detail")
        yield Static(self._hint(), id="summary")
        yield Footer()

    def on_resize(self, event):
        self._arrange(event.size.width)

    def _arrange(self, width):
        """The period line for this width: one line if it fits; else the buttons
        under the boxes; if even that is too tight, shorter words."""
        # measured: a button is its label + 4 (padding, compact frame) + 1 margin
        def chips(short):
            labels = [SHORT.get(p, l) if short else l for l, p in CHIPS] + [chevron(False), chevron()]
            return sum(len(x) + 5 for x in labels)
        cal = len(f"{icon('calendar')}Calendar") + 4 + 3                 # + its margins
        boxes = 10 + 11 + 3 + 11 + cal
        stacked = width < 3 + boxes + 3 + chips(False)          # 3: side padding; 3: the gap
        short = stacked and width < 3 + 10 + chips(False)
        line = self.query_one("#period-line")
        line.set_class(stacked, "stacked")
        line.set_class(short, "tight")                 # no inner padding, no indent
        for label, period in CHIPS:
            self.query_one(f"#chip-{period}", Button).label = SHORT.get(period, label) if short else label
        self.query_one("#cal", Button).label = (icon("calendar") or "Cal") if short else (
            f"{icon('calendar')}Calendar")

    def _hint(self):
        recent = "  ·  recent: " + " · ".join(self.history[:5]) if self.history else ""
        return ("type From / To (2026-10-01 or 10-01) and press Enter, or c for the calendar"
                "  ·  [ ] step back / forward"
                + recent)

    def _account_options(self, platform, add=True):
        """The platform's accounts, then "+ Add account…" (the key form, here)."""
        return ([(src["account_label"], src["key"]) for src in self.sources
                 if src["platform"] == platform] + ([("+ Add account…", ADD_ACCOUNT)] if add else []))

    def on_mount(self):
        self.register_theme(MOCHA)
        self.theme = "adglance-mocha"
        self.title = f"adglance · {self.source['label']}"
        self._columns(self.layout.columns())
        self._arrange(self.size.width)
        self.set_interval(30, lambda: self._status(self.problem))   # "4m ago" keeps counting
        self.set_interval(60, self._keep_current)      # left open, it keeps itself current
        self._banner()
        self._fit_account()
        self.query_one("#group", Button).set_class(bool(self.by), "on")
        if self.words:
            self._load(" ".join(self.words))
        if not self.state.get("hinted"):               # once: where everything else is set
            self.notify(", opens settings.json -- name columns, metrics, targets, colours. "
                        "? lists every key.", timeout=10)
            self.state["hinted"] = True
            self.save(self.state)

    def action_settings(self):
        """,: settings.json in $EDITOR (the screen waits), then read it again."""
        path = settings.ensure(self.source["platform"], self.source["account"], self.source["label"])
        try:
            with self.suspend():
                subprocess.call([*shlex.split(settings.editor()), str(path)])
        except SuspendNotSupported:
            self.notify(f"cannot leave the screen here: edit {path}, then press r", timeout=10)
            return
        except (OSError, ValueError) as e:
            self.notify(f"cannot open an editor ({e}): set $EDITOR, or edit {path}", severity="error")
            return
        self.action_reload()

    # ---- period -> data -------------------------------------------------------
    def _load(self, text, refetch=None):
        """Show the period from the store at once, then fetch only the days it
        lacks: never fetched, or unsettled and last fetched over STALE ago.
        refetch: "recent" (r) the unsettled days again, "all" (R) every day shown."""
        words = text.split()
        try:
            start, end = self.window(words)
        except SystemExit as e:                       # a bad period: mark the box, say why
            for box in self.query("#from, #to"):
                box.add_class("invalid")
            self._status(str(e).removeprefix("adglance: "))
            return
        for box in self.query("#from, #to"):
            box.remove_class("invalid")
        self.problem = None
        self.words = words
        self.dates = (dt.date.fromisoformat(start), dt.date.fromisoformat(end))
        self.span = start if start == end else f"{start} .. {end}"
        self.sub_title = self.span
        # the boxes always show the real days, whatever was typed or clicked
        self.query_one("#from", Input).value, self.query_one("#to", Input).value = start, end
        for button in self.query("#row-chips Button"):      # light the button for this period
            button.set_class(button.id == f"chip-{text.strip()}", "active")
        store = self.source["store"]
        if refetch == "all":
            need = list(days(start, end))
        elif refetch == "recent":
            settled = store.settled_before()
            need = sorted(set(store.missing(start, end)) | set(
                store.missing(str(settled), str(account_today()), recent_since=time.time())))
        else:
            need = store.missing(start, end, recent_since=time.time() - STALE)
        if not need:
            # nothing to fetch for this period: a fetch still out for the one you
            # left no longer speaks for the screen, so its spinner goes too
            self.query_one(DataTable).loading = False
            self._busy(None)
            self._from_store(self._as_of())
            self._remember(" ".join(words))
            self._fetch_status(full=refetch is not None)
            return
        # the numbers stay on screen; a spinner in the status line says new ones are coming
        self.busy_label = "Refreshing" if refetch else "Fetching"
        self._from_store(self._as_of())
        self._busy(f"{self.busy_label} {len(need)} day{'s' if len(need) != 1 else ''}…")
        self.query_one(DataTable).loading = not self.rows
        self._fetch(need, " ".join(words), (start, end))

    def _as_of(self):
        """How fresh what is shown is: the oldest fetch of its unsettled days."""
        store, (start, end) = self.source["store"], map(str, self.dates)
        settled = str(store.settled_before())
        if end < settled:
            return f"{icon('lock')}settled days"
        at = store.fetched_at(max(start, settled), end)
        return f"{icon('clock')}as of {dt.datetime.fromtimestamp(at):%H:%M}" if at else ""

    @work(thread=True, exclusive=True, group="fetch")
    def _fetch(self, need, typed, dates):
        def progress(done, total):
            if total > 1:
                self.call_from_thread(self._progress, dates, done, total)
        try:
            self.source["store"].fetch(need, progress)
            error = None
        except Exception as e:                        # one line on screen, the rest in the log
            log.exception("fetch %s days from %s", len(need), need[0])
            error = str(e).splitlines()[0][:160] if str(e) else type(e).__name__
        self.call_from_thread(self._fetched, dates, error, typed)

    def _current(self, dates):
        return dates == tuple(map(str, self.dates or ()))

    def _progress(self, dates, done, total):
        """A long period fills in as its pieces arrive."""
        if self._current(dates):
            self.query_one(DataTable).loading = False
            self._from_store(self.note)
            self._busy(f"{self.busy_label} {done}/{total}…")

    def _fetched(self, dates, error, typed):
        if not self._current(dates):
            return                                    # an answer for a period since left
        self.query_one(DataTable).loading = False
        self._busy(None)
        if error:
            if self.rows:                             # keep what is on screen, say it is old
                self._from_store(self._as_of())
                self._status(f"fetch failed {dt.datetime.now():%H:%M}: {error}")
            else:
                # kept, so a redraw (d, a filter) says this, not "No spend"
                self.failed = (dates, f"Could not fetch {self.span}\n\n{error}\n\n"
                                      f"r tries again  ·  the whole error is in ~/.cache/adglance/adglance.log")
                self.problem = f"fetch failed {dt.datetime.now():%H:%M}: {error}"
                self._from_store("")
            return
        self.failed = None
        self._from_store(self._as_of())
        self._remember(typed)
        # the numbers are up; the statuses (a separate, slower call) follow
        self._fetch_status(full=self.status_full)
        if not self.backfilled:
            self.backfilled = True
            self._backfill()

    @work(thread=True, group="backfill")
    def _backfill(self):
        """This year's days not fetched yet, and the unsettled ones, behind the
        screen -- after this any period this year draws without a fetch."""
        store, today = self.source["store"], account_today()
        need = store.missing(f"{today.year}-01-01", str(today), recent_since=time.time() - STALE)
        if not need:
            return
        try:
            store.fetch(need)
        except Exception:
            log.exception("backfill")                 # the next fetch on screen will say it
            return
        self.call_from_thread(self._from_store, self._as_of())

    def _from_store(self, note):
        """The shown period's rows, from the store -- summed over its days, or
        one row a day when Daily is on."""
        self.note = note
        if not self.dates:
            return
        start, end = map(str, self.dates)
        raw = self.source["store"].rows(start, end, self.daily, self.layout.fee)
        statuses = self.source["store"].statuses()
        if statuses is None and self.status_failed:
            statuses = {}                             # failed: "–", not a spinner for ever
        self.rows = prepare(raw, self.layout, statuses)
        self._redraw()
        self._spin_status(statuses is None)

    @work(thread=True, exclusive=True, group="status")
    def _fetch_status(self, full=False):
        """The ads' statuses, after the numbers: the Status cells spin until they land."""
        if STATUS not in self.layout.names:
            return
        store = self.source["store"]
        try:
            store.fetch_statuses(full=full)
            error = None
        except Exception as e:
            log.exception("statuses")
            error = str(e).splitlines()[0][:120] if str(e) else type(e).__name__
        self.call_from_thread(self._status_landed, store, error)

    def _status_landed(self, store, error):
        self.status_full = False
        if store is not self.source["store"]:
            return                                    # an account since left
        self.status_failed = bool(error) and store.statuses() is None
        self._from_store(self.note)
        if error:
            self._status(f"statuses failed: {error}")

    def _spin_status(self, loading):
        """Turn the Status cells' spinner while statuses are on their way --
        only those cells are touched, not the whole table."""
        if loading and not self.status_timer:
            self.status_timer = self.set_interval(0.12, self._turn_status)
        elif not loading and self.status_timer:
            self.status_timer.stop()
            self.status_timer = None

    def _turn_status(self):
        table = self.query_one(DataTable)
        SPIN[0] = SPINNER[(SPINNER.index(SPIN[0]) + 1) % len(SPINNER)] if SPIN[0] in SPINNER else SPINNER[0]
        try:
            col = list(table.columns).index(f"n:{STATUS}")
        except ValueError:
            return
        # only the rows on screen: a long table would otherwise rewrite every
        # Status cell (1,400 of them in a year's Daily) eight times a second
        top = int(table.scroll_y)
        for i in range(max(0, top - 1), min(len(self.recs), top + table.size.height + 1)):
            r = self.recs[i]
            if r and r["names"].get(STATUS) == LOADING:
                table.update_cell_at(Coordinate(i, col), Text(SPIN[0], style="#cba6f7"))

    def action_daily(self):
        """d: one row a day, or the period summed. Daily starts sorted by date."""
        self.daily = not self.daily
        if self.daily and (self.sort_key, self.reverse) == self.layout.sort and not self.then:
            self.sort_key, self.reverse = f"n:{DATE}", False
        elif not self.daily and self.sort_key == f"n:{DATE}":
            self.sort_key, self.reverse = self.layout.sort
        self.then = [(k, r) for k, r in self.then if self.daily or k != f"n:{DATE}"]
        self.state["daily"] = self.daily
        self.save(self.state)
        self._from_store(self.note)

    def _remember(self, typed):
        """Keep the source and the period for next time, newest first."""
        self.history = [typed] + [h for h in self.history if h != typed][:HISTORY - 1]
        self.state.update(source=self.source["key"], periods=self.history)
        self.save(self.state)

    def on_select_changed(self, event):
        if event.select.screen is not self.screen_stack[0]:
            return                                    # a form's own picker (adding an account)
        if event.select.id == "platform" and event.value != self.source["platform"]:
            # a new platform: offer its accounts, take the first, fetch again
            options = self._account_options(event.value)
            account = self.query_one("#account", Select)
            account.set_options(options)
            self.call_after_refresh(self._fit_account)
            account.value = options[0][1]
        elif event.select.id == "account" and event.value == ADD_ACCOUNT:
            with self.prevent(Select.Changed):           # the picker keeps showing this account
                event.select.value = self.source["key"]
            self._add_account()
        elif event.select.id == "account" and event.value != self.source["key"]:
            self._switch(next(src for src in self.sources if src["key"] == event.value))

    def _fit_account(self):
        """The account picker, and the list it drops, as wide as its longest
        name (26 to 52 columns): a long account name is not cut to its first words."""
        longest = max((len(label) for label, _ in self._account_options(self.source["platform"])), default=0)
        self.query_one("#account", Select).styles.width = max(26, min(52, longest + 8))

    def _add_account(self):
        """The key form over the numbers; account_added comes back when saved."""
        from . import accounts
        from .setup import KeyScreen
        self.accounts, _ = accounts.load()
        self.push_screen(KeyScreen(inside=True))

    def account_added(self, acc):
        """Show the account just added: its source joins the pickers."""
        key = f"{acc['platform']}:{acc['id']}"
        src = next((s for s in self.sources if s["key"] == key), None)
        if src is None and self.more_sources:
            src = next((s for s in self.more_sources() if s["key"] == key), None)
            if src:
                self.sources.append(src)
        if src is None:
            self.notify("saved -- reopen adglance to see it", timeout=6)
            return
        with self.prevent(Select.Changed):
            platforms = {s["platform"]: s["platform_label"] for s in self.sources}
            self.query_one("#platform", Select).set_options([(l, n) for n, l in platforms.items()])
        self._switch(src)
        self.notify(f"added {src['label']}", timeout=4)

    def _switch(self, src):
        """Show another account, maybe on another platform: both pickers follow
        without answering their own change. Each account has its own options,
        so its columns and remembered view come with it; a drill-down does not
        carry over."""
        self.source = src
        self.layout = self.make_layout(src)
        self._restore_view()
        self.where, self.levels, self.filters = {}, [], []
        self.query_one("#group", Button).set_class(bool(self.by), "on")
        with self.prevent(Select.Changed):
            self.query_one("#platform", Select).value = src["platform"]
            account = self.query_one("#account", Select)
            account.set_options(self._account_options(src["platform"]))
            account.value = src["key"]
        self._fit_account()
        set_zone(src.get("timezone"))                 # its days, now
        self.title = f"adglance · {src['label']}"
        self.action_reload()

    def on_input_submitted(self, event):
        if event.input.id in ("from", "to"):
            start = self.query_one("#from", Input).value.strip()
            end = self.query_one("#to", Input).value.strip()
            if event.input.id == "from" and not DAY.fullmatch(start):
                self._load(start)                     # a whole period typed in From: 7d, lm, ...
            else:
                self._load(f"{start} {end or start}")
            self.query_one(DataTable).focus()
        elif event.input.id == "filter":              # keep the words, back to the table
            self.query_one(DataTable).focus()

    def action_quick(self, period):
        self._load(period)

    def action_calendar(self):
        def picked(days):
            if days:
                start, end = days
                self._load(str(start) if start == end else f"{start} {end}")
        box = self.query_one("#from", Input).region     # drop down under the From box
        self.push_screen(CalendarScreen(self.dates, (box.x - 1, box.y + 1)), picked)

    def action_step(self, direction):
        """The window of the same length just before (-1) or after (+1) this one."""
        if not self.dates:
            return
        start, end = self.dates
        days = (end - start).days + 1
        if direction < 0:
            start, end = start - dt.timedelta(days=days), start - dt.timedelta(days=1)
        else:
            today = account_today()
            if end >= today:
                self.notify("already up to today", timeout=3)
                return
            start, end = end + dt.timedelta(days=1), min(end + dt.timedelta(days=days), today)
        fmt = "%m-%d" if start.year == account_today().year else "%Y-%m-%d"
        text = start.strftime(fmt) if start == end else f"{start.strftime(fmt)} {end.strftime(fmt)}"
        self._load(text)

    def on_button_pressed(self, event):
        bid = event.button.id or ""
        if bid == "step-back":
            self.action_step(-1)
        elif bid == "step-next":
            self.action_step(1)
        elif (event.button.name or "").startswith("crumb-"):
            self.action_back(to=int(event.button.name.removeprefix("crumb-")))
        elif bid == "group":
            self.action_pick_group()
        elif bid == "cal":
            self.action_calendar()
        elif bid.startswith("chip-"):
            self.action_quick(bid.removeprefix("chip-"))

    @property
    def view(self):
        """What is remembered per account: columns shown and their order, pins,
        sort, grouping. Two accounts' columns may differ, so each keeps its own."""
        return self.state.setdefault("views", {}).setdefault(self.source["key"], {})

    def _restore_view(self):
        """This account's remembered view over its settings."""
        self._apply_visible()
        self.sort_key, self.reverse = self.layout.sort
        # the sort after the first: [(key, reverse), ...], set in S; remembered
        self.then = []
        remembered = self.view.get("sorts")
        if isinstance(remembered, list) and remembered:
            try:
                keys = [(str(k), bool(r)) for k, r in remembered]
                self.sort_key, self.reverse = keys[0]
                self.then = keys[1:]
            except (TypeError, ValueError):
                pass
        # the grouping: name columns ticked, (ALL,) for everything, () for none
        self.by = self.layout.parse_group("+".join(self.view.get("by") or [])) or ()
        self.last_by = self.layout.parse_group("+".join(self.view.get("last_by") or [])) or self.by
        # pinned columns: what F picked (remembered), else the settings' pin
        picked = self.view.get("pin")
        self.pin = ([c for c in picked if c in self.layout.names] if isinstance(picked, list)
                    else list(self.layout.pin))
        self.pin_on = bool(self.view.get("pin_on", True))

    def action_reload(self):
        """Re-read settings.json and fetch the period again, past the cache; r also
        sweeps every ad's status."""
        self.status_full = True
        self.layout = self.make_layout(self.source)
        self._apply_visible()
        for src in self.sources:                      # a new settled_days applies at once
            src["store"].settled_days = getattr(self.layout, "settled_days", src["store"].settled_days)
        self._banner()
        # a grouping by a column settings.json no longer has: drop that column
        self._set_group(self.layout.ordered_group([c for c in self.by if c in self.layout.names
                                                    or c == ALL]))
        # the grouping g brings back too, or g would restore a column that is gone
        self.last_by = self.layout.ordered_group([c for c in self.last_by if c in self.layout.names
                                                  or c == ALL])
        self.view["last_by"] = list(self.last_by)
        self.save(self.state)
        # and a drill-down's conditions on it: kept, they would hide every row
        keep = set(self.layout.names) | {DATE}
        gone = sorted(set(self.where) - keep)
        self.where = {k: v for k, v in self.where.items() if k in keep}
        for level in self.levels:
            level["where"] = {k: v for k, v in level["where"].items() if k in keep}
            level["by"] = tuple(c for c in level["by"] if c in keep or c == ALL)
            if any(k in level["crumb"].split(" ")[0:1] or k in level["crumb"] for k in gone):
                level["crumb"] += " (gone)"
        if gone:
            self._crumbs()
        if not self.layout.key_of(self.sort_key.removeprefix("n:")) and self.sort_key != "count":
            self.sort_key, self.reverse = self.layout.sort
        self.then = [(k, r) for k, r in self.then
                     if self.layout.key_of(k.removeprefix("n:")) or k == "count"]
        if self.words:
            self._load(" ".join(self.words), refetch="recent")
        if gone:                                      # after the load, which clears problems
            self._status(f"{', '.join(gone)} left settings.json: the drill-down no longer "
                         "narrows by it")

    def action_refetch_all(self):
        """R: every day of the period fetched again, settled or not."""
        if self.words:
            self._load(" ".join(self.words), refetch="all")

    # ---- filter, group, sort -----------------------------------------------------
    def _columns(self, cols, every=None, widths=None):
        """(Re)build the columns for what is shown now -- a textual column only
        ever grows, so one long name would otherwise keep it wide for good.
        every: all the columns, before any are hidden by a sideways scroll --
        the number keys and their superscripts count those, so they never move.
        widths: each column's widest cell, measured before the rows go in. Given
        as a fixed width, so the table is drawn at its final widths at once:
        left to textual, a column is drawn at its header's width until textual
        measures the rows, then widens and pushes the rest aside."""
        every = list(every or cols)
        table = self.query_one(DataTable)
        # no fixed row over an empty table: textual looks the fixed row up and
        # dies (KeyError: None) when there is none -- set again once rows are in
        table.fixed_rows = 0
        table.clear(columns=True)
        limits = self.layout.limits
        currency = self.source.get("currency", "")
        self.shown_cols = every                       # every column, hidden by a scroll or not
        for col in cols:
            label = header_label(col, limits, currency)
            # its number key, small, in front: ¹Geo ²Type ... ⁰ is the tenth
            if col.key[2:] in self.pins and col.kind == "name":
                label = f"{icon('pin') or '▪ '}{label}"      # pinned: stays on a sideways scroll
            # the sorted columns wear an arrow -- the 2nd, 3rd ... its place too
            order = self._sort_order()
            if col.key in order:
                place, rev = order[col.key]
                label = f"{label} {'↓' if rev else '↑'}{SUPERSCRIPT[place % 10] if place > 1 else ''}"
            else:
                label = f"{label}  "
            label = Text(label, justify="left" if col.kind == "name" else "right")
            if col.kind == "share":
                table.add_column(label, key=col.key, width=SHARE_WIDTH + 1)
                continue
            width = max(label.cell_len, (widths or {}).get(col.key, 0))
            if col.key == f"n:{STATUS}":                  # room for any status before it arrives
                width = max(width, STATUS_WIDTH)
            table.add_column(label, key=col.key, width=width)

    def _shown(self):
        """(columns, records sorted, their total, how many rows they hold)"""
        query = self.query_one("#filter", Input).value
        keep = [r for r in self.rows if self._in_scope(r) and matches(r, query)]
        metrics = self.layout.metrics
        by = grouping(self.by, self.daily)
        total = derive(combine(keep, label="TOTAL"), metrics)
        total["share"] = 1.0 if keep else None
        # on copies: a formula never writes over the counts it is worked out from
        recs = with_share([derive(dict(r), metrics) for r in group(keep, by)], total["billed"])
        cols = self.layout.columns(recs, by, self.daily)
        shown = {c.key for c in cols}
        if self.sort_key not in shown:                          # its column dropped out
            self.sort_key, self.reverse = self.layout.sort
        # first key decides, the next breaks its ties, and so on: sort by the last
        # first -- each sort is stable, so the earlier keys win
        for key, rev in reversed(self._sorts(shown)):
            recs = ordered(recs, "billed" if key == "share" else key, rev)
        return cols, recs, total, len(keep)

    def _sorts(self, shown=None):
        """[(key, reverse), ...]: the first, then each tie-breaker -- only keys shown."""
        keys = [(self.sort_key, self.reverse)] + [(k, r) for k, r in self.then
                                                  if k != self.sort_key]
        return [(k, r) for k, r in keys if shown is None or k in shown]

    def _sort_order(self):
        return {k: (i + 1, r) for i, (k, r) in enumerate(self._sorts())}

    def _redraw(self):
        table = self.query_one(DataTable)
        row = table.cursor_row
        here = grouping(self.by, self.daily)
        was = self._identity(self.recs[row], here) if 0 <= row < len(self.recs) else None
        every, recs, total, kept = self._shown()
        cols = self._visible(every)
        # the total of exactly what is shown -- it follows the filter -- first,
        # held in place on top so it stays in view however far you scroll
        self.recs = [total] + list(recs) if recs else []
        rows = [cells(cols, r, self.layout) for r in self.recs]
        widths = {c.key: max((row[i].cell_len for row in rows), default=0) for i, c in enumerate(cols)}
        self._columns(cols, every, widths)
        for row_cells in rows:
            table.add_row(*row_cells)
        table.fixed_rows = 1 if self.recs else 0      # TOTAL stays on top -- when there is one
        # a refresh keeps your place: the same row, wherever it is now
        here = grouping(self.by, self.daily)          # the grouping now (the same one, on a refresh)
        same = next((i for i, r in enumerate(self.recs) if was and self._identity(r, here) == was), None)
        if same is not None and same > 0:
            self._place(same)
        elif 0 < row < len(self.recs) and was is None:
            self._place(row)
        elif len(self.recs) > 1:
            self._place(1)                            # else the first row under TOTAL
        else:
            self._place(0)
        self._detail(table.cursor_row)
        if not recs:                                  # an empty table says why it is empty
            query = self.query_one("#filter", Input).value.strip()
            failed = self.failed and self._current(self.failed[0]) and not self.rows
            self._message(self.failed[1] if failed else
                          f"Nothing matches '{query}'  ·  esc clears the filter" if self.rows
                          else f"No spend in {self.span}")
        else:
            self._message(None)
        rows = "rows" if self.daily else "ads"
        groups = f" in {len(recs)} groups by {self._group_words()}" if self.by else ""
        self.counts = (f"{kept}/{len(self.rows)} {rows}" + groups
                       + (f"  ·  {icon('daily')}Daily" if self.daily else ""))
        names = {c.key: c.header for c in every}       # hidden by a scroll, still the sort
        self.sort_header = " · ".join(f"{names.get(k, k)} {'↓' if r else '↑'}"
                                      for k, r in self._sorts(set(names)))
        sorts = self._sorts()
        if self.view.get("sorts") != [list(x) for x in sorts]:       # remembered for next time
            self.view["sorts"] = [list(x) for x in sorts]
            self.save(self.state)
        self._status(self.problem)
        self._cards()

    # ---- the summary cards -----------------------------------------------------
    def _before(self):
        """The period just before the one shown, as long: 7d -> the 7 days before."""
        if not self.dates:
            return None
        start, end = self.dates
        n = (end - start).days + 1
        return start - dt.timedelta(days=n), start - dt.timedelta(days=1)

    def _prev_total(self):
        """What is shown -- the same scope and filter -- summed over the period
        before; None when its days are not here yet (they are fetched behind)."""
        before = self._before()
        if before is None:
            return None
        store = self.source["store"]
        a, b = map(str, before)
        need = store.missing(a, b)
        if need:
            asked = (self.source["key"], a, b)
            if asked not in self.prev_asked:          # once: a failure is not retried in a loop
                self.prev_asked.add(asked)
                self._fetch_before(store, need)
            return None
        rows = prepare(store.rows(a, b, False, self.layout.fee), self.layout, store.statuses() or {})
        query = self.query_one("#filter", Input).value
        keep = [r for r in rows if self._in_scope(r) and matches(r, query)]
        return combine(keep, label="TOTAL") if keep else {}

    @work(thread=True, group="before")
    def _fetch_before(self, store, need):
        try:
            store.fetch(need)
        except Exception:                             # the cards just go without a change
            log.exception("fetching the period before")
        self.call_from_thread(self._cards)

    def _cards(self):
        """One card per summary metric: its total over what is shown, and its
        change on the period before -- teal when it got better (a cost down, a
        rate up), peach when worse; green and red stay the targets' verdicts."""
        box = self.query_one("#cards", Horizontal)
        cards = self.layout.cards if self.recs else []
        box.display = bool(cards) and self.size.height >= 28    # a short screen keeps its rows
        if not box.display:
            return
        now = derive(dict(self.recs[0]), cards)
        prev = self._prev_total()
        prev = derive(dict(prev), cards) if prev else prev
        sym = SYMBOL.get(self.source.get("currency", ""), "")
        n = (self.dates[1] - self.dates[0]).days + 1
        widgets = []
        for c in cards:
            v = now.get(c.key)
            shown = "–" if v is None else (sym if c.kind in ("money", "cost") else "") + number(v, c.kind)
            text = Text(f"{c.header}\n", style="#a6adc8")
            text.append(f"{shown}\n", style="bold #ffffff")
            p = prev.get(c.key) if prev else None
            if prev is None:
                text.append("… the period before", style="#6c7086")
            elif v is None or not p:
                text.append(f"– no {n}d before to compare", style="#6c7086")
            else:
                change = (v - p) / abs(p)
                up = change >= 0
                better = None if c.key == "billed" else (not up if c.kind in ("money", "cost") else up)
                colour = "#a6adc8" if better is None else ("#94e2d5" if better else "#fab387")
                text.append(f"{'▲' if up else '▼'} {abs(change) * 100:.1f}%", style=f"bold {colour}")
                text.append(f"  vs {n}d before", style="#6c7086")
            widgets.append(text)
        have = list(box.query(".card"))
        if len(have) == len(widgets):                 # the same cards: new words, no flicker
            for card, text in zip(have, widgets):
                card.update(text)
        else:
            box.remove_children()
            box.mount_all([Static(t, classes="card") for t in widgets])

    def _status(self, problem):
        """The line under the detail: a problem first, in red, until it is fixed;
        then the period, what is shown, currency, sort and how fresh it is."""
        self.problem = problem
        # the dates are on top and the currency in the headers: not repeated here;
        # columns scrolled off on the left come early, so a narrow screen keeps it
        parts = [f"{chevron(False)} {self.hidden} column{'s' if self.hidden != 1 else ''} left" if self.hidden else "",
                 getattr(self, "counts", ""),
                 f"Sort: {getattr(self, 'sort_header', '')}"]
        line = Text("  ·  ".join(p for p in parts if p))
        fresh = self._freshness()                     # first, so a narrow screen never cuts it
        if fresh:
            line = Text.assemble((fresh, "bold #b4befe"), "  ·  ", line)
        if self.busy:
            frame = SPINNER[self.frame % len(SPINNER)]
            line = Text.assemble((f"{frame} {self.busy}", "bold #cba6f7"), "  ·  ", line)
        if problem:
            line = Text.assemble((f"{icon('error') or '✗ '}{problem}", "bold #f38ba8"), "  ·  ", line)
        self.query_one("#summary", Static).update(line)

    def _freshness(self):
        """When the numbers shown were fetched (adglance_store.freshness), with its icon."""
        if not self.dates or not self.rows:
            return ""
        text, settled = freshness(self.source["store"], *map(str, self.dates))
        return f"{icon('lock' if settled else 'clock')}{text}" if text else ""

    def _busy(self, text):
        """A fetch on screen: text with a spinner at the front of the status
        line until it ends (None)."""
        self.busy = text
        if text and not self.spinner:
            self.spinner = self.set_interval(0.08, self._spin)
        elif not text and self.spinner:
            self.spinner.stop()
            self.spinner = None
        self._status(self.problem)

    def _spin(self):
        self.frame += 1
        self._status(self.problem)

    def _message(self, text):
        """In place of the table: why there is nothing to show (None: the table)."""
        message = self.query_one("#message", Static)
        message.update(Text(text or ""))             # plain: config names carry [brackets]
        message.display = bool(text)
        self.query_one(DataTable).display = not text

    def _banner(self):
        """settings.json's problems, on top until the file is fixed (then r)."""
        banner = self.query_one("#banner", Static)
        problems = self.layout.problems
        text = "" if not problems else (
            f"{icon('warning') or '✗ '}{problems[0]}" + (f"  (+{len(problems) - 1} more)" if len(problems) > 1 else "")
            + "  ·  the rest of settings.json is in use  ·  fix it, then r")
        banner.update(Text(text))
        banner.set_class(bool(problems), "shown")

    @staticmethod
    def _identity(rec, by=None):
        """What a row is, whatever its place: an ad by its names and ad; a group
        by the values of the columns it is grouped by, and nothing else -- a new
        ad joining it (even one that makes another column mixed) keeps it the
        same row."""
        if rec.get("total"):
            return ("TOTAL",)
        if rec.get("members"):
            keys = [k for k in (by or ()) if k != ALL]
            return ("group",) + tuple((k, rec["names"].get(k, "")) for k in keys)
        # not the status: it changes under a row without making it another row
        return (tuple(sorted((k, v) for k, v in rec["names"].items() if k != STATUS)), rec.get("ids"))

    def _detail(self, index):
        rec = self.recs[index] if 0 <= index < len(self.recs) else None
        # fixed height: no layout pass for it, which every cursor move would pay
        col = next((c for c in getattr(self, "shown_cols", []) if c.key == self.cur_key), None)
        self.query_one("#detail", Static).update(
            Text(detail(rec, self.source.get("currency", ""), col, self.layout.labels)
                 if rec else ""), layout=False)

    def on_data_table_cell_selected(self, event):
        """Enter (or a click on the cell already under the cursor): open the row --
        a group goes a level in, an ad (or TOTAL) shows its menu."""
        row = event.coordinate.row
        rec = self.recs[row] if 0 <= row < len(self.recs) else None
        if rec and rec.get("members") and not rec.get("total"):
            self.action_drill()
        else:
            self._menu(row)

    def action_nothing(self):
        pass

    def on_data_table_cell_highlighted(self, event):
        """The cursor moved: its row's detail, and remember which column it is on."""
        tables = self.query(DataTable)
        if not tables:
            return                                    # closing: the table is already gone
        cols = list(tables.first().columns)
        if 0 <= event.coordinate.column < len(cols):
            self.cur_key = str(cols[event.coordinate.column].value)
        self._detail(event.coordinate.row)

    def _menu(self, row):
        rec = self.recs[row] if 0 <= row < len(self.recs) else None
        if rec is None:
            return
        cols, *_ = self._shown()
        shown = self.recs[0]["billed"] if self.recs and self.recs[0].get("total") else 0
        self.push_screen(ActionScreen(rec, cols, self.span, tuple(map(str, self.dates)),
                                      self.source.get("currency", ""), self.source.get("link"),
                                      shown))



    def on_input_changed(self, event):
        """The filter redraws as you type -- after a pause of a key or two, so a
        long Daily table is not summed again for every letter."""
        if event.input.id == "filter" and self.span:
            if self.typing:
                self.typing.stop()
            self.typing = self.set_timer(0.12, self._redraw)

    def _keep_current(self):
        """Once a minute: if any unsettled day shown was fetched over STALE ago,
        fetch again -- so the numbers are never much more than STALE old. Not
        while a fetch is out, a date box is being typed in or a dialog is open
        (the filter box does not count: a refresh leaves it alone)."""
        if not self.words or not self.dates or self.busy or self.screen is not self.screen_stack[0]:
            return
        if isinstance(self.focused, Input) and self.focused.id in ("from", "to"):
            return
        start, end = map(str, self.dates)
        try:                                          # after midnight, "today" or "7d" is new days
            moved = self.window(self.words) != (start, end)
        except SystemExit:
            moved = False
        if moved or self.source["store"].missing(start, end, recent_since=time.time() - STALE):
            self._load(" ".join(self.words))

    def on_data_table_header_selected(self, event):
        self._sort_by(str(event.column_key.value))

    def action_sort_here(self):
        """o: sort by the column the cursor is on (again: the other way)."""
        if self.cur_key:
            self._sort_by(self.cur_key)

    def action_pick_sort(self):
        """O: every column shown, to pick the sort from (past the tenth too)."""
        cols = getattr(self, "shown_cols", [])
        limits = self.layout.limits
        currency = self.source.get("currency", "")
        choices = [(header_label(c, limits, currency), c.key) for c in cols]
        self.push_screen(SortScreen(self, choices))

    def _natural(self, key):
        """A column's first direction: costs cheapest first -- a money column that
        is a rate (CPM: billed / impressions) is a cost too; names A-Z; the rest
        largest first. True = largest first."""
        col = next((c for c in self.layout.columns(self.recs, (), self.daily) if c.key == key), None)
        kind = col.kind if col else "count"
        rate = kind == "money" and "/" in (col.formula or "")
        return not (kind in ("name", "cost") or rate)

    def _sort_by(self, key):
        """Sort by one column alone: the one already sorted (alone) flips; another
        starts its own way. Any tie-breakers from O go."""
        if key == self.sort_key and not self.then:
            self.reverse = not self.reverse
        else:
            self.sort_key, self.reverse = key, self._natural(key)
        self.then = []
        if self.span:
            self._redraw()

    def sort_add(self, key):
        """O, + or space: the column as the next tie-breaker; one already in the
        sort flips its direction instead."""
        sorts = self._sorts()
        keys = [k for k, _ in sorts]
        if key in keys:
            i = keys.index(key)
            sorts[i] = (key, not sorts[i][1])
        else:
            sorts.append((key, self._natural(key)))
        (self.sort_key, self.reverse), self.then = sorts[0], sorts[1:]
        if self.span:
            self._redraw()

    def sort_remove(self, key):
        """O, x: the column out of the sort (the last one left stays)."""
        sorts = [s for s in self._sorts() if s[0] != key] or self._sorts()
        (self.sort_key, self.reverse), self.then = sorts[0], sorts[1:]
        if self.span:
            self._redraw()

    def _choices(self, picker):
        """The values a picker offers, in order."""
        if picker == "account":
            return [key for _, key in self._account_options(self.source["platform"], add=False)]
        return list(dict.fromkeys(src["platform"] for src in self.sources))

    def action_next_account(self):
        """a: the next ad account, across every platform (A: the list)."""
        if len(self.sources) < 2:
            self.notify("only one account -- A, then + Add account… adds another", timeout=4)
            return
        at = next((i for i, src in enumerate(self.sources) if src is self.source), -1)
        self._switch(self.sources[(at + 1) % len(self.sources)])

    def action_cycle(self, picker):
        """The picker's next choice (s source, a account, g group)."""
        select = self.query_one(f"#{picker}", Select)
        choices = self._choices(picker)
        if len(choices) < 2:
            self.notify(f"only one {'source' if picker == 'platform' else picker} to pick", timeout=2)
            return
        at = choices.index(select.value) if select.value in choices else -1
        select.value = choices[(at + 1) % len(choices)]

    def action_open(self, picker):
        """The picker's list, open, to choose from (S, A, G)."""
        select = self.query_one(f"#{picker}", Select)
        select.focus()
        select.expanded = True

    # ---- grouping ------------------------------------------------------------------
    def _group_words(self):
        return " + ".join(self.layout.label(c) for c in self.by)

    def _group_label(self):
        return f"{icon('group')}Group: {self._group_words() or 'none'}"

    def _set_group(self, by):
        """Group by these columns (() none, (ALL,) everything); remembered."""
        self.by = tuple(by)
        if self.by:
            self.last_by = self.by
        self.view.update(by=list(self.by), last_by=list(self.last_by))
        self.save(self.state)
        button = self.query_one("#group", Button)
        button.label = self._group_label()
        button.set_class(bool(self.by), "on")
        button.refresh(layout=True)                   # a longer label needs a wider button
        if self.span:
            self._redraw()

    def action_toggle_group(self):
        """g: grouping off, or back to the last one ticked."""
        if self.by:
            self._set_group(())
        elif self.last_by:
            self._set_group(self.last_by)
        else:
            self.action_pick_group()

    # ---- shown / hidden columns ---------------------------------------------------
    def _apply_visible(self):
        """What h / H picked (remembered), over settings.json's columns and show;
        keys settings.json no longer has drop out."""
        # name columns: what h / H picked, remembered with the view; the number
        # columns are the settings' show -- which H writes -- in its order
        picked = self.view.get("visible")
        if isinstance(picked, list):
            names = {f"n:{c}" for c in self.layout.names}
            metrics = {k for k in self.layout.visible if not k.startswith("n:")}
            self.layout.visible = {k for k in picked if k in names} | metrics
        # and the order shift+← → made among the names: keys it does not know keep their place after
        order = self.view.get("order")
        if isinstance(order, list):
            rank = {k: i for i, k in enumerate(order)}
            names = sorted(self.layout.names,
                           key=lambda c: (rank.get(f"n:{c}", len(rank)), self.layout.names.index(c)))
            self.layout.names[:] = names

    def action_move_col(self, step):
        """shift+← / shift+→: the cursor's column one place left / right, past
        the next shown column -- names among names, metrics among metrics. The
        cursor goes with it; the order is remembered."""
        key = self.cur_key
        if not key:
            return
        if key.startswith("n:"):
            zone = [f"n:{c}" for c in self.layout.names]
        else:
            zone = [c.key for c in self.layout.catalog]
        if key not in zone:
            return                                    # Date, Ads: they have no place to move
        shown = [k for k in zone if k in self.layout.visible or k == key]
        at = shown.index(key)
        to = at + step
        if not 0 <= to < len(shown):
            return
        neighbour = shown[to]
        zone.remove(key)
        zone.insert(zone.index(neighbour) + (1 if step > 0 else 0), key)
        if key.startswith("n:"):
            self.layout.names[:] = [k[2:] for k in zone]
            self.view["order"] = [f"n:{c}" for c in self.layout.names]
            self.save(self.state)
        else:                                         # a number column: its place in show
            by_key = {c.key: c for c in self.layout.catalog}
            self.layout.catalog[:] = [by_key[k] for k in zone]
            if not self._write_show([c.key for c in self.layout.catalog if c.key in self.layout.visible]):
                return
        if self.span:
            self._redraw()

    def _reset_view(self):
        """H, r: the settings' columns again -- this account's show taken out of
        its file, so the platform's own set (or settings.json's) applies."""
        for k in ("visible", "order"):
            self.view.pop(k, None)
        self.save(self.state)
        self._write_show(None)

    def _write_show(self, show):
        """This account's number columns, written to its own file (None: taken
        out); the screen reads the settings again. False when it cannot write."""
        try:
            settings.write_option(self.source["platform"], self.source["account"], "show", show)
        except (OSError, ValueError) as e:
            self.notify(f"cannot write this account's file ({e}) -- , opens it", severity="error")
            return False
        self.layout = self.make_layout(self.source)
        self._apply_visible()
        if self.span:
            self._redraw()
        return True

    def _set_visible(self, keys):
        """Name columns into the view; number columns into the account's show,
        keeping the order they had and adding new ones at the end."""
        keys = set(keys)
        self.view["visible"] = sorted(k for k in keys if k.startswith("n:"))
        self.save(self.state)
        now = [c.key for c in self.layout.catalog if c.key in self.layout.visible]
        show = [k for k in now if k in keys] + [c.key for c in self.layout.catalog
                                               if c.key in keys and c.key not in now]
        if show != now:
            self._write_show(show)
            return
        self.layout.visible = {k for k in self.layout.visible if not k.startswith("n:")} | {
            k for k in keys if k.startswith("n:")}
        if self.span:
            self._redraw()

    def action_hide_here(self):
        """h: hide the column the cursor is on; the cursor steps to the next one."""
        key = self.cur_key
        if not key or key not in self.layout.visible:
            return                                    # Date and Ads come and go with d / grouping
        every = [c.key for c in getattr(self, "shown_cols", [])]
        at = every.index(key) if key in every else 0
        rest = [k for k in every if k != key and k in self.layout.visible]
        if not rest:
            self.notify("the last column stays", timeout=2)
            return
        after = [k for k in every[at + 1:] if k in rest] or [k for k in every[:at] if k in rest][-1:]
        self.cur_key = after[0]
        self._set_visible(self.layout.visible - {key})
        self.notify(f"hid {self.layout.label(key[2:]) if key.startswith('n:') else key}"
                    "  ·  H brings it back", timeout=2)

    def action_pick_columns(self):
        """H: every column there is, searchable, ticked when shown -- the
        platform's set ticked from the start; each tick applies at once."""
        lay = self.layout
        cols = [(f"n:{c}", lay.label(c), "name", True) for c in lay.names]
        for c in lay.catalog:
            what = c.formula or "part of the spend shown"
            ok = lay.available(c)
            cols.append((c.key, c.header, what if ok else f"{what}  · not on {lay.platform_title}", ok))
        title = f"Columns  ·  {self.source['label']}"
        self.push_screen(ColumnsScreen(title, cols, lay.visible, self._set_visible,
                                       self._reset_view, self._new_metric))

    def _new_metric(self):
        total = self.recs[0] if self.recs else None
        self.push_screen(MetricScreen(total, self.layout.provides, self.layout.platform_title,
                                      self.source.get("currency", ""), self._save_metric))

    def _save_metric(self, name, formula, kind):
        """The new metric into the account's own file, and shown at the end."""
        mid = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "metric"
        taken = {c.key for c in self.layout.catalog}
        base, n = mid, 2
        while mid in taken:
            mid, n = f"{base}_{n}", n + 1
        try:
            mine = settings.read_option(self.source["platform"], self.source["account"], "metrics", {})
            mine = dict(mine) if isinstance(mine, dict) else {}
            mine[mid] = {"name": name, "formula": formula, "format": kind}
            settings.write_option(self.source["platform"], self.source["account"], "metrics", mine)
        except (OSError, ValueError) as e:
            self.notify(f"cannot write this account's file ({e}) -- , opens it", severity="error")
            return
        now = [c.key for c in self.layout.catalog if c.key in self.layout.visible]
        if self._write_show(now + [mid]):
            self.cur_key = mid
            self.notify(f"added {name}  ·  {formula}", timeout=4)

    # ---- pinned columns ------------------------------------------------------------
    @property
    def pins(self):
        # a level's own names (Campaign, Ad group) stay in view like a pin: they are the rows
        lead = [c for c in LEVELS[2] if c in self.by]
        return lead + [c for c in self.pin if c not in lead] if self.pin_on else lead

    def _movable(self, cols):
        return [c for c in cols if not (c.kind == "name" and c.key[2:] in self.pins)]

    def _visible(self, cols):
        """The columns in their order, less the first `self.hidden` unpinned ones:
        a sideways scroll by columns that leaves the pinned ones where they are."""
        movable = self._movable(cols)
        self.hidden = max(0, min(self.hidden, len(movable) - 1))
        gone = set(id(c) for c in movable[:self.hidden])
        return [c for c in cols if id(c) not in gone]

    def _place(self, row):
        """Put the cursor on this row, on the column it was on (by key)."""
        table = self.query_one(DataTable)
        keys = [str(k.value) for k in table.columns]
        col = keys.index(self.cur_key) if self.cur_key in keys else 0
        table.move_cursor(row=max(0, row), column=col, animate=False)
        table.scroll_to(x=0, animate=False)           # sideways is ours (hidden columns)

    def action_copy_cell(self):
        """ctrl+c -- the one copy key: the cursor's cell, as shown, to the
        clipboard -- through the terminal, so over SSH it lands on this Mac."""
        table = self.query_one(DataTable)
        try:
            value = table.get_cell_at(table.cursor_coordinate)
        except Exception:
            return
        text = (value.plain if isinstance(value, Text) else str(value)).strip()
        text = text.replace("Σ ", "")
        text = text.lstrip("█▉▊▋▌▍▎▏ ")              # a Share cell: its figure, not its bar
        for glyph in [*GLYPHS.values(), *(g for pair in CHEVRONS.values() for g in pair)]:   # decoration
            text = text.replace(glyph, "").strip()
        if not text or text in ("–", "·"):
            return
        self.copy_to_clipboard(text)
        self.notify(f"{icon('copy')}copied {text}", timeout=2)

    def action_row_edge(self, direction):
        """Cmd+↓ / Cmd+↑: the last row / the first row under TOTAL."""
        if len(self.recs) > 1:
            self._place(len(self.recs) - 1 if direction > 0 else 1)

    def action_cell_step(self, step):
        """← / →: the cell before / after, across every column in its order.
        A hidden column comes back when the cursor reaches it; going right past
        the screen hides unpinned columns on the left, as few as it takes.
        home / end (±99): the first / the last column. Most moves only move the
        cursor -- the table is rebuilt only when a column hides or shows."""
        every = getattr(self, "shown_cols", [])
        if not every:
            return
        keys = [c.key for c in every]
        at = keys.index(self.cur_key) if self.cur_key in keys else 0
        target = 0 if step == -99 else len(keys) - 1 if step == 99 else at + step
        target = max(0, min(len(keys) - 1, target))
        self.cur_key = keys[target]
        movable = [c.key for c in self._movable(every)]
        table = self.query_one(DataTable)
        row = table.cursor_row
        hidden = 0 if step == -99 else self.hidden
        if self.cur_key in movable and movable.index(self.cur_key) < hidden:
            hidden = movable.index(self.cur_key)      # back over the hidden ones
        if hidden != self.hidden:
            self.hidden = hidden
            self._redraw()
        # past the right edge: hide unpinned columns on the left until it shows
        room = table.size.width - 2
        while True:
            upto = 0
            for k, c in table.columns.items():
                upto += c.get_render_width(table)
                if str(k.value) == self.cur_key:
                    break
            hideable = (movable.index(self.cur_key) > self.hidden if self.cur_key in movable
                        else self.hidden < len(movable) - 1)
            if upto <= room or not hideable:
                break
            self.hidden += 1
            self._redraw()
        self._place(row)

    def _set_pin(self, cols, on=True):
        self.pin = [c for c in self.layout.names if c in cols]
        self.pin_on = on
        self.view.update(pin=self.pin, pin_on=self.pin_on)
        self.save(self.state)
        if self.span:
            self._redraw()

    def action_toggle_pin(self):
        """f: pinning off, or back on."""
        self._set_pin(self.pin, not self.pin_on)
        self.notify(f"pinned: {', '.join(self.layout.label(c) for c in self.pins) or 'none'}", timeout=2)

    def action_pick_pin(self):
        """F: tick the name columns to keep in view on the left."""
        self.push_screen(PinScreen(self.layout.names, self.layout.labels, self.pin,
                                   lambda cols: self._set_pin(cols, True)))

    def action_pick_group(self):
        """G or the button: tick the columns to group by; rows merge as you tick."""
        self.push_screen(GroupScreen(self.layout.names, self.layout.labels, self.by,
                                     lambda cols: self._set_group(self.layout.ordered_group(cols))))

    def action_help(self):
        self.push_screen(KeysScreen())

    def action_focus_period(self):
        self.query_one("#from", Input).focus()

    def action_focus_filter(self):
        self.query_one("#filter", Input).focus()

    def action_clear(self):
        """esc, the one back key: undo the last thing -- a filter is cleared
        first; with none, up a level. (Dialogs close on esc themselves.)"""
        box = self.query_one("#filter", Input)
        if not box.value and self.levels:
            self.action_back()
            return
        box.value = ""
        self.query_one(DataTable).focus()

    # ---- drill-down ----------------------------------------------------------------
    def _in_scope(self, r):
        """In the drilled-into group: its columns match, and every filter of the
        levels above still holds -- so the ads inside are exactly the group's."""
        names = r["names"]
        return (all(names.get(k) == v for k, v in self.where.items()
                    if k != DATE or self.daily)      # a day picked only binds while Daily is on
                and all(matches(r, f) for f in self.filters))

    def action_level(self, n):
        """1 / 2 / 3: the campaigns, their ad groups, or the ads, over what is
        in view (a drill-down keeps its scope). Enter goes a level down."""
        self._set_group(LEVELS[n])
        self._group_button()
        self._place(1)

    def action_drill(self):
        """space: into the group under the cursor -- its ads, a level down -- or,
        on an ad, its menu."""
        table = self.query_one(DataTable)
        if self.focused is not table:
            return
        row = table.cursor_row
        rec = self.recs[row] if 0 <= row < len(self.recs) else None
        if rec is None:
            return
        if rec.get("total"):
            return                                    # TOTAL is everything shown, not a group
        if not rec.get("members"):
            return                                    # an ad is the bottom: Enter is its menu
        by = grouping(self.by, self.daily)
        if rec.get("total"):
            step = {}
        else:
            # a blank value is a value too: Stage blank (a project) is its own group
            step = {k: rec["names"].get(k, "") for k in by if k != ALL}
        crumb = " · ".join(f"{self.layout.label(k)} {v or '–'}" for k, v in step.items()) or (
            "All" if self.by == (ALL,) or rec.get("total") else "group")
        box = self.query_one("#filter", Input)
        self.levels.append({"where": dict(self.where), "filters": list(self.filters),
                            "by": self.by, "filter": box.value,
                            "sort": (self.sort_key, self.reverse), "then": list(self.then),
                            "row": row, "crumb": crumb,
                            "at": self._identity(rec, by)})
        self.where = {**self.where, **step}
        if box.value.strip():                          # the filter goes down with you
            self.filters = self.filters + [box.value.strip()]
        # a level down: a campaign's ad groups (from level 1), else the ads themselves
        self.by = LEVELS[2] if set(self.by) == set(LEVELS[1]) else ()
        box.value = ""
        self.sort_key, self.reverse = (f"n:{DATE}", False) if self.daily else self.layout.sort
        self.then = []
        self._group_button()
        self._crumbs()
        self._redraw()
        self._place(1)

    def action_back(self, to=None):
        """backspace: up one level (to: up to that many levels kept), as it was."""
        to = len(self.levels) - 1 if to is None else to
        if not self.levels or to >= len(self.levels):
            return                                    # already here
        level = None
        while len(self.levels) > to:
            level = self.levels.pop()
        self.where, self.by, self.filters = level["where"], level["by"], level["filters"]
        self.sort_key, self.reverse = level["sort"]
        self.then = level.get("then", [])
        self.query_one("#filter", Input).value = level["filter"]
        self._group_button()
        self._crumbs()
        self._redraw()
        # back on the row you went in from, wherever it is now
        here = grouping(self.by, self.daily)
        at = next((i for i, r in enumerate(self.recs) if self._identity(r, here) == level["at"]),
                  level["row"])
        self._place(at)
        self.query_one(DataTable).focus()

    def _crumbs(self):
        """The path down: All ❯ one crumb per level; a crumb takes you back there."""
        bar = self.query_one("#crumbs", Horizontal)
        bar.remove_children()
        bar.set_class(bool(self.levels), "shown")
        if not self.levels:
            return
        # each crumb: where that level was, and the filter it had when you went down
        names = ["All"] + [lvl["crumb"] for lvl in self.levels]
        parts = [name + (f'  "{self.levels[i]["filter"].strip()}"'
                         if i < len(self.levels) and self.levels[i]["filter"].strip() else "")
                 for i, name in enumerate(names)]
        widgets = []
        for i, label in enumerate(parts):
            if i:
                widgets.append(Static(Text(f" {chevron()} ", style=f"bold {MAUVE}")))
            here = i == len(parts) - 1
            # a name, not an id: the old crumbs are still leaving while these arrive
            widgets.append(Button(label, name=f"crumb-{i}", compact=True,
                                  classes="crumb here" if here else "crumb"))
        widgets.append(Static("   esc goes back", classes="hint"))
        bar.mount_all(widgets)

    def _group_button(self):
        button = self.query_one("#group", Button)
        button.label = self._group_label()
        button.set_class(bool(self.by), "on")
        button.refresh(layout=True)
