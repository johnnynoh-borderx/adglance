"""adglance setup: add an ad account, check it, set its label.

The first run opens straight on the key form; `adglance setup` opens on the
list of accounts. Everything is written to accounts.json (accounts.py), mode
600, which stays yours to edit by hand. A key is checked with one read-only
call before it is kept; nothing is sent anywhere but the platform it is for.

    key form      platform, token, account id  ->  check  ->  account form
    account form  label                        ->  save   ->  the numbers (or the list)
    list          n new  ·  enter edit  ·  x remove  ·  esc done

The key and account forms also open over the numbers, from the account
picker's "+ Add account…" (inside=True): saving goes back to the numbers, on
the new account.
"""
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from . import accounts, settings
from .platforms import PLATFORMS
from .platforms.base import PlatformError
from .style import chevron
from .tui import MOCHA

GREEN, RED, SUBTEXT, MAUVE = "#a6e3a1", "#f38ba8", "#a6adc8", "#cba6f7"
READS_ONLY = "adglance only reads: every request it makes is a GET, so it cannot change a campaign."

CSS = """
.f-form { width: 90; max-width: 100%; height: auto; padding: 1 2; }
.f-title { color: #cba6f7; text-style: bold; margin: 0 0 1 0; }
.f-label { color: #a6adc8; margin: 1 0 0 0; }
.f-hint { color: #6c7086; }
.f-result { margin: 1 0 0 0; height: auto; }
.f-buttons { height: auto; margin: 1 0 0 0; }
.f-buttons Button { margin: 0 2 0 0; }
Input { width: 100%; }
Select { width: 30; }
#list { height: auto; max-height: 20; margin: 1 0 0 0; }
"""


def clean(token):
    """A pasted token without the spaces or line breaks a copy can carry."""
    return "".join(token.split())


def seen(token):
    """What was received, without showing it: length and its ends."""
    return f"(read {len(token)} characters: {token[:4]}…{token[-4:]})" if len(token) > 12 else ""


class Form(Screen):
    """A setup screen; its styles stay its own, so they never reach the numbers."""
    DEFAULT_CSS = CSS

    def on_mount(self):
        self.styles.background = "#1e1e2e"

    def footer(self):
        # over the numbers, their keys would fill the footer and act behind the form
        return [] if getattr(self, "inside", False) else [Footer()]


class KeyScreen(Form):
    """A new account: which platform, its token and id, checked before kept."""
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, first=False, inside=False):
        super().__init__()
        self.first, self.inside = first, inside

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="f-form"):
            if self.first:
                yield Static("Welcome to adglance", classes="f-title")
                yield Static("Every ad's spend at a glance. First, connect an ad account.\n"
                             + READS_ONLY, classes="f-hint")
            else:
                yield Static("Add an ad account", classes="f-title")
            yield Static("Platform", classes="f-label")
            yield Select([(m.TITLE, name) for name, m in PLATFORMS.items()], value="tiktok",
                         allow_blank=False, compact=True, id="platform")
            yield Static("", classes="f-hint", id="help")
            yield Static("Access token", classes="f-label")
            yield Input(password=True, placeholder="paste the token", id="token")
            yield Static("Ad account id", classes="f-label")
            yield Input(placeholder="", id="account")
            with Horizontal(classes="f-buttons"):
                yield Button("Check", variant="primary", id="check")
                yield Button("Quit" if self.first else f"{chevron(False)} Back", id="back")
            yield Static("", classes="f-result", id="result")
        yield from self.footer()

    def on_mount(self):
        super().on_mount()
        self._help()
        self.query_one("#token").focus()

    def _help(self):
        module = PLATFORMS[self.query_one("#platform", Select).value]
        self.query_one("#help", Static).update(Text(module.HELP))
        self.query_one("#account", Input).placeholder = (
            "act_1234567890" if module.NAME == "meta" else "7000000000000000000")

    def on_select_changed(self, event):
        self._help()

    def on_input_submitted(self, event):
        if event.input.id == "token":
            self.query_one("#account").focus()
        else:
            self.action_check()

    def on_button_pressed(self, event):
        if event.button.id == "check":
            self.action_check()
        elif event.button.id == "back":
            self.action_back()

    def action_check(self):
        token = clean(self.query_one("#token", Input).value)
        account = self.query_one("#account", Input).value.strip()
        result = self.query_one("#result", Static)
        if not token or not account:
            result.update(Text("Both the token and the account id, please.", style=RED))
            return
        result.update(Text("Checking…", style=MAUVE))
        self.check(self.query_one("#platform", Select).value, token, account)

    @work(thread=True, exclusive=True)
    def check(self, platform, token, account):
        try:
            info = PLATFORMS[platform].check(token, account)
        except PlatformError as e:
            self.app.call_from_thread(self.query_one("#result", Static).update,
                                      Text(f"✗ {e}\n{seen(token)}", style=RED))
            return
        acc = {"platform": platform, "id": info["id"], "token": token, "label": info["name"],
               "currency": info["currency"], "timezone": info["timezone"]}
        if any(a["platform"] == platform and a["id"] == acc["id"] for a in self.app.accounts):
            self.app.call_from_thread(self.query_one("#result", Static).update,
                                      Text("That account is already set up: edit it from the list.",
                                           style=RED))
            return
        self.app.call_from_thread(self.app.switch_screen,
                                  AccountScreen(acc, new=True, first=self.first, inside=self.inside))

    def action_back(self):
        if self.first:
            self.app.exit(False)
        elif self.inside:
            self.app.pop_screen()                  # back to the numbers
        else:
            self.app.switch_screen(ListScreen())


