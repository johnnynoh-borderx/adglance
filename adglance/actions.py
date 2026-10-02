"""adglance's action menu: Enter (or a click) on a row.

One ad: copy its name, its ID or its campaign's name, copy the link to it in the
platform's own manager (Ads Manager, filtered to this ad -- the preview is one
click there), or copy the row as one line to paste into a message. Copies go
through the terminal (OSC 52), so over SSH they land on this Mac's clipboard.

A group, or TOTAL: its ads, to pick one, or copy all their names at once.

Nothing here changes the account: adglance stays read-only.
"""
from rich.style import Style
from rich.text import Text
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from .style import DATE, SYMBOL, derive, icon, number

CSS = """
ActionScreen { align: center middle; background: #11111b 50%; }
ActionScreen > Vertical { width: auto; max-width: 100; height: auto; max-height: 80%;
                          padding: 1 2; background: #181825; border: round #cba6f7; }
ActionScreen .head { color: #cdd6f4; text-style: bold; }
ActionScreen .sub { color: #7f849c; margin: 0 0 1 0; }
ActionScreen OptionList { background: #181825; border: none; height: auto; max-height: 20;
                          padding: 0; }
ActionScreen OptionList:focus { border: none; background-tint: transparent; }
ActionScreen OptionList > .option-list--option-highlighted,
ActionScreen OptionList:focus > .option-list--option-highlighted { background: #313244; color: #cdd6f4; }
ActionScreen .link { color: #89b4fa; margin: 1 0 0 0; }
"""


def row_line(rec, cols, span, currency):
    """The row as one line to paste: what it is, the period, then each number."""
    names = [v for v in rec["names"].values() if v]
    sym = SYMBOL.get(currency, currency)
    numbers = []
    for c in cols:
        v = rec.get(c.key)
        if c.kind in ("name", "count") or v is None:
            continue
        if c.kind == "share":
            numbers.append(f"{c.header} {v * 100:.0f}%")
        else:
            money = sym if c.kind in ("money", "cost") else ""
            numbers.append(f"{c.header} {money}{number(v, c.kind)}")
    return "  ·  ".join([" ".join(names), span, *numbers])


class ActionScreen(ModalScreen):
    DEFAULT_CSS = CSS
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, rec, cols, span, dates, currency, link, shown=0):
        super().__init__()
        self.shown = shown                            # the spend on screen, for an ad's share
        self.rec, self.cols, self.currency, self.link = rec, cols, currency, link
        self.period = (span, dates)                   # the screen's, for an ad picked from a group
        # a Daily row is one day: its line and its Ads Manager link say that day
        day = rec["names"].get(DATE) if rec.get("names") else None
        self.span, self.dates = (day, (day, day)) if day else (span, dates)
        self.actions = {}                     # option id -> (what was copied, text)

    def _single(self):
        return not self.rec.get("members") or len(self.rec["members"]) == 1 and not self.rec.get("total")

    def compose(self):
        rec = self.rec["members"][0] if self.rec.get("members") and self._single() else self.rec
        with Vertical():
            if self._single():
                yield Static(Text(" · ".join(v for v in rec["names"].values() if v)), classes="head")
                status = (f"\n{rec['raw_status'][1]}"
                          if rec.get("raw_status") else "")
                counts = "  ·  ".join(f"{label} {rec.get(p) or 0:,.0f}" for p, label in (
                    ("impressions", "impr"), ("views", "views"), ("views_6s", "6s"),
                    ("views_100", "complete"), ("follows", "follows"), ("likes", "likes")))
                yield Static(Text(f"{rec['ad_name']}\n{rec['campaign_name']}{status}\n{counts}"),
                             classes="sub")
                url = self.link(rec["campaign_name"], rec["ad_name"], *self.dates) if self.link else None
                ad_id = next(iter(rec.get("ids") or []), "")
                items = [("n", "copy", "Copy ad name", "ad name", rec["ad_name"]),
                         ("i", "id", "Copy ad ID", "ad ID", ad_id),
                         ("c", "campaign", "Copy campaign name", "campaign name", rec["campaign_name"])]
                if url:
                    items.append(("o", "link", "Copy Ads Manager link", "Ads Manager link", url))
                items.append(("s", "row", "Copy this row", "row",
                              row_line(self.rec, self.cols, self.span, self.currency)))
                yield OptionList(*[self._option(*it) for it in items])
                if url:                       # clickable where the terminal does OSC 8
                    yield Static(Text("open in Ads Manager", style=Style(link=url, underline=True)),
                                 classes="link")
            else:
                members = sorted(self.rec["members"], key=lambda m: -m["billed"])
                label = self.rec.get("total") or " · ".join(v for v in self.rec["names"].values() if v)
                yield Static(Text(f"{icon('ads')}{label}  ·  {len(members)} rows"), classes="head")
                yield Static(Text("pick one for its actions"), classes="sub")
                names = "\n".join(sorted({m["ad_name"] for m in members}))
                options = [self._option("n", "copy", "Copy all ad names", "ad names", names),
                           self._option("s", "row", "Copy this row", "row",
                                        row_line(self.rec, self.cols, self.span, self.currency))]
                for i, m in enumerate(members):
                    text = Text.assemble((f"   {' · '.join(v for v in m['names'].values() if v)}", "#cdd6f4"),
                                         (f"   {SYMBOL.get(self.currency, self.currency)}{m['billed']:,.0f}",
                                          "#7f849c"))
                    options.append(Option(text, id=f"member-{i}"))
                    self.actions[f"member-{i}"] = ("member", m)
                yield OptionList(*options)

    def _option(self, key, glyph, label, what, text):
        self.actions[key] = (what, text)
        prompt = Text.assemble((f" {key} ", "bold #fab387"), f" {icon(glyph)}{label}")
        return Option(prompt, id=key)

    def on_mount(self):
        self.query_one(OptionList).focus()

    def on_key(self, event):
        if event.character in self.actions:          # the letter beside an action
            event.stop()
            self._do(event.character)

    def on_option_list_option_selected(self, event):
        self._do(event.option.id)

    def _do(self, key):
        what, text = self.actions[key]
        if what == "member":                          # one ad of the group: its own menu
            one = derive(dict(text), [c for c in self.cols if c.formula])   # its own numbers
            # its share of what is on screen, like every row there
            one["share"] = one["billed"] / self.shown if self.shown else None
            span, dates = self.period
            self.app.push_screen(ActionScreen(one, self.cols, span, dates, self.currency, self.link,
                                              self.shown))
            return
        self.app.copy_to_clipboard(text)
        self.app.notify(f"{icon('copy')}Copied the {what}", timeout=2)
        self.dismiss()
