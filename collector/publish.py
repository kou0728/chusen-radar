"""site/ 以下に公開用ファイル（index.html, data.json, feed.xml, sitemap.xml）を書き出す。"""
from __future__ import annotations

import html
import json
import shutil
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path

import yaml

from . import areas
from .extract import Keywords

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web"
OUT = ROOT / "site"
FORM = {"web": "WEB", "app": "アプリ", "store": "店頭", "x": "X"}


def _site() -> dict:
    return yaml.safe_load((ROOT / "config" / "site.yaml").read_text(encoding="utf-8"))


def _sort_key(d: dict):
    return (d.get("apply_end") or "9999", d["store"])


def _fmt(iso: str | None) -> str:
    if not iso:
        return ""
    d = datetime.fromisoformat(iso)
    return f"{d.month}/{d.day}({'月火水木金土日'[d.weekday()]}) {d:%H:%M}"


def _prerender(items: list[dict], kw: Keywords, now: datetime) -> str:
    """JSが動かない環境・検索エンジン向けの素のリスト。JSが読み込まれると置き換わる。"""
    rows = []
    for d in items:
        if d.get("apply_end") and datetime.fromisoformat(d["apply_end"]) < now:
            continue
        game = kw.games.get(d.get("game") or "", {}).get("label", "")
        period = f"{_fmt(d.get('apply_start'))}〜{_fmt(d.get('apply_end'))}" if d.get("apply_end") else "受付中"
        rows.append(
            f'<li><a href="{html.escape(d.get("url") or "#")}" rel="nofollow noopener">'
            f'{html.escape(d["store"])}：{html.escape(d.get("product") or d["title"])}</a> '
            f'{html.escape(game)} {html.escape(FORM.get(d.get("form"), ""))} 受付 {html.escape(period)}</li>'
        )
    return '<ul class="prerender">' + "".join(rows) + "</ul>"


def write_site(state: dict, kw: Keywords, now: datetime):
    site = _site()
    items = sorted(state["items"].values(), key=_sort_key)
    games = {gid: g["label"] for gid, g in kw.games.items()}
    sources = [{"id": k, **v} for k, v in state.get("sources", {}).items()]
    payload = {"updated": state.get("updated"), "games": games, "areas": areas.labels(), "items": items, "sources": sources,
               "site": {k: site.get(k) for k in ("name", "url", "x_account", "contact_url", "amazon_tag")}}

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT)
    (OUT / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    data_js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    open_n = sum(1 for d in items if not d.get("apply_end") or datetime.fromisoformat(d["apply_end"]) >= now)
    repl = {
        "{{NAME}}": html.escape(site["name"]),
        "{{TAGLINE}}": html.escape(site["tagline"]),
        "{{DESCRIPTION}}": html.escape(site["description"]),
        "{{URL}}": html.escape(site["url"]),
        "{{OPERATOR}}": html.escape(site["operator"]),
        "{{UPDATED}}": html.escape(_fmt(state.get("updated"))),
        "{{OPEN_COUNT}}": str(open_n),
        "<!--PRERENDER-->": _prerender(items, kw, now),
        "/*DATA*/null": data_js,
    }
    for page in OUT.glob("*.html"):
        t = page.read_text(encoding="utf-8")
        for k, v in repl.items():
            t = t.replace(k, v)
        page.write_text(t, encoding="utf-8")

    _feed(items, site, now)
    _sitemap(site, now)
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site['url']}sitemap.xml\n", encoding="utf-8")


def _feed(items: list[dict], site: dict, now: datetime):
    recent = sorted(items, key=lambda d: d.get("first_seen") or "", reverse=True)[:50]
    entries = []
    for d in recent:
        title = f"【{d['store']}】{d.get('product') or d['title']} 抽選"
        desc = f"受付 {_fmt(d.get('apply_start'))}〜{_fmt(d.get('apply_end'))}" if d.get("apply_end") else "受付中"
        if d.get("announce"):
            desc += f" / 当選発表 {_fmt(d['announce'])}"
        pub = datetime.fromisoformat(d.get("first_seen") or now.isoformat())
        entries.append(
            "<item>"
            f"<title>{html.escape(title)}</title>"
            f"<link>{html.escape(d.get('url') or site['url'])}</link>"
            f"<guid isPermaLink=\"false\">{d['id']}</guid>"
            f"<pubDate>{format_datetime(pub)}</pubDate>"
            f"<description>{html.escape(desc)}</description>"
            "</item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{html.escape(site['name'])}</title><link>{html.escape(site['url'])}</link>"
        f"<description>{html.escape(site['description'])}</description><language>ja</language>"
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>" + "".join(entries) + "</channel></rss>"
    )
    (OUT / "feed.xml").write_text(xml, encoding="utf-8")


def _sitemap(site: dict, now: datetime):
    urls = [site["url"], site["url"] + "about.html"]
    body = "".join(f"<url><loc>{html.escape(u)}</loc><lastmod>{now.date().isoformat()}</lastmod></url>" for u in urls)
    (OUT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
        encoding="utf-8",
    )