class WelcomeScreen(KeyScreen):
    """The key form on the first run: nothing before it, so esc quits."""
    BINDINGS = [Binding("escape", "back", "Quit")]

    def __init__(self):
        super().__init__(first=True)


class AccountScreen(Form):
    """One account's label (and a new token). Saved to accounts.json. A fee,
    like every other option, is the account's own file (`,` on the screen)."""
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, acc, new=False, first=False, inside=False):
        super().__init__()
        self.acc, self.new, self.first, self.inside = dict(acc), new, first, inside

    def compose(self) -> ComposeResult:
        a, module = self.acc, PLATFORMS[self.acc["platform"]]
        with VerticalScroll(classes="f-form"):
            yield Static(("Connected  ✓" if self.new else "Account settings"), classes="f-title")
            yield Static(Text.assemble((f"{module.TITLE}  ·  ", SUBTEXT), (a.get("label") or a["id"], "bold"),
                                       (f"\n{a['id']}  ·  {a.get('currency') or '?'}  ·  "
                                        f"{a.get('timezone') or '?'}", SUBTEXT)))
            yield Static("Label: what the account picker shows", classes="f-label")
            yield Input(a.get("label", ""), id="label")
            yield Static("A fee, name columns, targets: options in this account's own file -- "
                         ", on the numbers opens it.", classes="f-hint")
            if not self.new:
                yield Static("Access token: blank keeps the one saved", classes="f-label")
                yield Input(password=True, placeholder="•••• kept", id="token")
            with Horizontal(classes="f-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button(f"{chevron(False)} Back", id="back")
            yield Static("", classes="f-result", id="result")
            yield Static(READS_ONLY, classes="f-hint")
        yield from self.footer()

    def on_mount(self):
        super().on_mount()
        self.query_one("#label").focus()

    def on_input_submitted(self, event):
        self.action_save()

    def on_button_pressed(self, event):
        if event.button.id == "save":
            self.action_save()
        elif event.button.id == "back":
            self.action_back()

    def _fail(self, text):
        self.query_one("#result", Static).update(Text(text, style=RED))

    def action_save(self):
        self.acc.update(label=self.query_one("#label", Input).value.strip() or self.acc["id"])
        self.acc.pop("fee", None)
        self.acc.pop("profile", None)                 # options are the account's own file now
        token = "" if self.new else clean(self.query_one("#token", Input).value)
        if token:
            self.query_one("#result", Static).update(Text("Checking the new token…", style=MAUVE))
            self.recheck(token)
        else:
            self._store()

    @work(thread=True, exclusive=True)
    def recheck(self, token):
        try:
            PLATFORMS[self.acc["platform"]].check(token, self.acc["id"])
        except PlatformError as e:
            self.app.call_from_thread(self._fail, f"✗ {e}\n{seen(token)}")
            return
        self.acc["token"] = token
        self.app.call_from_thread(self._store)

    def _store(self):
        accs = [a for a in self.app.accounts
                if not (a["platform"] == self.acc["platform"] and a["id"] == self.acc["id"])]
        at = next((i for i, a in enumerate(self.app.accounts)
                   if a["platform"] == self.acc["platform"] and a["id"] == self.acc["id"]), len(accs))
        accs.insert(at, self.acc)
        try:
            accounts.save(accs)
        except OSError as e:
            return self._fail(f"Cannot write {accounts.PATH}: {e.strerror}")
        self.app.accounts = accs
        try:                                       # every account has its own options file, empty to start
            settings.ensure(self.acc["platform"], self.acc["id"],
                            f"{PLATFORMS[self.acc['platform']].TITLE} · {self.acc.get('label', self.acc['id'])}")
        except OSError:
            pass                                   # `,` makes it later
        if self.first:
            self.app.exit(True)                    # straight on to the numbers
        elif self.inside:
            self.app.pop_screen()
            self.app.account_added(self.acc)       # the numbers, on the new account
        else:
            self.app.switch_screen(ListScreen())

    def action_back(self):
        if self.first:
            self.app.switch_screen(WelcomeScreen())
        elif self.inside:
            self.app.switch_screen(KeyScreen(inside=True))
        else:
            self.app.switch_screen(ListScreen())


class ListScreen(Form):
    """Every account: n adds one, enter edits, x removes, esc is done."""
    BINDINGS = [Binding("n", "new", "New"), Binding("x", "remove", "Remove"),
                Binding("escape", "done", "Done")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="f-form"):
            yield Static("Ad accounts", classes="f-title")
            yield Static(f"{accounts.PATH}  ·  readable by you alone", classes="f-hint")
            yield OptionList(id="list")
            yield Static("", classes="f-result", id="result")
            yield Static(READS_ONLY, classes="f-hint")
        yield from self.footer()

    def on_mount(self):
        super().on_mount()
        self._fill()
        self.query_one("#list").focus()

    def _fill(self):
        box = self.query_one("#list", OptionList)
        box.clear_options()
        for i, a in enumerate(self.app.accounts):
            module = PLATFORMS.get(a["platform"])
            line = Text.assemble((f"{module.TITLE if module else a['platform']:<7}", MAUVE),
                                 (f"{a.get('label', a['id'])}", "bold"),
                                 (f"   {a['id']}"
                                  , SUBTEXT))
            box.add_option(Option(line, id=str(i)))
        if not self.app.accounts:
            self.query_one("#result", Static).update(Text("No accounts yet: n adds one.", style=SUBTEXT))
        self.armed = None

    def on_option_list_option_selected(self, event):
        self.app.switch_screen(AccountScreen(self.app.accounts[int(event.option.id)]))

    def action_new(self):
        self.app.switch_screen(KeyScreen())

    def action_remove(self):
        box = self.query_one("#list", OptionList)
        if box.highlighted is None:
            return
        acc = self.app.accounts[box.highlighted]
        if self.armed != box.highlighted:          # once to ask, again to do it
            self.armed = box.highlighted
            self.query_one("#result", Static).update(
                Text(f"x again removes {acc.get('label', acc['id'])} from adglance "
                     "(the ad account itself is untouched).", style=RED))
            return
        accs = [a for i, a in enumerate(self.app.accounts) if i != box.highlighted]
        accounts.save(accs)
        self.app.accounts = accs
        self.query_one("#result", Static).update("")
        self._fill()

    def action_done(self):
        self.app.exit(bool(self.app.accounts))


class SetupApp(App):
    TITLE = "adglance setup"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, first):
        super().__init__()
        self.first = first
        self.accounts, self.problems = accounts.load()

    def on_mount(self):
        self.register_theme(MOCHA)
        self.theme = MOCHA.name
        self.push_screen(WelcomeScreen() if self.first else ListScreen())


def run_setup(first=False):
    """True when there is at least one account to show afterwards."""
    return bool(SetupApp(first).run())
