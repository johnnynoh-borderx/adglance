"""adglance's store: every ad's counts per day, kept on this machine.

Old days do not change, so each day is fetched once and kept. Only the days
younger than `settled_days` (30 by default, settings.json) are fetched again --
once when adglance opens, and when r is pressed. Any period is then the sum of
its days, worked out here in milliseconds: a wider range costs nothing more,
and Daily is the same rows not summed.

All the counts add up across days (that is why reach is not one), so the sum of
a period's days is that period's total.

~/.cache/adglance/<account>.sqlite -- a cache: delete it and it is fetched again.
"""
import datetime as dt
import pathlib
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from .style import PARTS

CACHE = pathlib.Path.home() / ".cache" / "adglance"
PARALLEL = 4                         # report pieces asked at once (1.6 s for Jan..Sep, not 6.8)
SCHEMA = 2                           # 2: an ad's names are stamped with when they were fetched
_ZONE = {"name": None}               # the ad account's time zone: its days, not this machine's


def set_zone(name):
    _ZONE["name"] = name


def today(zone=None):
    """Today in an ad account's time zone (the report's days are its days):
    the one given, else the account on screen."""
    name = zone or _ZONE["name"]
    if name:
        try:
            return dt.datetime.now(ZoneInfo(name)).date()
        except (KeyError, ValueError):
            pass
    return dt.date.today()


def days(start, end, zone=None):
    """The days of start..end up to today -- a day still to come has nothing to fetch."""
    d = dt.date.fromisoformat(start)
    last = min(dt.date.fromisoformat(end), today(zone))
    while d <= last:
        yield d
        d += dt.timedelta(days=1)


def freshness(store, start, end):
    """(when the numbers of start..end were fetched, as words, and whether the
    period is wholly settled): ('fetched 02:59 · 4m ago', False), with the date
    when not today; ('fetched 10-02 01:37 · settled', True); ('', False) when
    a day is missing. Shared by the screen and --print."""
    settled = str(store.settled_before())
    old = end < settled
    at = store.fetched_at(start, end) if old else store.fetched_at(max(start, settled), end)
    if not at:
        return "", False
    when = dt.datetime.fromtimestamp(at)
    stamp = f"{when:%H:%M}" if when.date() == today(store.zone) else f"{when:%m-%d %H:%M}"
    if old:
        return f"fetched {stamp} · settled", True
    age = time.time() - at
    ago = ("just now" if age < 60 else f"{age // 60:.0f}m ago" if age < 3600
           else f"{age // 3600:.0f}h ago" if age < 86400 else f"{age // 86400:.0f}d ago")
    return f"fetched {stamp} · {ago}", False


def _runs(days, longest):
    """Sorted days -> (start, end) runs of consecutive days, none over `longest`."""
    runs = []
    for d in days:
        if runs and (d - runs[-1][1]).days == 1 and (d - runs[-1][0]).days < longest:
            runs[-1][1] = d
        else:
            runs.append([d, d])
    return [(str(a), str(b)) for a, b in runs]


