"""HTMLから抽選情報を取り出す汎用エクストラクタ。

サイトごとにセレクタを書かなくても動くように、
「抽選キーワード」と「日付の期間」が両方入っている一番小さいまとまり（li / tr / article / div …）を
1件の抽選として扱います。ページ全体が1件の抽選ページなら、ページ全体を1件として扱います。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from . import dates

BLOCK_TAGS = ["li", "tr", "article", "section", "div", "dl", "dd", "td", "p"]
NOISE_TAGS = ["script", "style", "noscript", "nav", "footer", "header", "form", "svg", "iframe"]

FORM_WORDS = {
    "app": ["アプリ"],
    "store": ["店頭", "店舗受け取り", "店舗受取", "来店", "QRコード"],
    "web": ["WEB", "Web", "web", "オンライン", "通販", "ネット"],
}


@dataclass
class Keywords:
    games: dict
    lottery_words: list[str]
    exclude_words: list[str]

    def detect_game(self, text: str) -> str | None:
        for gid, g in self.games.items():
            if any(w in text for w in g.get("match", [])) or any(p in text for p in g.get("products", [])):
                return gid
        return None

    def detect_product(self, text: str, game: str | None) -> str | None:
        pools = [self.games[game]] if game in self.games else list(self.games.values())
        for g in pools:
            for p in g.get("products", []):
                if p in text:
                    return p
        return None

    def is_lottery(self, text: str) -> bool:
        return any(w in text for w in self.lottery_words) and not any(w in text for w in self.exclude_words)


@dataclass
class Lottery:
    store: str
    title: str
    url: str
    game: str | None = None
    product: str | None = None
    form: str | None = None
    apply_start: str | None = None
    apply_end: str | None = None
    announce: str | None = None
    purchase_start: str | None = None
    purchase_end: str | None = None
    conditions: list[str] = field(default_factory=list)
    announce_allday: bool = False
    pref: str | None = None
    area: str | None = None
    source_id: str = ""
    source_url: str = ""
    origin: str = "auto"

    @property
    def id(self) -> str:
        key = "|".join([self.store, self.product or self.title[:40], (self.apply_end or "")[:10], self.url.split("?")[0]])
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["id"] = self.id
        return d


def clean_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(NOISE_TAGS):
        t.decompose()
    return soup


def _text(el: Tag) -> str:
    return dates.normalize(el.get_text(" ", strip=True))


def _has_period(text: str, now: datetime) -> bool:
    return dates.parse_range(text, now)[1] is not None


def candidate_blocks(soup: BeautifulSoup, kw: Keywords, now: datetime) -> list[Tag]:
    hits: list[Tag] = []
    for el in soup.find_all(BLOCK_TAGS):
        t = _text(el)
        if not (15 <= len(t) <= 1500):
            continue
        if kw.is_lottery(t) and _has_period(t, now):
            hits.append(el)
    # 入れ子になっているものは一番内側だけ残す
    hit_set = set(map(id, hits))
    inner = [el for el in hits if not any(id(d) in hit_set for d in el.find_all(BLOCK_TAGS))]
    inner_set = set(map(id, inner))
    # 一番内側は「応募期間 …」の1行だけのことが多いので、
    # ほかの抽選を含まない範囲で外側へ広げ、商品名やリンクまで含める
    keep, seen = [], set()
    for el in inner:
        cur = el
        while cur.parent is not None and cur.parent.name not in ("body", "html", "[document]"):
            p = cur.parent
            n = sum(1 for d in p.find_all(BLOCK_TAGS) if id(d) in inner_set)
            if n > 1 or len(_text(p)) > 2000:
                break
            cur = p
        if id(cur) not in seen:
            seen.add(id(cur))
            keep.append(cur)
    return keep


def _title_of(el: Tag, fallback: str) -> str:
    for sel in ["h1", "h2", "h3", "h4", "strong", "b", "a", "dt", "th"]:
        c = el.find(sel)
        if c:
            t = _text(c)
            if 4 <= len(t) <= 120:
                return t
    first = _text(el).split(" ")
    t = " ".join(first[:12])
    return t[:100] or fallback


def _link_of(el: Tag, base: str) -> str:
    a = el if el.name == "a" else el.find("a", href=True)
    if a and a.get("href") and not a["href"].startswith(("#", "javascript")):
        return urljoin(base, a["href"])
    return base


def _form_of(text: str, default: str | None) -> str | None:
    for form, words in FORM_WORDS.items():
        if any(w in text for w in words):
            return form
    return default


CONDITION_RE = re.compile(r"(?:応募条件|条件|対象)[:：]?\s*([^。]{4,80})")


def to_lottery(text: str, title: str, url: str, src: dict, kw: Keywords, now: datetime) -> Lottery | None:
    game = kw.detect_game(title + " " + text) or src.get("game")
    if not game:
        return None  # トレカ以外の抽選（家電・ゲーム機など）は載せない
    applied = dates.find_labeled(text, "apply", now) or dates.parse_range(text, now)
    start, end = applied if applied else (None, None)
    if not end:
        return None
    ann = dates.find_labeled(text, "announce", now)
    allday = bool(ann and ann.hour == 0 and ann.minute == 0)
    if allday:
        ann = ann.replace(hour=23, minute=59)
    pur = dates.find_labeled(text, "purchase", now)
    conds = [c.strip() for c in CONDITION_RE.findall(text)][:3]
    return Lottery(
        store=src.get("store") or src["name"],
        title=title,
        url=url,
        game=game,
        product=kw.detect_product(title + " " + text, game),
        form=_form_of(text, src.get("form")),
        apply_start=dates.iso(start),
        apply_end=dates.iso(end),
        announce=dates.iso(ann) if ann and not isinstance(ann, tuple) else None,
        purchase_start=dates.iso(pur[0]) if pur else None,
        purchase_end=dates.iso(pur[1]) if pur else None,
        conditions=conds,
        announce_allday=allday,
        source_id=src["id"],
        source_url=src.get("url", ""),
    )


def extract(html: str, src: dict, kw: Keywords, now: datetime | None = None) -> list[Lottery]:
    now = now or datetime.now(dates.JST)
    soup = clean_soup(html)
    base = src["url"]
    out: dict[str, Lottery] = {}
    blocks = candidate_blocks(soup, kw, now)
    if not blocks:
        # ページ全体で1件の抽選ページ（商品ページ・告知記事）として読む
        body = soup.body or soup
        text = _text(body)
        if kw.is_lottery(text):
            h = soup.find(["h1", "title"])
            lot = to_lottery(text, _text(h) if h else src["name"], base, src, kw, now)
            if lot:
                out[lot.id] = lot
    for el in blocks:
        text = _text(el)
        lot = to_lottery(text, _title_of(el, src["name"]), _link_of(el, base), src, kw, now)
        if lot:
            out.setdefault(lot.id, lot)
    return list(out.values())


def page_fingerprint(html: str) -> str:
    soup = clean_soup(html)
    main = soup.find("main") or soup.body or soup
    text = re.sub(r"\s+", "", main.get_text())
    return hashlib.sha1(text.encode()).hexdigest()


def extract_from_post(text: str, url: str, src: dict, kw: Keywords, now: datetime) -> Lottery | None:
    """Xの投稿1件から抽選を読む。"""
    t = dates.normalize(text)
    if not kw.is_lottery(t):
        return None
    first_line = t.split("\n")[0][:80]
    return to_lottery(t, first_line, url, {**src, "form": src.get("form")}, kw, now)
