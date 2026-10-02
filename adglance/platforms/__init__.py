"""The ad platforms adglance reads. Each module gives, for one account dict
(accounts.json):

    NAME, TITLE, MAX_DAYS
    HELP                  where to find the token and the account id (setup)
    check(token, id)      {id, name, currency, timezone} -- one read-only call
    daily(start, end, acc)   one row per ad per day, counts under style.PARTS
    statuses(acc, ids=None)  {ad id: (word, raw)}, word one of style.STATUS_GLYPH
    ad_link(campaign, ad, start, end, acc)   the ad in the platform's own UI

Every request is a GET: nothing here can change an account.
"""
from . import meta, tiktok

PLATFORMS = {m.NAME: m for m in (tiktok, meta)}
