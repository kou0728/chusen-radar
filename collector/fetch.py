"""ページ取得。robots.txt を守り、間隔をあけ、必要ならヘッドレスブラウザで描画する。"""
from __future__ import annotations

import logging
import time
import urllib.robotparser
from functools import lru_cache
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

_session = requests.Session()
_last_hit: dict[str, float] = {}
MIN_INTERVAL_PER_HOST = 3.0  # 同じサイトへは3秒以上あける


class FetchError(Exception):
    pass


@lru_cache(maxsize=128)
def _robots(origin: str, ua: str) -> urllib.robotparser.RobotFileParser | None:
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = _session.get(origin + "/robots.txt", timeout=10, headers={"User-Agent": ua})
        if r.status_code >= 400:
            return None  # robots.txt が無ければ制限なし
        rp.parse(r.text.splitlines())
        return rp
    except requests.RequestException:
        return None


def allowed(url: str, ua: str) -> bool:
    p = urlparse(url)
    rp = _robots(f"{p.scheme}://{p.netloc}", ua)
    return True if rp is None else rp.can_fetch(ua, url)


def _throttle(host: str):
    wait = MIN_INTERVAL_PER_HOST - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()


def fetch_html(url: str, ua: str, render: bool = False, retries: int = 2) -> str:
    if not allowed(url, ua):
        raise FetchError("robots.txt で巡回が禁止されています")
    _throttle(urlparse(url).netloc)
    if render:
        return _fetch_rendered(url, ua)
    last = None
    for i in range(retries + 1):
        try:
            r = _session.get(url, timeout=20, headers={"User-Agent": ua, "Accept-Language": "ja"})
            if r.status_code == 200:
                r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
                return r.text
            last = f"HTTP {r.status_code}"
            if r.status_code in (403, 404, 410):
                break
        except requests.RequestException as e:
            last = str(e)
        time.sleep(2 * (i + 1))
    raise FetchError(last or "取得に失敗しました")


_browser = None


def _fetch_rendered(url: str, ua: str) -> str:
    global _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise FetchError("playwright が入っていません") from e
    if _browser is None:
        pw = sync_playwright().start()
        _browser = pw.chromium.launch()
    ctx = _browser.new_context(user_agent=ua, locale="ja-JP")
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(1500)
        return page.content()
    except Exception as e:
        raise FetchError(f"描画に失敗: {e}") from e
    finally:
        ctx.close()


def close():
    global _browser
    if _browser is not None:
        _browser.close()
        _browser = None
