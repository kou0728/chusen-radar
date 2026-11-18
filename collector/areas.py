"""都道府県とエリア（東京・千葉・埼玉・通販全国・その他）の判定。"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

PREFS = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
    "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]
PREF_RE = re.compile("|".join(PREFS))


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((ROOT / "config" / "areas.yaml").read_text(encoding="utf-8"))


def find_pref(text: str) -> str | None:
    m = PREF_RE.search(text or "")
    return m.group(0) if m else None


def guess_pref(store: str) -> str | None:
    """店名に入っている地名から都道府県を推定する（例：カードラボ所沢店 → 埼玉県）。"""
    p = find_pref(store)
    if p:
        return p
    for pref, words in config().get("place_hints", {}).items():
        if any(w in store for w in words):
            return pref
    return None


def area_of(pref: str | None, form: str | None, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if pref:
        for a in config()["areas"]:
            if pref in a.get("prefs", []):
                return a["id"]
        return "other"
    # 住所が無い：通販・アプリは全国、店頭やXの個別店は地域不明として「その他」
    return "national" if form in ("web", "app") else "other"


def apply(d: dict) -> dict:
    """抽選1件（dict）に pref と area を埋める。"""
    if not d.get("pref") and d.get("area") != "national":
        d["pref"] = guess_pref(d.get("store") or "")
    d["area"] = area_of(d.get("pref"), d.get("form"), d.get("area"))
    return d


def labels() -> list[dict]:
    return [{"id": a["id"], "label": a["label"]} for a in config()["areas"]]
