"""情報提供フォーム（Googleスプレッドシート）で「掲載する」にチェックが入った投稿を読む。"""
from __future__ import annotations

import logging
from datetime import datetime

import requests

from . import dates
from .extract import Keywords, Lottery

log = logging.getLogger(__name__)
FORM_IDS = {"WEB": "web", "web": "web", "アプリ": "app", "app": "app", "店頭": "store", "store": "store", "X": "x", "x": "x"}


def _dt(v: str, end: bool = False):
    v = (v or "").strip()
    if not v:
        return None, False
    try:
        if "T" in v or " " in v and ":" in v:
            return datetime.fromisoformat(v.replace(" ", "T")).replace(tzinfo=dates.JST), True
        d = datetime.fromisoformat(v)
        return d.replace(hour=23 if end else 0, minute=59 if end else 0, tzinfo=dates.JST), False
    except ValueError:
        p = dates.parse_point_ex(v, default_end=end)
        return (p[0], p[1]) if p else (None, False)


def collect(endpoint: str, kw: Keywords) -> tuple[list[dict], dict]:
    try:
        r = requests.get(endpoint, params={"mode": "approved"}, timeout=30)
        r.raise_for_status()
        rows = r.json().get("items", [])
    except (requests.RequestException, ValueError) as e:
        log.warning("情報提供フォーム: %s", e)
        return [], {"ok": False, "note": f"読み込めませんでした: {e}"[:120]}
    games_by_label = {g["label"]: gid for gid, g in kw.games.items()}
    out = []
    for row in rows:
        s, _ = _dt(row.get("apply_start"))
        e, _ = _dt(row.get("apply_end"), end=True)
        if not e or not row.get("store"):
            continue
        a, a_time = _dt(row.get("announce"), end=True)
        pref = row.get("pref") or None
        area = None
        if pref in ("通販・全国",):
            pref, area = None, "national"
        elif pref in ("その他の地域", "その他"):
            pref, area = None, "other"
        game = games_by_label.get(row.get("game")) or kw.detect_game(f"{row.get('game')} {row.get('product')}")
        product = row.get("product") or None
        lot = Lottery(
            store=row["store"].strip(), title=f"{product} 抽選" if product else "抽選販売",
            url=(row.get("url") or "").strip(), game=game, product=product,
            form=FORM_IDS.get(row.get("form") or "", None),
            apply_start=dates.iso(s), apply_end=dates.iso(e), announce=dates.iso(a), announce_allday=bool(a and not a_time),
            conditions=[row["note"].strip()] if row.get("note") else [],
            pref=pref, area=area, source_id="form", origin="form",
        )
        out.append(lot.to_dict())
    return out, {"ok": True, "found": len(out)}
