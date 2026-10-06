# adglance

Every ad's spend at a glance, in the terminal. TikTok and Meta, read-only.

```
uv tool install git+https://github.com/<you>/adglance    # or: uv tool install --editable .
adglance            # first run: connect an account, then the numbers
adglance setup      # add, check and edit accounts later (or A on the screen: + Add account…)
```

- **Fast.** Each day is fetched once and kept in SQLite; any period, group or
  daily breakdown after that is a local sum.
- **Real totals.** A group or total sums the counts first, then applies the
  formula, so its CPV is the real one, never an average of averages.
- **Nothing to set up but a key.** Onboarding asks for the token and the
  account id; names show as they are. Everything else is an option.
- **A summary on top.** Cards with the total of what is shown -- spend and the
  platform's key costs -- and the change on the period just before (7d: the 7
  days before), teal when it got better, peach when worse. `"cards"` picks them.
- **Columns for the platform.** TikTok starts on views and follows, Meta on
  clicks and completes.
- **One options window.** Columns, Sort, Group, Pins and Cards are tabs of one
  window; `H` `S` `G` `F` open it on theirs. Columns offers sets by what a
  campaign is for (Default, Video views, Engagement, Traffic, your own
  `"presets"`), a search box, ticks, `shift+↑↓` to order and `n` to make a
  metric from a formula. Every change -- there, or with `s` `g` `h` `f` and
  `shift+← →` on the table -- is written to the account's own file: how an
  account is seen lives in one place.
- **Campaigns, ad groups, ads.** `1` `2` `3` switch the level; Enter on a
  campaign opens its ad groups, on an ad group its ads.
- **Keyboard first.** Arrow keys move a cell cursor, Enter opens a group or an
  ad, Esc goes back, `/` filters, `s` sorts, `G` groups, `d` goes daily, `?`
  lists every key.
- **Read-only.** Every request is a GET. It cannot pause, edit or create anything.

## Accounts

`adglance setup` writes `~/.config/adglance/accounts.json` (mode 600): the only
part set up interactively, because a key has to be checked against the platform.

```json
{"accounts": [
  {"platform": "tiktok", "id": "7000000000000000001", "label": "Brand US",
   "token": "…", "currency": "KRW", "timezone": "Asia/Seoul"},
  {"platform": "meta", "id": "act_1234567890", "label": "Brand CA", "token": "…"}
]}
```

Tokens: TikTok, a Marketing API app's long-term access token with reporting
read scope. Meta, a system user token with `ads_read`.

## Settings, the Ghostty way

Everything else is a JSON file you edit. Every default lives in the code; a
file holds only what you change, and starts empty.

```
,                                          on the screen: open it in $EDITOR, reload on close
adglance +show-config --default --docs     every option with what it does (a valid file)
adglance +validate-config                  check it
```

A mistake never stops adglance: that option keeps its default and a strip on
top says why. Lines starting with `//` are comments.

Add a fee to every cost (billed = spend × fee; the cost metrics are formulas
over billed), split names built from pieces into columns, and judge rows
against targets:

```json
{
  "fee": 1.15,
  "names": {"split": "_", "campaign": ["brand", "geo", "objective", "flight"],
            "labels": {"objective": "Obj"}},
  "targets": ["Obj=VV cpv6 <= 20"],
  "colors": {"US": "blue"},
  "metrics": {"cpv3": {"name": "3s CPV", "formula": "billed / views_2s", "format": "cost"}}
}
```

Names a separator cannot read take regular expressions (`names.patterns`);
`examples/advanced.json` is a complete advanced example.

### Each account's own options

Nothing has to be written to start: the defaults work for any account. Every
account also has its own file, made empty when the account is added, and `,`
on the screen opens the one for the account shown. What you write there
overrides the defaults for that account only. There is nothing to name and
nothing to link: where the file is says what it covers.

```
~/.config/adglance/
  accounts.json                         keys (adglance setup)
  accounts/tiktok-7000000000000000001.json   this account's options
  accounts/meta-act_1234567890.json          that one's
  settings.json                         optional: options for every account
```

The layers, last wins: the defaults in the code, settings.json, the account's
file. Each account also remembers its own shown columns, order, pins, sort and
grouping. `+validate-config` checks every file and names one that no account
reads.

## License

MIT
