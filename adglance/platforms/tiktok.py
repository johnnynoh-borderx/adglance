"""TikTok: the daily report and the ads' state, through the Marketing API.

One row per ad per day, as its campaign and ad names and its counts under
adglance's names (style.PARTS). Nothing here reads a name -- config.toml says
how names become columns -- and nothing works out a rate; rates are formulas
over the counts, so a total over any rows is the real one.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import requests

from .base import PlatformError

NAME, TITLE = "tiktok", "TikTok"
BASE = "https://business-api.tiktok.com/open_api/v1.3"
HELP = ("TikTok for Business -> My Apps (business-api.tiktok.com/portal): create an app with "
        "the Reporting and Ad Management read scopes, authorise it on your ad account, and copy "
        "its long-term access token. The advertiser id: the number after aadvid= in Ads Manager's address bar, or the whole address.")

# adglance's count -> the report's metric. views is a play started; clicks are
# clicks to the destination. watch_time is derived below, since the report
# gives only an average per play.
COUNTS = {"impressions": "impressions", "clicks": "clicks", "views": "video_play_actions",
          "views_2s": "video_watched_2s", "views_6s": "video_watched_6s",
          "views_25": "video_views_p25", "views_50": "video_views_p50",
          "views_75": "video_views_p75", "views_100": "video_views_p100",
          "likes": "likes", "comments": "comments", "shares": "shares", "follows": "follows",
          "profile_visits": "profile_visits", "engagements": "engagements"}
METRICS = ["campaign_name", "adgroup_name", "ad_name", "spend", "average_video_play", *COUNTS.values()]
# the counts it reports (all of them), and the columns a TikTok account starts with
PROVIDES = {"spend", "billed", "watch_time", *COUNTS}
SHOW = ["billed", "share", "cpm", "cpv", "cpv6", "cpf"]
CARDS = ["billed", "cpm", "cpv6", "cpf"]           # the summary on top
# column sets to pick from in H, by what a campaign is for
PRESETS = {"Default": SHOW,
           "Video views": ["billed", "share", "cpm", "cpv", "cpv2", "cpv6", "cpcv", "rate_6s", "complete"],
           "Engagement": ["billed", "share", "cpm", "cpe", "cpf", "er"],
           "Traffic": ["billed", "share", "impressions", "cpm", "ctr", "cpc"]}
# the report allows a daily breakdown over 30 days at most ("max time span is
# 30 days when use stat_time_day"); the store asks in pieces this long
MAX_DAYS = 30


# a refusal that asking again will not change: the token or its permission
FINAL = {40001, 40100, 40102, 40104, 40105}


def get(path, params, token, tries=3):
    """One GET; nested values go as JSON, as the API wants. TikTok now and then
    fails a read for no reason of ours ("remote or network error", "Creative_data
    not valid" -- gone a second later), so a failure is asked again, up to
    `tries` times, a little later each time; an auth refusal is not. Raises
    PlatformError."""
    query = {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in params.items()}
    for attempt in range(tries):
        try:
            r = requests.get(BASE + path, headers={"Access-Token": token}, params=query, timeout=60)
            body = r.json()
        except requests.RequestException as e:
            error = PlatformError(f"TikTok unreachable: {e}")
        except ValueError:
            error = PlatformError(f"TikTok answered {r.status_code} with no JSON")
        else:
            if body.get("code") == 0:
                return body.get("data") or {}
            error = PlatformError(f"TikTok {body.get('code')}: {body.get('message')}")
            if body.get("code") in FINAL:
                raise error
        if attempt < tries - 1:
            time.sleep(0.6 * (attempt + 1))
    raise error


def advertiser(account_id):
    """The advertiser id from whatever was pasted: the number, aadvid=…, or
    Ads Manager's whole address."""
    text = str(account_id).strip()
    found = re.search(r"aadvid=(\d+)", text) or re.search(r"(\d{6,})", text)
    return found.group(1) if found else text


def check(token, account_id):
    """{id, name, currency, timezone}: proof the token reads this account."""
    account_id = advertiser(account_id)
    data = get("/advertiser/info/", {"advertiser_ids": [str(account_id)],
                                    "fields": ["name", "currency", "timezone", "display_timezone"]},
               token)
    found = data.get("list") or []
    if not found:
        raise PlatformError(f"TikTok: the token cannot see advertiser {account_id}")
    a = found[0]
    return {"id": str(account_id), "name": a.get("name", ""), "currency": a.get("currency", ""),
            "timezone": a.get("display_timezone") or a.get("timezone") or ""}


def daily(start, end, acc):
    """[{ad_id, day, campaign_name, ad_name, spend, impressions, ...}]: one row
    per ad per day in start..end (at most MAX_DAYS), only the days an ad did
    something. Every page of the report, so a busy month is complete."""
    out, page = [], 1
    while True:
        data = get("/report/integrated/get/", {
            "advertiser_id": acc["id"], "report_type": "BASIC", "data_level": "AUCTION_AD",
            "dimensions": ["ad_id_v2", "stat_time_day"], "metrics": METRICS,
            "start_date": start, "end_date": end, "page_size": 1000, "page": page}, acc["token"])
        for raw in data.get("list") or []:
            m, d = raw.get("metrics", {}), raw.get("dimensions", {})
            spend = float(m.get("spend") or 0)
            impressions = float(m.get("impressions") or 0)
            if spend <= 0 and impressions <= 0:
                continue                            # a day the ad did nothing
            row = {"ad_id": d.get("ad_id_v2", ""), "day": (d.get("stat_time_day") or "")[:10],
                   "campaign_name": m.get("campaign_name", ""), "adgroup_name": m.get("adgroup_name", ""),
                   "ad_name": m.get("ad_name", ""), "spend": spend}
            for part, metric in COUNTS.items():
                row[part] = float(m.get(metric) or 0)
            row["watch_time"] = float(m.get("average_video_play") or 0) * row["views"]
            out.append(row)
        info = data.get("page_info") or {}
        if page >= int(info.get("total_page") or 1):
            return out
        page += 1


ADS_MANAGER = "https://ads.tiktok.com/i18n/manage/creative"


def ad_link(campaign_name, ad_name, start, end, acc):
    """Ads Manager's ad list filtered to this one ad over start..end -- built
    from what is in hand, no request; the preview is one click from there."""
    query = {"aadvid": acc["id"], "st": start, "et": end,
             "filters[0][field]": "creative_name", "filters[0][filter_type]": "31",
             "filters[0][in_field_values][0]": ad_name,
             "filters[1][field]": "campaign_name", "filters[1][filter_type]": "31",
             "filters[1][in_field_values][0]": campaign_name}
    return f"{ADS_MANAGER}?{urlencode(query)}"


def word(op, secondary):
    """ENABLE / AD_STATUS_DELIVERY_OK -> Live; the rest in a word or two."""
    s = (secondary or "").upper()
    if "DELETE" in s:
        return "Deleted"
    if "REJECT" in s:
        return "Rejected"
    if "AUDIT" in s or "REVIEW" in s:
        return "Review"
    if "CAMPAIGN_DISABLE" in s or "ADGROUP_DISABLE" in s:
        return "Camp off"
    if op == "DISABLE" or s.endswith("_DISABLE"):
        return "Off"
    if "DELIVERY_OK" in s:
        return "Live"
    if "BUDGET" in s or "EXCEED" in s or "BALANCE" in s:
        return "No budget"
    if "NOT_START" in s:
        return "Scheduled"
    if "DONE" in s or "END" in s:
        return "Ended"
    return s.removeprefix("AD_STATUS_").replace("_", " ").capitalize() or "–"


def _smaller(size):
    """The next page size down that divides this one: 100 -> 50 -> 25 -> 5 -> 1."""
    for d in (2, 5):
        if size % d == 0:
            return size // d
    return 1


def _pages(path, acc, key, ids=None, id_filter=None, size=100, fields=None, extra=None):
    """({ad id: (word, raw)}, [ids TikTok could not read]) from one listing:
    every page (4 at a time), or these ids (100 a call). A call that still
    fails is split in smaller ones -- a page into smaller pages, ids into
    halves -- so one ad TikTok cannot read costs that ad, not its 99 neighbours."""
    def call(page, n, chunk=None):
        flt = dict(extra or {})
        if chunk:
            flt[id_filter] = chunk
        params = {"advertiser_id": acc["id"], "page_size": n, "page": page}
        if fields:
            params["fields"] = fields
        if flt:
            params["filtering"] = flt
        return get(path, params, acc["token"]).get("list") or []

    unreadable = []

    def by_ids(chunk):
        try:
            return call(1, 100, chunk)
        except PlatformError:
            if len(chunk) == 1:
                unreadable.append(chunk[0])
                return []
            mid = len(chunk) // 2
            return by_ids(chunk[:mid]) + by_ids(chunk[mid:])

    def span(page, n):
        try:
            return call(page, n)
        except PlatformError:
            if n == 1:
                return []                            # one ad, no id to name it by
            m = _smaller(n)
            k = n // m                               # page `page` of n = these pages of m
            return [ad for i in range(k) for ad in span((page - 1) * k + i + 1, m)]

    if ids:
        ids = list(ids)
        with ThreadPoolExecutor(4) as pool:
            found = [ad for got in pool.map(by_ids, [ids[i:i + 100] for i in range(0, len(ids), 100)])
                     for ad in got]
    else:
        first = get(path, {"advertiser_id": acc["id"], "page_size": size, "page": 1,
                           **({"fields": fields} if fields else {}),
                           **({"filtering": dict(extra)} if extra else {})}, acc["token"])
        total = int((first.get("page_info") or {}).get("total_page") or 1)
        with ThreadPoolExecutor(4) as pool:
            found = (list(first.get("list") or [])
                     + [ad for got in pool.map(lambda n: span(n, size), range(2, total + 1)) for ad in got])
    return ({ad[key]: (word(ad.get("operation_status"), ad.get("secondary_status")),
                       f"{ad.get('operation_status', '')} {ad.get('secondary_status', '')}".strip())
             for ad in found if ad.get(key)}, unreadable)


SMART = "/smart_plus/ad/get/"
DELETED = {"secondary_status": "AD_STATUS_DELETE"}   # the listing leaves deleted ads out unless asked


def _no_smart_plus(error):
    """An account this app may not read Smart+ ads of: not a failure, just none."""
    text = str(error).lower()
    return "permission" in text or "not support" in text or "40001" in text


def statuses(acc, ids=None):
    """{ad id: (word, raw)}. The report's ad_id_v2 is a Smart+ ad's
    smart_plus_ad_id (checked 2026-10-02) and a regular ad's ad_id, so both
    listings are read; Smart+ wins where both know an id. Deleted ads are asked
    for too (TikTok leaves them out otherwise), and an ad TikTok cannot read
    comes back as Unreadable rather than taking its neighbours down."""
    def smart_plus():
        try:
            got, bad = _pages(SMART, acc, "smart_plus_ad_id", ids, "smart_plus_ad_ids")
            missing = [i for i in ids if i not in got and i not in bad] if ids else None
            if ids is None or missing:
                gone, more = _pages(SMART, acc, "smart_plus_ad_id", missing, "smart_plus_ad_ids",
                                    extra=DELETED)
                for k, v in gone.items():
                    got.setdefault(k, v)
                bad += more
            return got, bad
        except PlatformError as e:
            if _no_smart_plus(e):
                return {}, []
            raise PlatformError(f"Smart+ ad statuses: {e}") from e

    # side by side: /ad/get/ takes 1,000 a page (491 ads ~2.6 s), Smart+ 100
    with ThreadPoolExecutor(2) as pool:
        plain = pool.submit(_pages, "/ad/get/", acc, "ad_id", ids, "ad_ids", 1000,
                            ["ad_id", "operation_status", "secondary_status"])
        smart = pool.submit(smart_plus)
        out, bad = plain.result()
        got, bad_smart = smart.result()
        out.update(got)
    for ad in (set(bad) | set(bad_smart)) - set(out) if ids else []:
        out.setdefault(ad, ("Unreadable", "TikTok could not read this ad"))
    return out
