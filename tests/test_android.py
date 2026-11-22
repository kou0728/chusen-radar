"""Android端末まわり（画面の読み取り・レシピ・サイトへの取り込み）と、X APIの読み方のテスト。

実機は使わず、画面ダンプのXMLと、端末のふりをする FakeDevice で確かめる。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from android_agent import agent, screen
from android_agent.extract import extract_pages, from_fields
from android_agent.recipe import RecipeError, Runner, parse_step
from collector import dates, main, publish, xsource
from collector.main import load_keywords

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=dates.JST)
FX = Path(__file__).parent / "fixtures"
KW = load_keywords()
APP = {"id": "testshop", "store": "テスト電機", "name": "テスト電機アプリ", "package": "jp.example.shopapp",
       "url": "https://example.jp/app"}


def ui(*rows, pkg="jp.example.shopapp") -> str:
    """縦に並んだボタン（文字）だけの簡単な画面ダンプを作る。"""
    nodes = "".join(
        f'<node text="{t}" class="android.widget.TextView" package="{pkg}" content-desc="" resource-id="" '
        f'clickable="true" bounds="[0,{200 + i * 150}][1080,{330 + i * 150}]" />'
        for i, t in enumerate(rows)
    )
    return ('<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">'
            f'<node text="" class="android.widget.FrameLayout" package="{pkg}" content-desc="" resource-id="" '
            f'clickable="false" bounds="[0,0][1080,2400]">{nodes}</node></hierarchy>')


class FakeDevice:
    """画面ダンプを順番に返す偽の端末。押す・スクロールすると次の画面に進む。"""

    def __init__(self, screens):
        self.screens = list(screens)
        self.i = 0
        self.calls = []

    def dump(self):
        return self.screens[min(self.i, len(self.screens) - 1)]

    def _next(self, *call):
        self.calls.append(call)
        self.i += 1

    def tap(self, x, y):
        self._next("tap", x, y)

    def scroll(self, direction="down"):
        self._next("scroll", direction)

    def back(self):
        self._next("back")

    def launch(self, package):
        self.calls.append(("launch", package))

    def force_stop(self, package):
        self.calls.append(("stop", package))

    def home(self):
        self.calls.append(("home",))

    def installed(self, package):
        return True

    def screenshot(self):
        return b"\x89PNG fake"


def run_steps(dev, steps, **kw):
    r = Runner(dev, APP, sleep=lambda s: None, say=lambda m: None, **kw)
    r.run(steps)
    return r


# --- 画面の読み取り ---
def test_screen_parse_find_and_skip_status_bar():
    root = screen.parse((FX / "app_news.xml").read_text(encoding="utf-8"))
    labels = screen.labels(root)
    assert "10:12" not in labels  # ステータスバーの時計は読まない
    assert "マイページ" in labels  # 文字の無いボタンは content-desc を使う
    assert any("ハドウシーカー" in t for t in labels)  # 絵文字の壊れた文字参照があっても読める
    nav = screen.find(root, rid="nav_news")
    assert nav.center == (675, 2300)
    assert screen.find(root, text="お知らせ", index=1).center == (675, 2300)
    assert screen.node_at(root, 600, 2300).label == "お知らせ"


def test_extract_app_news_list():
    root = screen.parse((FX / "app_news.xml").read_text(encoding="utf-8"))
    lots = sorted(extract_pages([screen.to_html(root)], APP, KW, NOW), key=lambda l: l.game)
    assert [l.game for l in lots] == ["onepiece", "pokemon"]  # Switch 2 など、トレカ以外は載せない
    op, pk = lots
    assert pk.title.startswith("【抽選販売】ポケモンカードゲーム")  # 日付の行は見出しにしない
    assert pk.product == "ハドウシーカー" and pk.store == "テスト電機"
    assert (pk.apply_start, pk.apply_end) == ("2026-11-21T10:00+09:00", "2026-11-24T23:59+09:00")
    assert pk.announce == "2026-11-27T23:59+09:00" and pk.announce_allday
    assert op.product == "神の支配"
    assert (op.apply_start, op.apply_end) == ("2026-11-22T10:00+09:00", "2026-11-25T23:59+09:00")
    assert {l.form for l in lots} == {"app"} and {l.origin for l in lots} == {"app"}
    assert {l.url for l in lots} == {"https://example.jp/app"}
    assert {l.source_id for l in lots} == {"app:testshop"}


def test_same_lottery_on_two_scroll_pages_is_one():
    html = screen.to_html(screen.parse((FX / "app_news.xml").read_text(encoding="utf-8")))
    assert len(extract_pages([html, html], APP, KW, NOW)) == 2


def test_detail_screen_is_read_as_one_lottery():
    detail = ui("お知らせ詳細", "ポケモンカードゲーム「ハドウシーカー」抽選販売のご案内",
                "当店アプリ会員さま限定で抽選販売を行います。", "■応募期間",
                "11月21日(土)10:00〜11月24日(火)23:59", "■当選発表", "11月27日(金)",
                "応募条件：アプリ会員登録済みの方", "※お一人様1回限り")
    lots = extract_pages([screen.to_html(screen.parse(detail))], APP, KW, NOW)
    assert len(lots) == 1
    assert lots[0].title == "ポケモンカードゲーム「ハドウシーカー」抽選販売のご案内"
    assert lots[0].apply_end == "2026-11-24T23:59+09:00"


def test_area_from_apps_yaml():
    root = screen.parse((FX / "app_news.xml").read_text(encoding="utf-8"))
    lots = extract_pages([screen.to_html(root)], {**APP, "area": "other"}, KW, NOW)
    assert {l.area for l in lots} == {"other"}


# --- レシピ ---
def test_recipe_runs_steps_and_stops_scrolling_at_the_end():
    home = ui("閉じる", "ホーム", "お知らせ")
    after_popup = ui("ホーム", "お知らせ")
    news = (FX / "app_news.xml").read_text(encoding="utf-8")
    dev = FakeDevice([home, after_popup, news, news])  # スクロールしても同じ画面 = 一番下
    r = run_steps(dev, ["restart", {"dismiss": ["閉じる", "あとで"]}, {"tap": "お知らせ"}, {"read_pages": 5}])
    assert dev.calls[:2] == [("stop", "jp.example.shopapp"), ("launch", "jp.example.shopapp")]
    taps = [c for c in dev.calls if c[0] == "tap"]
    assert taps == [("tap", 540, 265), ("tap", 540, 415)]
    assert [c for c in dev.calls if c[0] == "scroll"] == [("scroll", "down")]
    assert len(r.pages) == 1


def test_recipe_never_presses_entry_or_purchase_buttons():
    dev = FakeDevice([ui("ポケカ抽選", "応募する", "購入手続きへ")])
    with pytest.raises(RecipeError, match="押しません"):
        run_steps(dev, [{"tap": "応募する"}])
    with pytest.raises(RecipeError, match="押しません"):
        run_steps(dev, [{"tap": {"xy": [500, 570]}}])  # 座標で指定しても、そこが「購入手続き」なら押さない
    assert not [c for c in dev.calls if c[0] == "tap"]


def test_each_opens_matching_items_but_skips_entry_buttons():
    listing = ui("抽選販売のお知らせ（ポケカ）", "抽選に応募する", "営業時間のお知らせ")
    detail = ui("ポケモンカード ハドウシーカー 抽選販売", "応募期間 11/21 10:00〜11/24 23:59")
    dev = FakeDevice([listing, detail, listing, listing])
    r = run_steps(dev, [{"each": {"match": ["抽選"], "max": 3, "pages": 1}}])
    assert [c for c in dev.calls if c[0] == "tap"] == [("tap", 540, 265)]
    assert len(r.pages) == 1


def test_missing_text_and_bad_steps():
    dev = FakeDevice([ui("ホーム")])
    with pytest.raises(RecipeError, match="見つかりません"):
        run_steps(dev, [{"tap": "お知らせ"}])
    run_steps(dev, [{"tap": "お知らせ", "optional": True}])  # optional なら止めない
    with pytest.raises(RecipeError):
        parse_step({"jump": 1})
    assert parse_step({"tap": "お知らせ", "wait": 1}) == ("tap", "お知らせ", {"wait": 1})  # 2つ目の wait はオプション
    with pytest.raises(RecipeError, match="package"):
        Runner(dev, {**APP, "package": ""}, sleep=lambda s: None).run(["read"])


def test_run_app_reports_failure_without_crashing():
    dev = FakeDevice([ui("ホーム")])
    st, lots, _ = agent.run_app(dev, {**APP, "steps": [{"tap": "お知らせ"}]}, KW, NOW, {}, {})
    assert st["ok"] is False and "見つかりません" in st["note"] and lots == []
    assert ("stop", "jp.example.shopapp") in dev.calls  # 失敗してもアプリは閉じる


def test_due_and_quiet_hours():
    local = {"last_run": {"testshop": (NOW - timedelta(minutes=30)).isoformat()}}
    assert not agent.is_due(APP, local, NOW, {"every_minutes": 60})
    assert agent.is_due(APP, local, NOW, {"every_minutes": 30})
    assert agent.in_quiet_hours(NOW.replace(hour=3), {"quiet_hours": [1, 6]})
    assert not agent.in_quiet_hours(NOW.replace(hour=6), {"quiet_hours": [1, 6]})
    assert agent.in_quiet_hours(NOW.replace(hour=23), {"quiet_hours": [23, 5]})


def test_health_messages_only_when_state_changes():
    local = {}
    bad = {"testshop": {"name": "テスト電機", "ok": False, "note": "見つかりません"}}
    first = agent.health_lines(bad, {"ok": True}, True, local)
    assert first == ["【注意】テスト電機 のアプリを読めませんでした: 見つかりません"]
    assert agent.health_lines(bad, {"ok": True}, True, local) == []
    good = {"testshop": {"name": "テスト電機", "ok": True}}
    assert agent.health_lines(good, {"ok": True}, True, local) == ["【復旧】テスト電機 のアプリを読めるようになりました"]
    lines = agent.health_lines(good, {"ok": False, "note": "端末が見つかりません"}, True, local)
    assert lines == ["【注意】Android端末につながりません: 端末が見つかりません"]


def test_push_is_skipped_when_nothing_changed(monkeypatch):
    sent = []
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    monkeypatch.setattr(agent.push, "put_file", lambda *a, **k: sent.append(a) or True)
    payload = {"updated": "t1", "device": {"ok": True}, "apps": {"a": {"ok": True, "found": 1, "checked": "t1"}}, "items": []}
    local = {}
    assert "送りました" in agent.send(payload, local, NOW)
    later = {**payload, "updated": "t2", "apps": {"a": {"ok": True, "found": 1, "checked": "t2"}}}
    assert "省略" in agent.send(later, local, NOW + timedelta(minutes=30))
    assert "送りました" in agent.send(later, local, NOW + timedelta(hours=3))  # 2時間ごとの生存報告
    assert len(sent) == 2


def test_put_file_creates_updates_and_skips_same_content(monkeypatch):
    import base64
    from android_agent import push

    remote, puts = {}, []

    def get(url, headers=None, params=None, timeout=None):
        if "content" not in remote:
            return SimpleNamespace(status_code=404, json=lambda: {})
        body = {"sha": remote["sha"], "content": base64.b64encode(remote["content"]).decode() + "\n"}
        return SimpleNamespace(status_code=200, json=lambda: body)

    def put(url, headers=None, json=None, timeout=None):
        puts.append(json.get("sha"))
        remote.update(content=base64.b64decode(json["content"]), sha=f"sha{len(puts)}")
        return SimpleNamespace(status_code=201)

    monkeypatch.setattr(push.requests, "get", get)
    monkeypatch.setattr(push.requests, "put", put)
    assert push.put_file("o/r", "data/app_found.json", b"a", "t", "main", "m") is True
    assert push.put_file("o/r", "data/app_found.json", b"a", "t", "main", "m") is False  # 同じ中身なら送らない
    assert push.put_file("o/r", "data/app_found.json", b"b", "t", "main", "m") is True
    assert puts == [None, "sha1"]


def test_pause_file_stops_automatic_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "PAUSE", tmp_path / "PAUSE")
    (tmp_path / "PAUSE").write_text("")
    monkeypatch.setattr(agent, "_run", lambda args: pytest.fail("一時停止中は読まない"))
    assert agent.cmd_run(SimpleNamespace(force=False)) == 0


# --- 画像読み取り（Claude API） ---
def test_vision_result_uses_same_date_rules():
    r = {"title": "【抽選】ポケモンカードゲーム ハドウシーカー", "product": "ハドウシーカー",
         "apply_period": "11月21日(土)10:00〜11月24日(火)23:59", "announce": "", "purchase_period": "11/28〜11/30",
         "conditions": ["アプリ会員限定", ""]}
    lot = from_fields(r, APP, KW, NOW)
    assert lot.apply_end == "2026-11-24T23:59+09:00"
    assert lot.announce is None  # 空の当選発表に、購入期間の日付が入り込まない
    assert lot.purchase_start == "2026-11-28T00:00+09:00"
    assert lot.conditions == ["アプリ会員限定"] and lot.form == "app"
    assert from_fields({**r, "title": "Nintendo Switch 2 抽選", "product": ""}, APP, KW, NOW) is None


def test_vision_request_shape():
    pytest.importorskip("anthropic")
    from android_agent import vision

    calls = {}
    body = {"lotteries": [{"title": "t", "product": "", "apply_period": "", "announce": "", "purchase_period": "", "conditions": []}]}

    def reply(stop="end_turn"):
        return SimpleNamespace(stop_reason=stop, usage=SimpleNamespace(input_tokens=1500, output_tokens=120),
                               content=[SimpleNamespace(type="text", text=json.dumps(body))])

    def beta_create(**kw):
        calls["beta"] = kw
        return reply()

    def plain_create(**kw):
        calls["plain"] = kw
        return reply("refusal")

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=beta_create)),
                             messages=SimpleNamespace(create=plain_create))
    items, usage = vision.read(b"jpeg", "テスト電機", client=client)
    assert items == body["lotteries"] and usage == {"input_tokens": 1500, "output_tokens": 120}
    sent = calls["beta"]
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
    assert sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["output_config"]["format"]["type"] == "json_schema" and sent["output_config"]["effort"] == "low"
    assert sent["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"
    items, _ = vision.read(b"jpeg", "テスト電機", model="claude-haiku-5-5", client=client)
    assert items == [] and "fallbacks" not in calls["plain"]  # Haiku は自動フォールバックなし・断られたら空


def test_vision_daily_limit_and_cache(monkeypatch):
    pytest.importorskip("PIL")
    from android_agent import vision

    n = {"calls": 0}

    def fake_read(img, store, model, effort):
        n["calls"] += 1
        return [{"title": "ポケモンカード ハドウシーカー 抽選", "product": "", "apply_period": "11/21〜11/24",
                 "announce": "", "purchase_period": "", "conditions": []}], {"input_tokens": 10, "output_tokens": 5}

    monkeypatch.setattr(vision, "read", fake_read)
    import io
    from PIL import Image

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (1080, 2400), color).save(buf, "PNG")
        return buf.getvalue()

    local = {}
    lots, note = agent.vision_items([png("white"), png("white"), png("black")], APP, KW, NOW, {"vision_daily_limit": 1}, local)
    assert n["calls"] == 1  # 同じ画面は読み直さない。3枚目は上限で読まない
    assert len(lots) == 2 and "上限" in note
    assert local["vision"]["calls"] == 1 and local["vision"]["input_tokens"] == 10


# --- サイト側への取り込み ---
def _found(tmp_path, updated, items):
    p = tmp_path / "app_found.json"
    p.write_text(json.dumps({"updated": updated, "apps": {"testshop": {"name": "テスト電機", "ok": True, "found": len(items),
                                                                      "checked": updated, "url": "javascript:alert(1)"}},
                             "items": items}, ensure_ascii=False), encoding="utf-8")
    return p


def test_collector_reads_app_found(tmp_path, monkeypatch):
    root = screen.parse((FX / "app_news.xml").read_text(encoding="utf-8"))
    lots = [l.to_dict() for l in extract_pages([screen.to_html(root)], APP, KW, NOW)]
    lots[0]["url"] = "javascript:alert(1)"
    monkeypatch.setattr(main, "APP_FOUND", _found(tmp_path, dates.iso(NOW - timedelta(hours=1)), lots + [{"store": "x"}]))
    state = {"items": {}, "fingerprints": {}, "sources": {"app:old": {"ok": True}}}
    items = main.app_items(state, NOW)
    assert len(items) == 2 and {d["origin"] for d in items} == {"app"}
    assert items[0]["url"] == ""  # http(s) 以外のリンクは載せない
    assert state["sources"]["app:testshop"]["ok"] is True and state["sources"]["app:testshop"]["url"] == ""
    assert "app:old" not in state["sources"]
    new = main.merge(state, items, [], NOW)
    assert len(new) == 2 and {d["area"] for d in state["items"].values()} == {"national"}


def test_collector_marks_silent_device_as_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "APP_FOUND", _found(tmp_path, dates.iso(NOW - timedelta(hours=9)), []))
    state = {"items": {}, "fingerprints": {}, "sources": {}}
    main.app_items(state, NOW)
    st = state["sources"]["app:testshop"]
    assert st["ok"] is False and "届いていません" in st["note"]


def test_app_result_does_not_overwrite_manual_entry():
    d = {"id": "same", "store": "テスト電機", "title": "手動", "origin": "manual", "form": "app", "apply_end": "2026-11-24T23:59+09:00"}
    state = {"items": {"same": dict(d)}, "fingerprints": {}, "sources": {}}
    main.merge(state, [{**d, "title": "アプリ", "origin": "app"}], [dict(d)], NOW)
    assert state["items"]["same"]["title"] == "手動"


def test_about_page_lists_enabled_apps(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    (tmp_path / "config" / "apps.yaml").write_text(
        "apps:\n  - {id: testshop, enabled: true, store: テスト電機, name: テスト電機アプリ}\n"
        "  - {id: off, enabled: false, store: 止めている店}\n", encoding="utf-8")
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    out = publish._sources({"sources": {"app:testshop": {"ok": True, "checked": "t", "name": "x"}}})
    assert out == [{"id": "app:testshop", "name": "テスト電機", "sub": "テスト電機アプリ", "url": "", "kind": "app",
                    "pref": None, "ok": True, "checked": "t"}]


def test_repo_apps_yaml_is_valid():
    cfg = agent.load_apps()
    for a in cfg["apps"]:
        for step in a.get("steps") or []:
            parse_step(step)


# --- X API（従量課金） ---
class FakeX:
    def __init__(self, pages):
        self.pages = list(pages)
        self.params = []

    def get(self, url, headers=None, timeout=None, params=None):
        self.params.append(params)
        body = self.pages.pop(0) if self.pages else {"meta": {"result_count": 0}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


def post_id(at: datetime) -> str:
    """その時刻に投稿されたXの投稿ID（IDの上位ビットが投稿時刻になっている）。"""
    return str((int(at.timestamp() * 1000) - xsource.TWEPOCH_MS) << 22)


PID = post_id(NOW - timedelta(hours=1))
POST = {"data": [{"id": PID, "text": "【抽選販売】ポケモンカードゲーム ハドウシーカー\n応募期間 11/20 10:00〜11/23 23:59\n当選発表 11/25"}],
        "meta": {"newest_id": PID, "result_count": 1}}


def test_x_reads_only_new_posts(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "t")
    fake = FakeX([POST])
    monkeypatch.setattr(xsource.requests, "get", fake.get)
    srcs = [{"id": "x-a", "name": "店A", "account": "ShopA", "form": "store", "pref": "東京都"}]
    xstate = {}
    lots, st = xsource.collect(srcs, KW, NOW, xstate)
    assert len(lots) == 1 and lots[0].pref == "東京都" and st["x-a"] == {"ok": True, "found": 1}
    assert "start_time" in fake.params[0] and "since_id" not in fake.params[0]
    assert "expansions" not in fake.params[0]  # 投稿者情報は別料金なので付けない
    lots, _ = xsource.collect(srcs, KW, NOW, xstate)
    assert fake.params[1]["since_id"] == PID and "start_time" not in fake.params[1]
    assert xstate["spend"]["posts"] == 1 and xstate["spend"]["usd"] == 0.005
    xstate["since_id"]["shopa"] = post_id(NOW - timedelta(days=8))  # 1週間以上告知が無かった
    xsource.collect(srcs, KW, NOW, xstate)
    assert "since_id" not in fake.params[2] and "start_time" in fake.params[2]


def test_x_monthly_budget_stops_reading(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "t")
    monkeypatch.setenv("X_MONTHLY_BUDGET_USD", "0.005")
    fake = FakeX([POST])
    monkeypatch.setattr(xsource.requests, "get", fake.get)
    srcs = [{"id": "x-a", "name": "店A", "account": "ShopA"}, {"id": "x-b", "name": "店B", "account": "ShopB"}]
    xstate = {}
    _, st = xsource.collect(srcs, KW, NOW, xstate)
    assert len(fake.params) == 1 and st["x-b"]["ok"] is None and "上限" in st["x-b"]["note"]
    xstate["spend"]["month"] = "2026-10"  # 月が変われば再開
    xsource.collect(srcs, KW, NOW, xstate)
    assert len(fake.params) == 3


def test_x_without_token_is_skipped(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    lots, st = xsource.collect([{"id": "x-a", "name": "店A", "account": "ShopA"}], KW, NOW, {})
    assert lots == [] and st["x-a"]["ok"] is None
