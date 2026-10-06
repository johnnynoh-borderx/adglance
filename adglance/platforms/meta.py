"""Meta (Facebook / Instagram): ad insights and the ads' state, through the Graph API.

The same shape as TikTok's: one row per ad per day, counts under style.PARTS.
Meta has no 6-second view and no profile visit, so those stay 0 and any rate
over them shows –. follows are Page likes; views are plays started.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import requests

from .base import PlatformError

NAME, TITLE = "meta", "Meta"
VERSION = "v25.0"
BASE = f"https://graph.facebook.com/{VERSION}"
HELP = ("Meta Business Settings -> Users -> System users: add one with access to the ad account, "
        "then Generate token with the ads_read permission. The account id: paste the number after act= in Ads Manager's address bar, or the whole "
        "address -- either works.")
MAX_DAYS = 30

# adglance's count -> an insights field holding [{action_type, value}]
VIDEO = {"views": "video_play_actions", "views_2s": "video_continuous_2_sec_watched_actions",
         "views_25": "video_p25_watched_actions", "views_50": "video_p50_watched_actions",
         "views_75": "video_p75_watched_actions", "views_100": "video_p100_watched_actions"}
# adglance's count -> an action_type in `actions`
ACTIONS = {"likes": "post_reaction", "comments": "comment", "shares": "post", "follows": "like",
           "engagements": "post_engagement"}
# the counts it reports: no 6-second view, no profile visit -- a metric over
# either cannot be picked for a Meta account (H shows it greyed)
PROVIDES = {"spend", "billed", "impressions", "clicks", "watch_time", *VIDEO, *ACTIONS}
# what a Meta account starts with: clicks and completes, not 6-second views
SHOW = ["billed", "share", "impressions", "cpm", "ctr", "cpc", "cpv", "complete"]
CARDS = ["billed", "cpm", "ctr", "cpc"]            # the summary on top
# column sets to pick from in H, by what a campaign is for
PRESETS = {"Default": SHOW,
           "Video views": ["billed", "share", "cpm", "cpv", "cpv2", "cpcv", "complete", "avg_watch"],
           "Engagement": ["billed", "share", "cpm", "cpe", "cpf", "er"],
           "Traffic": ["billed", "share", "impressions", "cpm", "ctr", "cpc"]}
FIELDS = ["ad_id", "ad_name", "adset_name", "campaign_name", "spend", "impressions", "inline_link_clicks",
          "video_avg_time_watched_actions", "actions", *VIDEO.values()]


def act(account_id):
    """act_123 from whatever was pasted: 123, act_123, act=123, or Ads Manager's
    whole address (…?act=123&…)."""
    text = str(account_id).strip()
    found = re.search(r"act[=_](\d+)", text) or re.search(r"(\d{6,})", text)
    return f"act_{found.group(1)}" if found else text


def get(path, params, token):
    """One GET (path, or a full paging URL). Raises PlatformError."""
    url = path if path.startswith("http") else f"{BASE}/{path}"
    try:
        r = requests.get(url, params={**params, "access_token": token} if params else None,
                         timeout=60)
        body = r.json()
    except requests.RequestException as e:
        raise PlatformError(f"Meta unreachable: {str(e).split('access_token')[0]}") from None
    except ValueError:
        raise PlatformError(f"Meta answered {r.status_code} with no JSON")
    if "error" in body:
        err = body["error"]
        text = f"Meta {err.get('code')}: {err.get('message')}"
        if err.get("code") == 190:                  # the token itself, not the account
            text += (" -- Meta does not accept this token: it was not copied whole, it has "
                     "expired, or a newer one replaced it. Generate a fresh one and paste it again.")
        raise PlatformError(text)
    return body


def _all(path, params, token):
    """Every page of a listing: the paging URL carries the token itself."""
    body = get(path, params, token)
    out = list(body.get("data") or [])
    while (body.get("paging") or {}).get("next"):
        body = get(body["paging"]["next"], None, token)
        out += body.get("data") or []
    return out


def check(token, account_id):
    """{id, name, currency, timezone}: proof the token reads this account."""
    a = get(act(account_id), {"fields": "name,currency,timezone_name,account_status"}, token)
    return {"id": act(account_id), "name": a.get("name", ""), "currency": a.get("currency", ""),
            "timezone": a.get("timezone_name", "")}


def _value(items, action_type=None):
    for item in items or []:
        if action_type is None or item.get("action_type") == action_type:
            return float(item.get("value") or 0)
    return 0.0


def daily(start, end, acc):
    """[{ad_id, day, campaign_name, ad_name, spend, impressions, ...}], one row
    per ad per day that delivered, start..end in the account's own days."""
    rows = _all(f"{act(acc['id'])}/insights", {
        "level": "ad", "time_increment": 1, "limit": 500, "fields": ",".join(FIELDS),
        "time_range": json.dumps({"since": start, "until": end})}, acc["token"])
    out = []
    for m in rows:
        spend, impressions = float(m.get("spend") or 0), float(m.get("impressions") or 0)
        if spend <= 0 and impressions <= 0:
            continue
        row = {"ad_id": m.get("ad_id", ""), "day": m.get("date_start", ""),
               "campaign_name": m.get("campaign_name", ""), "adgroup_name": m.get("adset_name", ""),
               "ad_name": m.get("ad_name", ""),
               "spend": spend, "impressions": impressions,
               "clicks": float(m.get("inline_link_clicks") or 0)}
        for part, field in VIDEO.items():
            row[part] = _value(m.get(field))
        for part, action_type in ACTIONS.items():
            row[part] = _value(m.get("actions"), action_type)
        row["watch_time"] = _value(m.get("video_avg_time_watched_actions")) * row["views"]
        out.append(row)
    return out


ADS_MANAGER = "https://adsmanager.facebook.com/adsmanager/manage/ads"


def ad_link(campaign_name, ad_name, start, end, acc):
    """Ads Manager's ad list for this account over start..end, with the ad's
    name in its search box."""
    query = {"act": act(acc["id"]).removeprefix("act_"), "date": f"{start}_{end}",
             "search_value": ad_name}
    return f"{ADS_MANAGER}?{urlencode(query)}"


WORDS = {"ACTIVE": "Live", "PAUSED": "Off", "CAMPAIGN_PAUSED": "Camp off",
         "ADSET_PAUSED": "Camp off", "PENDING_REVIEW": "Review", "IN_PROCESS": "Review",
         "PREAPPROVED": "Review", "DISAPPROVED": "Rejected", "PENDING_BILLING_INFO": "No budget",
         "ARCHIVED": "Ended", "DELETED": "Deleted", "WITH_ISSUES": "Issues"}


def statuses(acc, ids=None):
    """{ad id: (word, effective_status)}: every ad of the account, or only
    these ids, 50 a call."""
    if ids:
        ids = list(ids)
        chunks = [ids[i:i + 50] for i in range(0, len(ids), 50)]
        with ThreadPoolExecutor(4) as pool:
            answers = pool.map(lambda c: get("", {"ids": ",".join(c), "fields": "effective_status"},
                                             acc["token"]), chunks)
            ads = [ad for answer in answers for ad in answer.values() if isinstance(ad, dict)]
    else:
        ads = _all(f"{act(acc['id'])}/ads", {"fields": "id,effective_status", "limit": 500},
                   acc["token"])
    return {ad["id"]: (WORDS.get(ad.get("effective_status"), (ad.get("effective_status") or "–")
                             .replace("_", " ").capitalize()), ad.get("effective_status", ""))
            for ad in ads if ad.get("id")}
