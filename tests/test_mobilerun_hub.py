"""Mobilerun 中継サーバーのテスト。実機の代わりに「Portal のふりをする WebSocket」でつなぐ。"""
import asyncio
import base64
import json
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestServer  # noqa: E402

from mobilerun_hub import register  # noqa: E402
from mobilerun_hub.client import Client, HubError  # noqa: E402
from mobilerun_hub.hub import Config, Hub, admin_app, public_app  # noqa: E402
from mobilerun_hub.store import Registry  # noqa: E402

ADMIN = "a" * 32
PNG = b"\x89PNG\r\n\x1a\nfake-screen"


def run(coro):
    return asyncio.run(coro)


class Env:
    """中継サーバー（公開側・管理側）を立ち上げ、通知を記録する。"""

    def __init__(self, tmp: Path, **cfg):
        self.cfg = Config(data_dir=tmp, admin_token=ADMIN, public_url="wss://portal.example.jp",
                          heartbeat=0, monitor_interval=3600, **cfg)
        self.sent: list[str] = []

        async def collect(text):
            self.sent.append(text)

        self.hub = Hub(self.cfg, notify=collect)

    async def __aenter__(self):
        self.pub = TestServer(public_app(self.hub))
        self.adm = TestServer(admin_app(self.hub))
        await self.pub.start_server()
        await self.adm.start_server()
        self.http = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        await self.http.close()
        await self.pub.close()
        await self.adm.close()

    def ws_url(self, path):
        return str(self.pub.make_url(path)).replace("http://", "ws://")

    async def api(self, method, path, body=None, token=ADMIN):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with self.http.request(method, self.adm.make_url(path), json=body, headers=headers) as r:
            data = await r.read()
            ctype = r.headers.get("Content-Type", "")
            return r.status, (json.loads(data) if "json" in ctype else data)

    async def portal(self, number, token, answer=True, path=None):
        """Portal のふり。命令に返事をする。"""
        ws = await self.http.ws_connect(self.ws_url(path or f"/join/{number}"), headers={
            "Authorization": f"Bearer {token}", "X-Device-ID": "dev-abc", "X-Device-Name": "Pixel 7a", "X-Device-Country": "JP"})

        async def loop():
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT or not answer:
                    continue
                req = json.loads(msg.data)
                m = req["method"]
                if m == "never":
                    continue
                result = {"version": "0.9.9", "screenshot": base64.b64encode(PNG).decode(), "state": {"a11y_tree": []}}.get(
                    m, {"echo": m, "params": req.get("params")})
                await ws.send_json({"id": req["id"], "status": "success", "result": result})

        return ws, asyncio.ensure_future(loop())


