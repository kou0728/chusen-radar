"""ショップのXアカウントから抽選告知を拾う（任意）。

X API の Bearer Token を環境変数 X_BEARER_TOKEN に入れたときだけ動きます。
2026年からの従量課金（読んだ投稿1件ごとに課金）に合わせて、むだな課金をしない読み方にしています。
  - 前回より新しい投稿だけを読む（since_id）。同じ投稿に何度も課金されない
  - 投稿者の情報（user）は付けない（別料金になるため）。お店ごとに分けて検索する
  - 月の上限額（X_MONTHLY_BUDGET_USD、既定5ドル）に達したら、その月は止める
トークンが無ければ何もしません（サイトは公式ページの巡回と手動登録だけで動きます）。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from .extract import Keywords, Lottery, extract_from_post

log = logging.getLogger(__name__)
API = "https://api.x.com/2/tweets/search/recent"
POST_READ_USD = 0.005     # 「投稿の読み取り1件」の単価（2026年時点）。最新は X の Developer Console で確認
DEFAULT_BUDGET_USD = 5.0  # 月の上限の既定値
FIRST_LOOKBACK_DAYS = 3   # 初回だけ、さかのぼって読む日数
SINCE_ID_MAX_AGE = timedelta(days=6)  # 直近検索は7日より前の since_id を受け付けないので、古くなったら日時で指定し直す
TWEPOCH_MS = 1288834974657  # 投稿IDに埋め込まれた時刻の起点

HTTP_NOTES = {
    401: "トークンが無効です",
    402: "クレジットが足りません（Developer Consoleで追加）",
    403: "このトークンでは検索できません（プラン・権限を確認）",
    429: "回数の上限に達しました（次の回で再開）",
}


def enabled() -> bool:
    return bool(os.environ.get("X_BEARER_TOKEN"))


def budget_usd() -> float:
    try:
        return float(os.environ.get("X_MONTHLY_BUDGET_USD") or DEFAULT_BUDGET_USD)
    except ValueError:
        return DEFAULT_BUDGET_USD


def _id_time(post_id) -> datetime | None:
    try:
        return datetime.fromtimestamp(((int(post_id) >> 22) + TWEPOCH_MS) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _note(e: Exception) -> str:
    if isinstance(e, requests.HTTPError) and e.response is not None:
        code = e.response.status_code
        return f"HTTP {code} {HTTP_NOTES.get(code, '')}".strip()
    return str(e)[:120]


def collect(x_sources: list[dict], kw: Keywords, now: datetime, xstate: dict | None = None) -> tuple[list[Lottery], dict]:
    """xstate は state.json の "x"（前回どこまで読んだか・今月の読み取り件数）。"""
    status: dict[str, dict] = {}
    if not enabled() or not x_sources:
        return [], {s["id"]: {"ok": None, "note": "X APIトークン未設定のためスキップ"} for s in x_sources}
    xstate = xstate if xstate is not None else {}
    month = now.strftime("%Y-%m")
    spend = xstate.setdefault("spend", {})
    if spend.get("month") != month:
        spend.clear()
        spend.update(month=month, posts=0)
    since = xstate.setdefault("since_id", {})
    limit = budget_usd()
    headers = {"Authorization": f"Bearer {os.environ['X_BEARER_TOKEN']}"}
    start = (now - timedelta(days=FIRST_LOOKBACK_DAYS)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[Lottery] = []
    for s in x_sources:
        if spend["posts"] * POST_READ_USD >= limit:
            status[s["id"]] = {"ok": None, "note": f"今月のX APIの上限（${limit:g}）に達したので止めています"}
            continue
        acct = s["account"]
        params = {"query": f"from:{acct} (抽選 OR 応募) -is:retweet", "max_results": 100, "tweet.fields": "created_at"}
        sid = since.get(acct.lower())
        t = _id_time(sid) if sid else None
        if t and now - t < SINCE_ID_MAX_AGE:
            params["since_id"] = sid
        else:
            # 初回、または1週間近く新しい告知が無かったお店。それ以降の投稿は無いので、読み直しにはならない
            params["start_time"] = start
        try:
            r = requests.get(API, headers=headers, timeout=20, params=params)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            status[s["id"]] = {"ok": False, "note": _note(e)}
            continue
        posts = data.get("data") or []
        spend["posts"] += len(posts)
        newest = (data.get("meta") or {}).get("newest_id")
        if newest:
            since[acct.lower()] = newest
        found = 0
        for tw in posts:
            url = f"https://x.com/{acct}/status/{tw['id']}"
            lot = extract_from_post(tw.get("text", ""), url, {**s, "url": url}, kw, now)
            if lot:
                lot.form = s.get("form") or "x"
                lot.pref = s.get("pref")
                out.append(lot)
                found += 1
        status[s["id"]] = {"ok": True, "found": found}
    spend["usd"] = round(spend["posts"] * POST_READ_USD, 3)
    return out, status
