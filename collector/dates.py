"""日本語の日付・期間表記をパースする。

対応例:
  2025年6月20日(金)10時00分~2025年7月4日(金)16時59分
  11月18日(水)10:00〜11月20日(金)23:59
  11/13 10:00〜11/18 23:59
  〜11/20
  11/14 午前中〜11/15 23:59
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

_WD = r"(?:[（(][月火水木金土日祝・]+[)）])?"
_YEAR = r"(?:(?P<{p}y>20\d{{2}})\s*[年/.\-]\s*)?"
_MD = r"(?P<{p}m>1[0-2]|0?[1-9])\s*(?:月|/|\.)\s*(?P<{p}d>3[01]|[12]\d|0?[1-9])\s*日?"
_TIME = (
    r"(?:\s*(?P<{p}ampm>午前|午後|AM|PM)?\s*"
    r"(?P<{p}H>[01]?\d|2[0-4])\s*(?:[:：]|時)\s*(?P<{p}M>[0-5]\d)?\s*分?"
    r"|\s*(?P<{p}fuzzy>午前中|午後|正午|昼頃|夕方|夜))?"
)


def _point(prefix: str) -> str:
    return (_YEAR + _MD + r"\s*" + _WD + _TIME).format(p=prefix)


DATE_RE = re.compile(_point("a"))
RANGE_SEP = r"\s*(?:〜|~|～|ー|－|–|—|-|から|より)\s*"
RANGE_RE = re.compile(
    r"(?:" + _point("s") + r")?" + RANGE_SEP + _point("e")
)

FUZZY_HOURS = {"午前中": (9, 0), "午後": (13, 0), "正午": (12, 0), "昼頃": (12, 0), "夕方": (17, 0), "夜": (20, 0)}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("〜", "〜").replace("~", "〜")
    return re.sub(r"[ \t　]+", " ", text)


def _infer_year(month: int, day: int, now: datetime) -> int:
    """年が書かれていない日付は、基準日に最も近くなる年を選ぶ。"""
    best = None
    for y in (now.year - 1, now.year, now.year + 1):
        try:
            cand = datetime(y, month, day, tzinfo=JST)
        except ValueError:
            continue
        diff = abs((cand - now).total_seconds())
        if best is None or diff < best[0]:
            best = (diff, y)
    return best[1] if best else now.year


def _build(gd: dict, p: str, now: datetime, default_end: bool, year_hint: int | None = None):
    if not gd.get(p + "m"):
        return None
    m, d = int(gd[p + "m"]), int(gd[p + "d"])
    y = int(gd[p + "y"]) if gd.get(p + "y") else (year_hint or _infer_year(m, d, now))
    has_time = True
    if gd.get(p + "H") is not None:
        H = int(gd[p + "H"])
        M = int(gd.get(p + "M") or 0)
        ampm = gd.get(p + "ampm")
        if ampm in ("午後", "PM") and H < 12:
            H += 12
    elif gd.get(p + "fuzzy"):
        H, M = FUZZY_HOURS[gd[p + "fuzzy"]]
    else:
        has_time = False
        H, M = (23, 59) if default_end else (0, 0)
    try:
        if H == 24:
            dt = datetime(y, m, d, 23, 59, tzinfo=JST)
        else:
            dt = datetime(y, m, d, H, M, tzinfo=JST)
    except ValueError:
        return None
    return dt, has_time


def parse_range(text: str, now: datetime | None = None):
    """文字列から最初の期間を探して (start, end) を返す。片方だけのこともある。"""
    now = now or datetime.now(JST)
    t = normalize(text)
    m = RANGE_RE.search(t)
    if m:
        gd = m.groupdict()
        start = _build(gd, "s", now, default_end=False)
        # 終了側に年が無い場合は開始側の年を引き継ぐ（年またぎは補正）
        end = _build(gd, "e", now, default_end=True, year_hint=start[0].year if start and not gd.get("ey") else None)
        if start and end and end[0] < start[0]:
            end = (end[0].replace(year=end[0].year + 1), end[1])
        if end:
            return (start[0] if start else None, end[0])
    return (None, None)


def parse_point_ex(text: str, now: datetime | None = None, default_end: bool = False):
    """(日時, 時刻が書かれていたか) を返す。"""
    now = now or datetime.now(JST)
    m = DATE_RE.search(normalize(text))
    if not m:
        return None
    return _build(m.groupdict(), "a", now, default_end=default_end)


def parse_point(text: str, now: datetime | None = None, default_end: bool = False):
    r = parse_point_ex(text, now, default_end)
    return r[0] if r else None


LABELS = {
    "apply": r"(?:応募|受付|抽選|エントリー|申込|申し込み)(?:受け?付け?)?(?:期間|期限|日時)?",
    "announce": r"(?:当選|抽選結果|結果)(?:発表|通知|連絡|のご案内)?",
    "purchase": r"(?:(?:購入|お支払い|支払い?|注文|引き?換え?|受け?取り?)(?:期間|期限|日)?|販売(?:期間|日))",
}


def find_labeled(text: str, kind: str, now: datetime | None = None):
    """「当選発表 11/26」のようにラベルの直後にある日付・期間を拾う。"""
    now = now or datetime.now(JST)
    t = normalize(text)
    for m in re.finditer(LABELS[kind] + r"\s*[:：】\]）)]?\s*", t):
        window = t[m.end(): m.end() + 70]
        if kind in ("apply", "purchase"):
            rm = RANGE_RE.search(window)
            if rm and rm.start() <= 8:
                s, e = parse_range(window, now)
                if e:
                    return s, e
        dm = DATE_RE.search(window)
        if dm and dm.start() <= 8:
            p = parse_point(window, now, default_end=(kind != "announce"))
            if p:
                return p if kind == "announce" else (None, p)
    return None


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(JST).isoformat(timespec="minutes") if dt else None
