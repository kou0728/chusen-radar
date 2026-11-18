"""巡回 → 抽出 → 既存データと統合 → サイト用ファイルを書き出す。

  python -m collector.main            # 全ソースを巡回して site/ を更新
  python -m collector.main --offline  # 巡回せず、手動登録と既存データだけで site/ を作り直す
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from . import areas, dates, fetch, formsource, notify, publish, xsource
from .extract import Keywords, Lottery, extract, page_fingerprint

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
STATE = ROOT / "data" / "state.json"
KEEP_DAYS = 45  # 受付終了から何日まで掲載を残すか

log = logging.getLogger("collector")


def load_yaml(name: str):
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def load_keywords() -> Keywords:
    k = load_yaml("keywords.yaml")
    return Keywords(games=k["games"], lottery_words=k["lottery_words"], exclude_words=k.get("exclude_words", []))


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"items": {}, "fingerprints": {}, "sources": {}}


def manual_items(kw: Keywords, now: datetime) -> list[dict]:
    out = []
    for i, m in enumerate(load_yaml("manual.yaml") or []):
        if m.get("hide"):
            continue
        s = e = None
        if m.get("apply"):
            s, e = dates.parse_range(str(m["apply"]), now)
            if not e:
                e = dates.parse_point(str(m["apply"]), now, default_end=True)
        if not e and not m.get("ongoing"):
            log.warning("manual.yaml %d件目: 受付期間が読めません (%s)", i + 1, m.get("store"))
            continue
        ref = s or e or now
        ann_r = dates.parse_point_ex(str(m["announce"]), ref, default_end=True) if m.get("announce") else None
        ann = ann_r[0] if ann_r else None
        ps = pe = None
        if m.get("purchase"):
            ps, pe = dates.parse_range(str(m["purchase"]), ref)
        product = m.get("product")
        lot = Lottery(
            store=m["store"],
            title=m.get("title") or (f"{product} 抽選" if product else "抽選販売"),
            url=m.get("url") or "",
            game=m.get("game") or kw.detect_game(f"{product} {m.get('title', '')}"),
            product=product,
            form=m.get("form"),
            apply_start=dates.iso(s),
            apply_end=dates.iso(e),
            announce=dates.iso(ann),
            purchase_start=dates.iso(ps),
            purchase_end=dates.iso(pe),
            conditions=m.get("conditions") or [],
            source_id="manual",
            origin="manual",
        )
        lot.pref = m.get("pref")
        lot.area = m.get("area")
        d = lot.to_dict()
        d["ongoing"] = bool(m.get("ongoing"))
        d["announce_allday"] = bool(ann_r and not ann_r[1])
        out.append(d)
    return out


def crawl(sources: list[dict], ua: str, kw: Keywords, state: dict, now: datetime):
    found: list[dict] = []
    changed: list[dict] = []
    for src in sources:
        if not src.get("enabled", True) or src["type"] == "x_user":
            continue
        st = {"name": src["name"], "url": src.get("url", "https://livepocket.jp/"), "checked": dates.iso(now)}
        try:
            html = fetch.fetch_html(src["url"], ua, render=src.get("render", False))
            fp = page_fingerprint(html)
            prev = state["fingerprints"].get(src["id"])
            if prev and prev != fp:
                changed.append(src)
                st["changed"] = dates.iso(now)
            state["fingerprints"][src["id"]] = fp
            if src["type"] == "page":
                lots = extract(html, src, kw, now)
                found += [l.to_dict() for l in lots]
                st["found"] = len(lots)
            st["ok"] = True
        except fetch.FetchError as e:
            st.update(ok=False, note=str(e)[:160])
            log.warning("%s: %s", src["name"], e)
        except Exception as e:  # 1サイトの失敗で全体を止めない
            st.update(ok=False, note=f"想定外のエラー: {e}"[:160])
            log.exception("%s", src["name"])
        prev_st = state["sources"].get(src["id"], {})
        if "changed" not in st and prev_st.get("changed"):
            st["changed"] = prev_st["changed"]
        state["sources"][src["id"]] = st
    fetch.close()
    xs = [s for s in sources if s["type"] == "x_user" and s.get("enabled", True)]
    x_lots, x_status = xsource.collect(xs, kw, now)
    found += [l.to_dict() for l in x_lots]
    for sid, st in x_status.items():
        src = next(s for s in xs if s["id"] == sid)
        state["sources"][sid] = {"name": src["name"], "url": f"https://x.com/{src['account']}", "checked": dates.iso(now), **st}
    return found, changed


def merge(state: dict, auto: list[dict], manual: list[dict], now: datetime, form_ok: bool = False) -> list[dict]:
    """新しく見つかったものを追加。手動登録は自動取得より優先。古いものは消す。"""
    items: dict = state["items"]
    new_ids = []
    stamp = dates.iso(now)
    for d in auto + manual:
        areas.apply(d)
        cur = items.get(d["id"])
        if cur is None:
            d["first_seen"] = stamp
            new_ids.append(d["id"])
        elif cur.get("origin") in ("manual", "form") and d.get("origin") == "auto":
            cur["last_seen"] = stamp
            continue
        else:
            d["first_seen"] = cur.get("first_seen", stamp)
        d["last_seen"] = stamp
        items[d["id"]] = d
    # 手動登録から消された／hideにされたものは削除
    manual_ids = {d["id"] for d in manual}
    gone = ("manual", "form") if form_ok else ("manual",)  # フォームが読めなかった回は、フォーム分を消さない
    for k in [k for k, v in items.items() if v.get("origin") in gone and k not in manual_ids]:
        del items[k]
    cutoff = now - timedelta(days=KEEP_DAYS)
    for k in [k for k, v in items.items() if v.get("apply_end") and datetime.fromisoformat(v["apply_end"]) < cutoff]:
        del items[k]
    return [items[i] for i in new_ids if i in items]


def run(offline: bool = False, now: datetime | None = None) -> int:
    now = now or datetime.now(dates.JST)
    srcs = load_yaml("sources.yaml")
    ua = srcs["defaults"]["user_agent"]
    kw = load_keywords()
    state = load_state()
    auto, changed = ([], []) if offline else crawl(srcs["sources"], ua, kw, state, now)
    manual = manual_items(kw, now)
    endpoint = (load_yaml("site.yaml") or {}).get("form_endpoint")
    form_ok = False
    if endpoint and not offline:
        form, st = formsource.collect(endpoint, kw)
        form_ok = st["ok"]
        state["sources"]["form"] = {"name": "情報提供フォーム（掲載OKの投稿）", "url": "", "checked": dates.iso(now), **st}
        manual = manual + form
    new = merge(state, auto, manual, now, form_ok=form_ok)
    state["updated"] = dates.iso(now)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    publish.write_site(state, kw, now)
    if not offline:
        notify.send(new, changed)
    log.info("掲載 %d件 / 新着 %d件 / ページ更新 %d件", len(state["items"]), len(new), len(changed))
    return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--now", help="基準時刻(テスト用) 例 2026-11-18T11:00+09:00")
    a = ap.parse_args()
    now = datetime.fromisoformat(a.now) if a.now else None
    sys.exit(run(offline=a.offline, now=now))


if __name__ == "__main__":
    main()