class Store:
    def __init__(self, module, account, settled_days=30, zone=None):
        # module: a platform (platforms/); account: its dict from accounts.json
        # its own account's zone: a fetch finishing after a switch still dates its own days
        # (no zone given: the one in use now, fixed for this store from here on)
        self.module, self.account, self.zone = module, account, zone or _ZONE["name"]
        self.settled_days = settled_days
        self.lock = threading.Lock()
        CACHE.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(CACHE / f"{module.NAME}-{account['id']}.sqlite", check_same_thread=False)
        cols = ", ".join(f"{p} REAL NOT NULL DEFAULT 0" for p in PARTS)
        with self.lock, self.db:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version == 1:                         # names stamped by day: any fetch now wins
                self.db.execute("UPDATE ads SET seen = ''")
                self.db.execute(f"PRAGMA user_version = {SCHEMA}")
            elif version != SCHEMA:                  # an older layout: start over, it is a cache
                self.db.executescript("DROP TABLE IF EXISTS daily; DROP TABLE IF EXISTS ads; "
                                      "DROP TABLE IF EXISTS days;")
                self.db.execute(f"PRAGMA user_version = {SCHEMA}")
            self.db.execute(f"CREATE TABLE IF NOT EXISTS daily (ad_id TEXT, day TEXT, {cols}, "
                            "PRIMARY KEY (ad_id, day))")
            # the latest names per ad: a rename shows on the ad's whole history
            self.db.execute("CREATE TABLE IF NOT EXISTS ads (ad_id TEXT PRIMARY KEY, "
                            "campaign_name TEXT, ad_name TEXT, seen TEXT)")
            # every day fetched, with or without spend, and when
            self.db.execute("CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, at REAL)")

    # ---- what is there ----------------------------------------------------------
    def settled_before(self):
        """The first day that may still change: today - settled_days + 1."""
        return today(self.zone) - dt.timedelta(days=self.settled_days - 1)

    def missing(self, start, end, recent_since=None):
        """The days in start..end to fetch: never fetched, or -- with
        recent_since (epoch seconds) -- still unsettled and fetched before it."""
        with self.lock:
            have = dict(self.db.execute("SELECT day, at FROM days WHERE day BETWEEN ? AND ?",
                                        (start, end)).fetchall())
        settled = self.settled_before()
        return [d for d in days(start, end, self.zone)
                if str(d) not in have
                or (recent_since is not None and d >= settled and have[str(d)] < recent_since)]

    def fetched_at(self, start, end):
        """When the oldest-fetched day of start..end was fetched (None: one is missing)."""
        with self.lock:
            n, oldest = self.db.execute("SELECT count(*), min(at) FROM days WHERE day BETWEEN ? AND ?",
                                        (start, end)).fetchone()
        return oldest if n == len(list(days(start, end, self.zone))) else None

    def rows(self, start, end, daily=False):
        """The counts of start..end per ad (daily: per ad per day), with each
        ad's latest names -- the records style.prepare takes."""
        # billed is spend x the fee as it is now, not as stored: a fee change
        # never leaves old days on the old fee
        fee = float(self.account.get("fee", 1.0))
        sums = ", ".join(f"sum(spend) * {fee!r} AS billed" if p == "billed" else f"sum({p}) AS {p}"
                         for p in PARTS)
        day = ", daily.day AS day" if daily else ""
        by = "daily.ad_id, daily.day" if daily else "daily.ad_id"
        sql = (f"SELECT daily.ad_id, ads.campaign_name, ads.ad_name{day}, {sums} FROM daily "
               f"LEFT JOIN ads USING (ad_id) WHERE daily.day BETWEEN ? AND ? GROUP BY {by}")
        with self.lock:
            cur = self.db.execute(sql, (start, end))
            names = [c[0] for c in cur.description]
            out = [dict(zip(names, row)) for row in cur.fetchall()]
        for r in out:
            r["campaign_name"], r["ad_name"] = r["campaign_name"] or "", r["ad_name"] or ""
        return [r for r in out if r["spend"] > 0 or r["impressions"] > 0]

    # ---- statuses: the ad's state now, not a count -------------------------------
    SWEEP = 3600                     # every ad at most hourly; in between, only the busy ones

    def statuses(self):
        """{ad id: (operation_status, secondary_status)} as last fetched this
        session; None before the first fetch (the screen shows it loading)."""
        with self.lock:
            st = getattr(self, "_status", None)
            return None if st is None else dict(st)

    def fetch_statuses(self, full=False):
        """Fetch ad statuses. Every ad (pages in parallel) when asked, never done
        this session, or the last full sweep is over SWEEP old; otherwise only
        the ads that delivered in the last 2 days, by id -- an ad that is not
        spending changes nothing that matters until it spends, and then it is
        in that set. So many ads cost one full sweep an hour, not one a refresh."""
        if not hasattr(self.module, "statuses"):
            return
        with self.lock:
            known = dict(getattr(self, "_status", None) or {})
            swept = getattr(self, "_swept", 0)
        if full or not known or time.time() - swept > self.SWEEP:
            got, swept = self.module.statuses(self.account), time.time()
            known = got
        else:
            since = str(today(self.zone) - dt.timedelta(days=1))
            with self.lock:
                busy = [r[0] for r in self.db.execute(
                    "SELECT DISTINCT ad_id FROM daily WHERE day >= ? AND spend > 0", (since,))]
            ids = [a for a in busy if a in known]
            if ids:
                known.update(self.module.statuses(self.account, ids))
            if any(a not in known for a in busy):            # a new ad: sweep to find it
                known, swept = self.module.statuses(self.account), time.time()
        with self.lock:
            self._status, self._swept = known, swept

    # ---- fetching ----------------------------------------------------------------
    def fetch(self, days, progress=None):
        """Fetch these days from the platform -- in pieces of at most the
        platform's MAX_DAYS, PARALLEL at a time -- and keep them. progress(done,
        total) after each piece. Raises the first error after keeping the rest."""
        runs = _runs(sorted(days), getattr(self.module, "MAX_DAYS", 30))
        done, error = 0, None
        with ThreadPoolExecutor(PARALLEL) as pool:
            futures = {pool.submit(self.module.daily, a, b, self.account): (a, b) for a, b in runs}
            for future, (a, b) in futures.items():
                try:
                    self._keep(a, b, future.result())
                except Exception as e:
                    error = error or e
                done += 1
                if progress:
                    progress(done, len(runs))
        if error:
            raise error

    def _keep(self, start, end, rows):
        """Replace start..end with these rows: a day's old rows go even when the
        new answer has none for that ad."""
        now, last = time.time(), str(today(self.zone))
        cols = ["ad_id", "day", *PARTS]
        with self.lock, self.db:
            self.db.execute("DELETE FROM daily WHERE day BETWEEN ? AND ?", (start, end))
            self.db.executemany(
                f"INSERT INTO daily ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [[r["ad_id"], r["day"], *(r.get(p) or 0 for p in PARTS)] for r in rows])
            # the names of the latest fetch: the report gives an ad's names as
            # they are now, whichever day is asked for, so the newest answer is
            # the newest name -- R on old days picks up a rename too
            stamp = f"{now:017.6f}"                  # sorts as text
            for r in rows:
                self.db.execute(
                    "INSERT INTO ads VALUES (?, ?, ?, ?) ON CONFLICT (ad_id) DO UPDATE SET "
                    "campaign_name = excluded.campaign_name, ad_name = excluded.ad_name, "
                    "seen = excluded.seen WHERE excluded.seen >= ads.seen",
                    (r["ad_id"], r["campaign_name"], r["ad_name"], stamp))
            self.db.executemany("INSERT OR REPLACE INTO days VALUES (?, ?)",
                                [(str(d), now) for d in days(start, min(end, last), self.zone)])
