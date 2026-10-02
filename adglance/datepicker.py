"""A month calendar for adglance's period: pick the start day, then the end day.

It drops down under the From box, over the table, like a web date picker; a
click anywhere outside it closes it. The mouse moves the cursor as it hovers, so
once the start is picked the range follows the pointer.

    arrows        move a day / a week        PgUp PgDn  a month back / forward
    Enter, click  pick the day under the cursor (the first pick is the start,
                  the second the end; the same day twice is one day)
    Esc           close without changing the period

Days after today are dimmed and cannot be picked -- the report has nothing there.
"""
import calendar
import datetime as dt

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget

from .store import today as account_today
from .style import chevron

CELL = 4                                  # each day is 4 columns wide
WIDTH = CELL * 7
HEAD = 2                                  # month line, weekday line

TEXT, DIM, FAINT = "#cdd6f4", "#7f849c", "#45475a"
MAUVE, PEACH, GREEN, RANGE = "#cba6f7", "#fab387", "#a6e3a1", "#313244"


def _shift_month(day, months):
    y, m = divmod(day.year * 12 + day.month - 1 + months, 12)
    return day.replace(year=y, month=m + 1, day=min(day.day, calendar.monthrange(y, m + 1)[1]))


class Calendar(Widget, can_focus=True):
    DEFAULT_CSS = f"Calendar {{ width: {WIDTH}; height: {HEAD + 6 + 2}; }}"
    BINDINGS = [Binding("left", "move(-1)", show=False), Binding("right", "move(1)", show=False),
                Binding("up", "move(-7)", show=False), Binding("down", "move(7)", show=False),
                Binding("pageup", "month(-1)", show=False),
                Binding("pagedown", "month(1)", show=False),
                Binding("enter", "pick", show=False)]

    class Picked(Message):
        def __init__(self, start, end):
            super().__init__()
            self.start, self.end = start, end

    def __init__(self, dates=None, **kw):
        super().__init__(**kw)
        self.today = account_today()
        self.shown = dates                       # the period now in use, drawn until a pick
        self.cursor = dates[1] if dates else self.today
        self.first = None                        # the start, once picked

    def _weeks(self):
        return calendar.Calendar(firstweekday=0).monthdatescalendar(self.cursor.year,
                                                                    self.cursor.month)

    def render(self):
        lo, hi = (sorted((self.first, self.cursor)) if self.first
                  else self.shown or (None, None))
        out = Text()
        title = f"{calendar.month_name[self.cursor.month]} {self.cursor.year}"
        out.append(f" {chevron(False)} ", style=f"bold {PEACH}")
        out.append(title.center(WIDTH - 6), style=f"bold {MAUVE}")
        out.append(f" {chevron()} \n", style=f"bold {PEACH}")
        out.append("".join(f"{d:>3} " for d in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")) + "\n",
                   style=DIM)
        for week in self._weeks():
            for day in week:
                label = f"{day.day:>3} "
                if day.month != self.cursor.month:
                    out.append(" " * CELL)
                    continue
                style = FAINT if day > self.today else TEXT
                if lo and lo <= day <= hi:
                    style += f" on {RANGE}"
                if day in (lo, hi):
                    style = f"bold #11111b on {MAUVE}"
                if day == self.today and day not in (lo, hi):
                    style += f" bold underline {GREEN}"
                if day == self.cursor and self.has_focus:
                    style = f"bold #11111b on {PEACH}"
                out.append(label, style=style)
            out.append("\n")
        out.append("\n" * (6 - len(self._weeks())))
        hint = (f"start {self.first:%m-%d} · now the end day" if self.first
                else "pick the start day")
        out.append("\n" + hint.center(WIDTH), style=DIM)
        return out

    def on_focus(self):
        self.refresh()

    def action_move(self, days):
        self.cursor = min(self.cursor + dt.timedelta(days=days), self.today)
        self.refresh()

    def action_month(self, months):
        self.cursor = min(_shift_month(self.cursor, months), self.today)
        self.refresh()

    def action_pick(self):
        day = self.cursor
        if day > self.today:
            return
        if self.first is None:
            self.first = day
            self.refresh()
            return
        start, end = sorted((self.first, day))
        self.post_message(self.Picked(start, end))

    def _day_at(self, x, y):
        row, col = y - HEAD, x // CELL
        weeks = self._weeks()
        if 0 <= row < len(weeks) and 0 <= col < 7:
            day = weeks[row][col]
            if day.month == self.cursor.month and day <= self.today:
                return day
        return None

    def on_mouse_move(self, event):
        day = self._day_at(event.x, event.y)
        if day and day != self.cursor:
            self.cursor = day
            self.refresh()

    def on_click(self, event):
        x, y = event.x, event.y
        if y == 0:                               # the month line: the chevrons
            if x < 3:
                self.action_month(-1)
            elif x >= WIDTH - 3:
                self.action_month(1)
            return
        day = self._day_at(x, y)
        if day:
            self.cursor = day
            self.action_pick()


class CalendarScreen(ModalScreen):
    """Dismisses with (start, end) dates, or None on Esc or a click outside.
    anchor: the screen (x, y) its top-left corner drops to."""
    DEFAULT_CSS = f"""
    CalendarScreen {{ align: left top; background: transparent; }}
    CalendarScreen > Vertical {{ width: {WIDTH + 6}; height: auto; padding: 1 2;
                                 background: #181825; border: round {MAUVE}; }}
    """
    BINDINGS = [("escape", "dismiss_none", "Close")]

    def __init__(self, dates=None, anchor=(0, 0)):
        super().__init__()
        self.dates, self.anchor = dates, anchor

    def compose(self):
        with Vertical():
            yield Calendar(self.dates)

    def on_mount(self):
        self.query_one(Vertical).styles.offset = self.anchor
        self.query_one(Calendar).focus()

    def on_click(self, event):
        if event.widget is self:                 # outside the box
            self.dismiss(None)

    def on_calendar_picked(self, event):
        self.dismiss((event.start, event.end))

    def action_dismiss_none(self):
        self.dismiss(None)
