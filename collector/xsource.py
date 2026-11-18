"""ショップのXアカウントから抽選告知を拾う（任意）。

X API v2 の Bearer Token を環境変数 X_BEARER_TOKEN に入れたときだけ動きます。
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


def enabled() -> bool:
    return bool(os.environ.get("X_BEARER_TOKEN"))


def collect(x_sources: list[dict], kw: Keywords, now: datetime) -> tuple[list[Lottery], dict]:
    status: dict[str, dict] = {}
    if not enabled() or not x_sources:
        return [], {s["id"]: {"ok": None, "note": "X APIトークン未設定のためスキップ"} for s in x_sources}
    headers = {"Authorization": f"Bearer {os.environ['X_BEARER_TOKEN']}"}
    since = (now - timedelta(days=3)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[Lottery] = []
    # 1リクエストに複数アカウントをまとめてAPI消費を抑える
    for i in range(0, len(x_sources), 10):
        chunk = x_sources[i:i + 10]
        by_acct = {s["account"].lower(): s for s in chunk}
        q = "(" + " OR ".join(f"from:{s['account']}" for s in chunk) + ") (抽選 OR 応募) -is:retweet"
        try:
            r = requests.get(API, headers=headers, timeout=20, params={
                "query": q, "max_results": 100, "start_time": since,
                "tweet.fields": "created_at,author_id", "expansions": "author_id", "user.fields": "username",
            })
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            for s in chunk:
                status[s["id"]] = {"ok": False, "note": str(e)[:120]}
            continue
        users = {u["id"]: u["username"].lower() for u in data.get("includes", {}).get("users", [])}
        for tw in data.get("data", []):
            acct = users.get(tw["author_id"])
            src = by_acct.get(acct)
            if not src:
                continue
            url = f"https://x.com/{src['account']}/status/{tw['id']}"
            lot = extract_from_post(tw["text"], url, {**src, "url": url}, kw, now)
            if lot:
                lot.form = src.get("form") or "x"
                lot.pref = src.get("pref")
                out.append(lot)
        for s in chunk:
            status[s["id"]] = {"ok": True, "found": sum(1 for l in out if l.source_id == s["id"])}
    return out, status
