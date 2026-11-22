"""アプリ画面の文字から抽選情報を取り出す。Web巡回と同じ抽出ルール（collector.extract）を使う。"""
from __future__ import annotations

import re
from datetime import datetime

from collector import dates
from collector.extract import Keywords, Lottery, candidate_blocks, clean_soup, to_lottery

DATE_ONLY = re.compile(r"^[\d\s/.\-:：()（）年月日時分火水木金土祝〜~～]+$")
# 「応募期間：…」「当選発表 …」のような行は見出しにしない
PERIOD_LINE = re.compile(r"^\W*(?:応募|受付|抽選|当選|購入|販売|申込|申し込み|エントリー|お支払い|支払|引き?換え?|受け?取り?)\S{0,6}?(?:期間|期限|日時|発表|日)")
MAX_PAGE_TEXT = 3000


def app_source(app: dict) -> dict:
    store = app.get("store") or app.get("name") or app["id"]
    return {
        "id": f"app:{app['id']}",
        "name": store,
        "store": store,
        "url": app.get("url") or "",
        "game": app.get("game"),
        "form": "app",
    }


def _block_labels(el) -> list[str]:
    return [dates.normalize(b.get_text(" ", strip=True)) for b in el.find_all("b")]


def pick_title(labels: list[str], kw: Keywords, fallback: str) -> str:
    cands = [t for t in labels if 6 <= len(t) <= 120 and not DATE_ONLY.match(t) and not PERIOD_LINE.match(t)]
    for t in cands:
        if kw.detect_game(t) or any(w in t for w in kw.lottery_words):
            return t
    return cands[0] if cands else fallback


def _finish(lot: Lottery | None, app: dict) -> Lottery | None:
    if lot:
        lot.form = "app"  # 応募方法はアプリ（店頭受け取りでも、応募はアプリから）
        lot.origin = "app"
        lot.pref = app.get("pref")  # 地域のお店のアプリなら apps.yaml に pref / area を書く
        lot.area = app.get("area")
    return lot


def extract_pages(pages: list[str], app: dict, kw: Keywords, now: datetime) -> list[Lottery]:
    """pages: screen.to_html() の結果（1画面ずつ）。"""
    src = app_source(app)
    fallback = f"{src['name']} 抽選"
    found: list[Lottery] = []
    for page in pages:
        soup = clean_soup(f"<html><body><section>{page}</section></body></html>")
        blocks = candidate_blocks(soup, kw, now)
        if not blocks:
            # お知らせの詳細画面など、画面全体で1件のこともある
            text = dates.normalize(soup.get_text(" ", strip=True))
            if len(text) <= MAX_PAGE_TEXT and kw.is_lottery(text):
                lot = to_lottery(text, pick_title(_block_labels(soup), kw, fallback), src["url"], src, kw, now)
                if _finish(lot, app):
                    found.append(lot)
            continue
        for el in blocks:
            text = dates.normalize(el.get_text(" ", strip=True))
            lot = to_lottery(text, pick_title(_block_labels(el), kw, fallback), src["url"], src, kw, now)
            if _finish(lot, app):
                found.append(lot)
    return dedup(found)


def from_fields(r: dict, app: dict, kw: Keywords, now: datetime) -> Lottery | None:
    """画像読み取り（vision）の結果1件を、ほかと同じ日付ルールで Lottery にする。"""
    src = app_source(app)
    title = (r.get("title") or "").strip()
    parts = [title, (r.get("product") or "").strip()]
    if r.get("apply_period"):
        parts.append(f"応募期間 {r['apply_period']}")
    if r.get("announce"):
        parts.append(f"当選発表 {r['announce']}")
    if r.get("purchase_period"):
        parts.append(f"購入期間 {r['purchase_period']}")
    text = dates.normalize(" ".join(p for p in parts if p))
    lot = to_lottery(text, title or f"{src['name']} 抽選", src["url"], src, kw, now)
    if lot:
        lot.conditions = [str(c).strip() for c in (r.get("conditions") or []) if str(c).strip()][:3]
    return _finish(lot, app)


def _score(lot: Lottery) -> int:
    return sum(1 for v in (lot.apply_start, lot.announce, lot.purchase_end, lot.product) if v) + len(lot.conditions)


def dedup(lots: list[Lottery]) -> list[Lottery]:
    """スクロールで同じお知らせを2回読んだ時などに、1件にまとめる（情報が多い方を残す）。"""
    best: dict[tuple, Lottery] = {}
    for lot in lots:
        key = (lot.apply_end, lot.product or re.sub(r"\W", "", lot.title)[:16])
        cur = best.get(key)
        if cur is None or _score(lot) > _score(cur):
            best[key] = lot
    return list(best.values())
