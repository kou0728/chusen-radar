"""LivePocket（ショップが抽選の応募受付に使うサイト）から抽選を集める。

多くのカードショップ・量販店・書店は、店頭受け取りの抽選を LivePocket で受け付けています。
検索ページでタイトルごとのキーワード（例：ポケモンカード）を引き、
抽選ページ（/e/xxxx）を開いて「販売受付期間」「結果発表予定日」「会場（都道府県）」を読み取ります。

一度読んだ抽選ページは state に覚えておき、次回からは開き直しません（相手サイトへの負担を減らすため）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from . import areas, dates, fetch
from .extract import Keywords, Lottery

log = logging.getLogger(__name__)
BASE = "https://livepocket.jp"
EVENT_RE = re.compile(r"(?:https?://(?:t\.)?livepocket\.jp)?/e/([A-Za-z0-9_\-]+)/?$")
STATUS_WORDS = ["販売中", "販売前", "受付中", "販売終了", "受付終了", "中止", "完売"]


def _text(el) -> str:
    return dates.normalize(el.get_text("\n", strip=True))


def parse_search(html: str) -> list[dict]:
    """検索結果ページから、イベントごとに {id, url, title, status, pref, text} を取り出す。"""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        m = EVENT_RE.search(a["href"].split("?")[0])
        if not m:
            continue
        eid = m.group(1)
        block = a
        # リンクがタイトルだけの場合は、カード全体（親要素）まで広げる
        while (block.parent is not None and len(_text(block)) < 25 and block.parent.name not in ("body", "html")
               and sum(1 for x in block.parent.find_all("a", href=True) if EVENT_RE.search(x["href"].split("?")[0])) <= 1):
            block = block.parent
        text = _text(block)
        lines = [l for l in text.split("\n") if l.strip()]
        title = next((l for l in lines if ("抽選" in l or "購入券" in l) and len(l) > 6), lines[0] if lines else "")
        status = next((w for w in STATUS_WORDS if w in text), "")
        cur = out.get(eid)
        if cur is None or len(text) > len(cur["text"]):
            out[eid] = {"id": eid, "url": f"{BASE}/e/{eid}", "title": title, "status": status,
                        "pref": areas.find_pref(text), "text": text}
    return list(out.values())


def _card_venue(text: str) -> str:
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.strip().startswith("会場"):
            v = l.strip()[2:].strip(" :：") or (lines[i + 1] if i + 1 < len(lines) else "")
            return areas.PREF_RE.sub("", v).strip(" （）()")[:60]
    return ""


def _after(lines: list[str], label: str, span: int = 3) -> str:
    for i, l in enumerate(lines):
        if label in l:
            rest = l.split(label, 1)[1]
            return " ".join([rest] + lines[i + 1: i + 1 + span])
    return ""


def parse_event(html: str, now: datetime) -> dict:
    """抽選ページ（/e/xxxx）から期間・発表日・会場を読む。"""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    og = soup.find("meta", property="og:title")
    h1 = soup.find("h1")
    title = (og.get("content") if og and og.get("content") else (_text(h1) if h1 else "")).split("|")[0].strip()
    lines = [l for l in _text(soup.body or soup).split("\n") if l.strip()]

    apply_txt = _after(lines, "販売受付期間") or _after(lines, "抽選エントリー期間") or _after(lines, "受付期間")
    s, e = dates.parse_range(apply_txt, now) if apply_txt else (None, None)
    ann_txt = _after(lines, "結果発表予定日", 1) or _after(lines, "当選発表", 1)
    ann = dates.parse_point_ex(ann_txt, s or now, default_end=True) if ann_txt else None
    day_txt = _after(lines, "開催日", 2)
    ps, pe = dates.parse_range(day_txt, s or now) if day_txt else (None, None)
    if day_txt and not pe:
        pe = dates.parse_point(day_txt, s or now, default_end=True)
    venue = _after(lines, "会場", 0).strip(" :：")
    if not venue:
        idx = next((i for i, l in enumerate(lines) if l.strip() == "会場"), None)
        venue = lines[idx + 1] if idx is not None and idx + 1 < len(lines) else ""
    venue_block = _after(lines, "会場", 4)
    return {
        "title": title,
        "apply_start": dates.iso(s), "apply_end": dates.iso(e),
        "announce": dates.iso(ann[0]) if ann else None,
        "announce_allday": bool(ann and not ann[1]),
        "purchase_start": dates.iso(ps), "purchase_end": dates.iso(pe),
        "venue": venue[:60],
        "pref": areas.find_pref(venue_block) or areas.find_pref(" ".join(lines)),
    }


def collect(src: dict, ua: str, kw: Keywords, state: dict, now: datetime) -> tuple[list[Lottery], dict]:
    cache: dict = state.setdefault("livepocket", {})
    found: dict[str, dict] = {}
    pages_ok = 0
    for word in src.get("keywords", []):
        for page in range(1, int(src.get("max_pages", 3)) + 1):
            url = f"{BASE}/event/search?word={quote(word)}" + (f"&page={page}" if page > 1 else "")
            try:
                html = fetch.fetch_html(url, ua)
            except fetch.FetchError as e:
                log.warning("LivePocket検索 %s: %s", word, e)
                break
            pages_ok += 1
            hits = parse_search(html)
            useful = [h for h in hits if kw.is_lottery(h["title"]) and kw.detect_game(h["title"])]
            for h in useful:
                found.setdefault(h["id"], h)
            if len(hits) < 15 or not useful:
                break  # 最後のページ

    budget = int(src.get("max_details", 60))
    out: list[Lottery] = []
    for eid, h in found.items():
        if h["status"] in ("中止",):
            continue
        info = cache.get(eid)
        if info is None or (h["status"] in ("販売中", "受付中", "販売前") and not info.get("apply_end")):
            if budget <= 0:
                continue
            budget -= 1
            try:
                info = parse_event(fetch.fetch_html(h["url"], ua), now)
                info["fetched"] = dates.iso(now)
                cache[eid] = info
            except fetch.FetchError as e:
                log.warning("LivePocket %s: %s", eid, e)
                continue
        if not info.get("apply_end"):
            continue
        title = info.get("title") or h["title"]
        game = kw.detect_game(title) or kw.detect_game(h["title"])
        store = info.get("venue") or _card_venue(h["text"]) or h["title"]
        lot = Lottery(
            store=store, title=title, url=h["url"], game=game,
            product=kw.detect_product(title, game), form="store",
            apply_start=info.get("apply_start"), apply_end=info["apply_end"],
            announce=info.get("announce"), announce_allday=info.get("announce_allday", False),
            purchase_start=info.get("purchase_start"), purchase_end=info.get("purchase_end"),
            conditions=["LivePocketでWEB応募・店頭で受け取り"],
            pref=info.get("pref") or h.get("pref"),
            source_id=src["id"], source_url=BASE,
        )
        out.append(lot)
    # 古いキャッシュは捨てる（60日）
    for k in [k for k, v in cache.items() if v.get("apply_end") and (now - datetime.fromisoformat(v["apply_end"])).days > 60]:
        del cache[k]
    return out, {"ok": pages_ok > 0, "found": len(out), "note": None if pages_ok else "検索ページを取得できませんでした"}