async def until(cond, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("時間内に状態が変わりませんでした")


# --- 台帳 ---
def test_registry_keeps_only_token_hashes(tmp_path):
    reg = Registry(tmp_path / "devices.json")
    t = reg.add("12", label="赤")
    raw = (tmp_path / "devices.json").read_text()
    assert t["portal_token"] not in raw and t["guardian_token"] not in raw
    assert reg.check("12", t["portal_token"]) and not reg.check("12", "wrong") and not reg.check("12", t["guardian_token"])
    assert reg.find_by_token(t["portal_token"]) == "12"
    assert Registry(tmp_path / "devices.json").check("12", t["portal_token"])  # 再起動しても使える
    with pytest.raises(KeyError):
        reg.add("12")
    assert reg.add("12", rotate=True)["portal_token"] != t["portal_token"]
    assert not reg.check("12", t["portal_token"])  # 作り直したら古いトークンは使えない
    with pytest.raises(ValueError):
        reg.add("12/../x")
    reg.add("3")
    assert reg.next_number() == "13"


# --- 端末の接続と操作 ---
def test_portal_connects_and_answers_commands(tmp_path):
    async def scenario():
        async with Env(tmp_path) as env:
            st, _ = await env.api("GET", "/api/devices", token=None)
            assert st == 401  # 管理APIはトークン必須
            st, reg = await env.api("POST", "/api/devices", {"number": "12", "label": "赤"})
            assert st == 200 and reg["portal_url"] == "wss://portal.example.jp/join/12"
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await env.portal("12", "wrong-token")
            ws, task = await env.portal("12", reg["portal_token"])
            await until(lambda: "12" in env.hub.portal)
            st, dev = await env.api("GET", "/api/devices/12")
            assert dev["online"] and dev["portal"]["device_name"] == "Pixel 7a" and dev["portal"]["device_id"] == "dev-abc"
            st, res = await env.api("POST", "/api/devices/12/rpc", {"method": "tap", "params": {"x": 200, "y": 400}})
            assert st == 200 and res["result"] == {"echo": "tap", "params": {"x": 200, "y": 400}}
            st, png = await env.api("GET", "/api/devices/12/screenshot")
            assert st == 200 and png == PNG
            st, res = await env.api("POST", "/api/devices/12/awake", {"enabled": False})
            assert res["result"] == {"echo": "screen/keepAwake/set", "params": {"enabled": False}}
            st, res = await env.api("POST", "/api/devices/12/rpc", {"method": "never", "timeout": 0.2})
            assert st == 504  # 返事が無ければ時間切れで返す
            await ws.send_json({"method": "events/device", "params": {"type": "APP_ENTERED"}})
            await until(lambda: env.hub.portal["12"].events)
            st, ev = await env.api("GET", "/api/devices/12/events")
            assert ev["events"][0]["params"]["type"] == "APP_ENTERED"
            st, csv = await env.api("GET", "/api/devices.csv")
            assert "赤" in csv.decode("utf-8-sig") and "○" in csv.decode("utf-8-sig")
            await ws.close()
            await until(lambda: "12" not in env.hub.portal)
            st, res = await env.api("POST", "/api/devices/12/rpc", {"method": "tap"})
            assert st == 409  # 切れている端末には送れない
            task.cancel()

    run(scenario())


def test_disconnect_and_reconnect_are_notified_once(tmp_path):
    async def scenario():
        async with Env(tmp_path, alert_after=0.05) as env:
            _, reg = await env.api("POST", "/api/devices", {"number": "7"})
            await env.hub.check_once()
            assert env.sent == []  # 登録直後でまだ一度もつながっていない端末は知らせない
            ws, task = await env.portal("7", reg["portal_token"])
            await until(lambda: "7" in env.hub.portal)
            await ws.close()
            await until(lambda: "7" not in env.hub.portal)
            await asyncio.sleep(0.1)
            await env.hub.check_once()
            await env.hub.check_once()
            await asyncio.sleep(0.01)
            assert len(env.sent) == 1 and env.sent[0].startswith("【切断】端末7")
            ws, task2 = await env.portal("7", reg["portal_token"])
            await until(lambda: len(env.sent) == 2)
            assert env.sent[1].startswith("【復帰】端末7")
            await ws.close()
            task.cancel()
            task2.cancel()

    run(scenario())


def test_new_connection_replaces_old_one(tmp_path):
    async def scenario():
        async with Env(tmp_path) as env:
            _, reg = await env.api("POST", "/api/devices", {"number": "5"})
            old, t1 = await env.portal("5", reg["portal_token"])
            await until(lambda: "5" in env.hub.portal)
            new, t2 = await env.portal("5", reg["portal_token"], path="/join")  # 番号なしでもトークンで分かる
            await until(lambda: old.closed)
            assert old.close_code == 4000 and "5" in env.hub.portal
            st, res = await env.api("POST", "/api/devices/5/rpc", {"method": "version"})
            assert res["result"] == "0.9.9"
            await new.close()
            t1.cancel()
            t2.cancel()

    run(scenario())


# --- 見張り役（復旧アプリ） ---
def test_recover_goes_through_guardian(tmp_path):
    async def scenario():
        async with Env(tmp_path) as env:
            _, reg = await env.api("POST", "/api/devices", {"number": "9"})
            st, res = await env.api("POST", "/api/devices/9/recover", {})
            assert st == 409  # 見張り役がつながっていない
            g = await env.http.ws_connect(env.ws_url("/guardian/9"), headers={"Authorization": f"Bearer {reg['guardian_token']}"})
            await g.send_json({"type": "hello", "portal_installed": True, "a11y_enabled": False})

            async def guardian():
                msg = json.loads((await g.receive()).data)
                assert msg["type"] == "recover"
                await g.send_json({"type": "recover_result", "id": msg["id"], "ok": True, "detail": "ユーザー補助を付け直しました"})

            t = asyncio.ensure_future(guardian())
            await until(lambda: "9" in env.hub.guardian and env.hub.guardian["9"].info)
            st, res = await env.api("POST", "/api/devices/9/recover", {})
            assert st == 200 and res["ok"] is True
            st, dev = await env.api("GET", "/api/devices/9")
            assert dev["guardian_online"] and dev["guardian"]["a11y_enabled"] is False
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await env.http.ws_connect(env.ws_url("/guardian/9"), headers={"Authorization": f"Bearer {reg['portal_token']}"})
            await g.close()
            await t

    run(scenario())


def test_auto_recover_is_rate_limited(tmp_path):
    async def scenario():
        async with Env(tmp_path, auto_recover=True, recover_after=0, recover_per_hour=1, alert_after=3600) as env:
            _, reg = await env.api("POST", "/api/devices", {"number": "4"})
            env.hub.awaiting.discard("4")
            g = await env.http.ws_connect(env.ws_url("/guardian/4"), headers={"Authorization": f"Bearer {reg['guardian_token']}"})
            got = []

            async def guardian():
                async for msg in g:
                    data = json.loads(msg.data)
                    got.append(data)
                    await g.send_json({"type": "recover_result", "id": data["id"], "ok": True})

            t = asyncio.ensure_future(guardian())
            await until(lambda: "4" in env.hub.guardian)
            for _ in range(3):
                await env.hub.check_once()
                await asyncio.sleep(0.05)
            assert len(got) == 1 and got[0]["reason"] == "auto"  # 1時間に1回まで
            await g.close()
            t.cancel()

    run(scenario())


# --- iMac の登録スクリプト ---
class FakeAdb(register.Adb):
    """adb のふり。Portal の接続先が設定されたら、その内容で中継サーバーに「Portal として」つなぐ。"""

    def __init__(self, on_configure, installed=True):
        super().__init__("FAKE123")
        self.cmds = []
        self.on_configure = on_configure
        self.installed = installed

    def run(self, *args, timeout=60, check=True):
        self.cmds.append(" ".join(args))
        if args[0] == "install":
            self.installed = True
            return "Success"
        if args[0] != "shell":
            return ""
        cmd = args[1]
        props = {"ro.product.model": "Pixel 7a", "ro.build.version.release": "15", "ro.serialno": "SER123"}
        if cmd.startswith("getprop "):
            return props.get(cmd.split()[1], "") + "\n"
        if cmd.startswith("pm list packages"):
            return f"package:{register.PORTAL}\n" if self.installed else ""
        if cmd.startswith("dumpsys package"):
            return "  com.mobilerun.portal/.service.MobilerunAccessibilityService filter 1234\n"
        if cmd.startswith("settings get secure enabled_accessibility_services"):
            return "com.other/.Svc\n"
        if "configure_reverse_connection" in cmd:
            fields = dict(b.split(":", 2)[::2] for b in cmd.replace("'", "").split("--bind ")[1:])
            self.on_configure(base64.b64decode(fields["url_base64"].strip()).decode(),
                              base64.b64decode(fields["token_base64"].strip()).decode())
        return ""


def test_register_script_sets_up_portal_and_verifies(tmp_path):
    async def scenario():
        async with Env(tmp_path) as env:
            loop = asyncio.get_running_loop()
            tasks = []

            def on_configure(url, token):
                assert url == "wss://portal.example.jp/join/1"
                fut = asyncio.run_coroutine_threadsafe(env.portal(url.rsplit("/", 1)[1], token), loop)
                tasks.append(fut)

            adb = FakeAdb(on_configure, installed=False)
            client = Client(url=str(env.adm.make_url("")), token=ADMIN)
            number = await loop.run_in_executor(None, lambda: register.register_device(adb, client, None, "", "~/portal.apk"))
            assert number == "1"
            log = "\n".join(adb.cmds)
            assert "install -r -g" in log
            assert "enabled_accessibility_services 'com.other/.Svc:com.mobilerun.portal/com.mobilerun.portal.service.MobilerunAccessibilityService'" in log
            assert "deviceidle whitelist +com.mobilerun.portal" in log
            assert "cloud/reconnect_after_restart" in log
            assert log.index("svc power stayon usb") < log.index("svc power stayon false")  # 登録中だけ点灯
            st, dev = await env.api("GET", "/api/devices/1")
            assert dev["online"] and dev["model"] == "Pixel 7a" and dev["usb_serial"] == "SER123" and dev["label"] == "Pixel 7a"
            ws, task = tasks[0].result()
            await ws.close()
            task.cancel()

    run(scenario())


def test_register_fails_clearly_without_portal_apk(tmp_path):
    async def scenario():
        async with Env(tmp_path) as env:
            client = Client(url=str(env.adm.make_url("")), token=ADMIN)
            adb = FakeAdb(lambda *a: None, installed=False)
            with pytest.raises(register.RegisterError, match="portal-apk"):
                await asyncio.get_running_loop().run_in_executor(None, lambda: register.register_device(adb, client, "2", "", None))
            assert any("stayon false" in c for c in adb.cmds)  # 失敗しても点灯は元に戻す

    run(scenario())


def test_client_reports_errors_in_japanese(tmp_path):
    async def scenario():
        async with Env(tmp_path) as env:
            loop = asyncio.get_running_loop()
            bad = Client(url=str(env.adm.make_url("")), token="x" * 32)
            with pytest.raises(HubError, match="管理トークン"):
                await loop.run_in_executor(None, bad.devices)
            good = Client(url=str(env.adm.make_url("")), token=ADMIN)
            with pytest.raises(HubError, match="台帳にありません"):
                await loop.run_in_executor(None, lambda: good.device("99"))

    run(scenario())
