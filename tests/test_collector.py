from datetime import datetime
from pathlib import Path

import pytest

from collector import dates
from collector.extract import extract
from collector.main import load_keywords

NOW = datetime(2026, 11, 18, 11, 0, tzinfo=dates.JST)
FX = Path(__file__).parent / "fixtures"
KW = load_keywords()


@pytest.mark.parametrize("text,start,end", [
    ("2025年6月20日(金)10時00分~2025年7月4日(金)16時59分", "2025-06-20T10:00+09:00", "2025-07-04T16:59+09:00"),
    ("11/13 10:00〜11/18 23:59", "2026-11-13T10:00+09:00", "2026-11-18T23:59+09:00"),
    ("〜2026/11/20", None, "2026-11-20T23:59+09:00"),
    ("12/28〜1/5", "2026-12-28T00:00+09:00", "2027-01-05T23:59+09:00"),
    ("11月18日(水)12:00～11月20日(金)23:59", "2026-11-18T12:00+09:00", "2026-11-20T23:59+09:00"),
    ("11/14(金)午前中 〜 11/15(土)23:59", "2026-11-14T09:00+09:00", "2026-11-15T23:59+09:00"),
    ("受付 11/1 午後3:00〜11/2 24:00", "2026-11-01T15:00+09:00", "2026-11-02T23:59+09:00"),
])
def test_ranges(text, start, end):
    s, e = dates.parse_range(text, NOW)
    assert dates.iso(s) == start and dates.iso(e) == end


def test_pokecen_list_picks_only_tcg():
    src = {"id": "pc", "name": "ポケモンセンターオンライン", "url": "https://www.pokemoncenter-online.com/lottery/apply.html", "form": "web"}
    lots = extract((FX / "pokecen_list.html").read_text(), src, KW, NOW)
    assert len(lots) == 1
    l = lots[0]
    assert l.game == "pokemon" and l.product == "ハドウシーカー"
    assert l.apply_end == "2026-11-10T16:59+09:00"
    assert l.announce == "2026-11-17T15:00+09:00"
    assert l.url == "https://www.pokemoncenter-online.com/9900000008482.html"


def test_shop_news():
    src = {"id": "s", "name": "サンプルショップ", "url": "https://example-shop.jp/news/"}
    lots = extract((FX / "shop_news.html").read_text(), src, KW, NOW)
    assert len(lots) == 1
    l = lots[0]
    assert l.game == "onepiece" and l.product == "神の支配"
    assert (l.apply_start, l.apply_end) == ("2026-11-18T12:00+09:00", "2026-11-20T23:59+09:00")
    assert l.purchase_end == "2026-11-24T23:59+09:00"
    assert l.announce_allday and l.form == "web"
    assert l.url == "https://example-shop.jp/lottery/op18"


def test_single_page():
    src = {"id": "p", "name": "平和堂", "url": "https://example.jp/p"}
    lots = extract((FX / "single_page.html").read_text(), src, KW, NOW)
    assert len(lots) == 1
    assert lots[0].form == "app"
    assert lots[0].apply_end == "2026-11-15T23:59+09:00"


def test_manual_yaml_parses_everything():
    from collector.main import load_yaml, manual_items
    raw = [m for m in load_yaml("manual.yaml") if not m.get("hide")]
    items = manual_items(KW, NOW)
    assert len(items) == len(raw)
    assert {i["game"] for i in items} == {"pokemon", "onepiece"}


def test_keywords_are_all_strings():
    for g in KW.games.values():
        assert all(isinstance(w, str) for w in g["match"] + g["products"])


def test_tournament_entry_is_not_a_sale():
    html = "<html><body><article><h2>遊戯王OCG 大会 参加抽選のお知らせ</h2><p>申込期間 11/20〜11/25</p></article></body></html>"
    src = {"id": "y", "name": "遊戯王OCG公式", "url": "https://example.jp/", "game": "yugioh"}
    assert extract(html, src, KW, NOW) == []


def test_livepocket_search_and_event():
    from collector import livepocket
    hits = livepocket.parse_search((FX / "lp_search.html").read_text())
    by_id = {h["id"]: h for h in hits}
    assert by_id["l9z_p"]["pref"] == "東京都" and by_id["l9z_p"]["status"] == "販売中"
    assert by_id["sd_2-"]["pref"] == "千葉県"
    assert by_id["xxxx1"]["status"] == "中止"
    useful = [h for h in hits if KW.is_lottery(h["title"]) and KW.detect_game(h["title"])]
    assert {h["id"] for h in useful} == {"l9z_p", "sd_2-", "xxxx1"}  # 入場券・無関係イベントは除外
    ev = livepocket.parse_event((FX / "lp_event.html").read_text(), NOW)
    assert ev["apply_start"] == "2026-11-14T13:00+09:00" and ev["apply_end"] == "2026-11-23T23:59+09:00"
    assert ev["announce"] == "2026-11-24T23:59+09:00" and ev["announce_allday"]
    assert ev["purchase_end"] == "2026-11-28T23:59+09:00"
    assert ev["venue"] == "CARDWINGS八王子駅前店" and ev["pref"] == "東京都"


def test_area_rules():
    from collector import areas
    assert areas.apply({"store": "カードラボ 所沢店", "form": "store"})["area"] == "saitama"
    assert areas.apply({"store": "ポケモンセンターオンライン", "form": "web"})["area"] == "national"
    assert areas.apply({"store": "謎のお店", "form": "store"})["area"] == "other"
    assert areas.apply({"store": "ビックカメラ", "form": "store", "area": "national"})["area"] == "national"
    assert areas.apply({"store": "X", "form": "store", "pref": "神奈川県"})["area"] == "other"
