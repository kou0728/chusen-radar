"""中継サーバー本体。

  公開側（Caddy の後ろ。インターネットから届く）
    /join/{number}      Portal の逆接続（端末 → ここ）。Authorization: Bearer <端末のトークン>
    /guardian/{number}  復旧アプリ（見張り役）の接続
    /healthz            動いているかの確認
  管理側（Tailscale か 127.0.0.1 だけで待つ。iMac から使う）
    /                   端末一覧の画面
    /api/devices ...    台帳・操作・復旧の API（Authorization: Bearer <管理トークン>）
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import csv
import hmac
import io
import json
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from .store import Registry, now_iso

log = logging.getLogger("mobilerun_hub")
HERE = Path(__file__).resolve().parent
MAX_MSG = 64 * 1024 * 1024  # スクリーンショット（base64のPNG）が数MBになることがある


class DeviceOffline(Exception):
    pass


@dataclass
class Config:
    data_dir: Path
    admin_token: str
    public_url: str                  # 例 wss://portal.example.jp （端末に設定する接続先の頭）
    public_host: str = "127.0.0.1"
    public_port: int = 8700
    admin_host: str = "127.0.0.1"    # Tailscale のIP（100.x.y.z）にすると iMac から届く
    admin_port: int = 8701
    heartbeat: float = 25.0          # この秒数ごとに ping（途中の機器に無通信で切られないため）
    rpc_timeout: float = 30.0
    alert_after: float = 120.0       # これだけ切れたままなら通知する
    monitor_interval: float = 15.0
    auto_recover: bool = False       # 切れた端末を、見張り役アプリ経由で自動で立て直す
    recover_after: float = 300.0
    recover_per_hour: int = 3
    discord_webhook: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        token = env.get("HUB_ADMIN_TOKEN", "")
        if len(token) < 24:
            raise SystemExit("HUB_ADMIN_TOKEN に24文字以上のランダムな文字列を入れてください（README参照）")
        url = env.get("HUB_PUBLIC_URL", "").rstrip("/")
        if not url.startswith(("wss://", "ws://")):
            raise SystemExit("HUB_PUBLIC_URL に端末から見た接続先（例 wss://portal.example.jp）を入れてください")
        return cls(
            data_dir=Path(env.get("HUB_DATA_DIR", "/var/lib/mobilerun-hub")),
            admin_token=token,
            public_url=url,
            public_host=env.get("HUB_PUBLIC_HOST", "127.0.0.1"),
            public_port=int(env.get("HUB_PUBLIC_PORT", "8700")),
            admin_host=env.get("HUB_ADMIN_HOST", "127.0.0.1"),
            admin_port=int(env.get("HUB_ADMIN_PORT", "8701")),
            heartbeat=float(env.get("HUB_HEARTBEAT_SECONDS", "25")),
            rpc_timeout=float(env.get("HUB_RPC_TIMEOUT_SECONDS", "30")),
            alert_after=float(env.get("HUB_ALERT_AFTER_SECONDS", "120")),
            auto_recover=env.get("HUB_AUTO_RECOVER", "0") == "1",
            recover_after=float(env.get("HUB_RECOVER_AFTER_SECONDS", "300")),
            recover_per_hour=int(env.get("HUB_RECOVER_PER_HOUR", "3")),
            discord_webhook=env.get("DISCORD_WEBHOOK_URL", ""),
        )


@dataclass
class Link:
    """つながっている WebSocket 1本（Portal か見張り役）。"""
    ws: web.WebSocketResponse
    info: dict
    connected_at: float = field(default_factory=time.time)
    last_rx: float = field(default_factory=time.time)
    pending: dict = field(default_factory=dict)
    events: collections.deque = field(default_factory=lambda: collections.deque(maxlen=100))


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds") if ts else None


def _bearer(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _ago(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}時間{m % 60}分" if m >= 60 else f"{m}分" if m else f"{int(seconds)}秒"


class Hub:
    def __init__(self, cfg: Config, registry: Registry | None = None,
                 notify: Callable[[str], Awaitable[None]] | None = None):
        self.cfg = cfg
        self.reg = registry or Registry(cfg.data_dir / "devices.json")
        self.portal: dict[str, Link] = {}
        self.guardian: dict[str, Link] = {}
        started = time.time()
        # 起動直後は、前につながったことのある端末を「まだ戻っていない」として数え始める
        self.offline_since: dict[str, float] = {n: started for n, d in self.reg.devices.items() if d.get("last_connected")}
        # 登録したばかりで一度もつながっていない端末は、切断の通知をしない
        self.awaiting: set[str] = {n for n, d in self.reg.devices.items() if not d.get("last_connected")}
        self.alerted: set[str] = set()
        self.recover_log: dict[str, collections.deque] = {}
        self._notify = notify or self._discord
        self._monitor: asyncio.Task | None = None

    # ------------------------------------------------------------------ 通知
    async def _discord(self, text: str) -> None:
        if not self.cfg.discord_webhook:
            return
        try:
            async with ClientSession(timeout=ClientTimeout(total=10)) as s:
                await s.post(self.cfg.discord_webhook, json={"content": text[:1900]})
        except Exception as e:  # 通知の失敗でサーバーを止めない
            log.warning("Discord通知に失敗: %s", e)

    def notify(self, text: str) -> None:
        log.info("通知: %s", text)
        asyncio.ensure_future(self._notify(text))

    def _name(self, number: str) -> str:
        label = self.reg.devices.get(number, {}).get("label")
        return f"端末{number}" + (f"（{label}）" if label else "")

    # ------------------------------------------------------------------ Portal（端末）
    async def portal_ws(self, request: web.Request) -> web.StreamResponse:
        number = request.match_info.get("number")
        token = _bearer(request)
        if number is None:
            number = self.reg.find_by_token(token, "portal")
        if not self.reg.check(number, token, "portal"):
            log.warning("Portal の認証に失敗しました（番号 %s、%s）", number, request.headers.get("X-Forwarded-For", request.remote))
            raise web.HTTPUnauthorized(text="unauthorized")
        ws = web.WebSocketResponse(heartbeat=self.cfg.heartbeat or None, max_msg_size=MAX_MSG)
        await ws.prepare(request)
        info = {
            "device_id": request.headers.get("X-Device-ID", ""),
            "device_name": request.headers.get("X-Device-Name", ""),
            "country": request.headers.get("X-Device-Country", ""),
            "user_id": request.headers.get("X-User-ID", ""),
            "remote": request.headers.get("X-Forwarded-For", request.remote or ""),
        }
        link = Link(ws, info)
        old = self.portal.get(number)
        self.portal[number] = link
        if old is not None:
            await old.ws.close(code=4000, message=b"replaced by a newer connection")
        self._portal_up(number, info)
        try:
            async for msg in ws:
                link.last_rx = time.time()
                if msg.type == WSMsgType.TEXT:
                    self._portal_text(link, msg.data)
                elif msg.type == WSMsgType.BINARY:
                    self._portal_binary(link, msg.data)
        finally:
            for fut in link.pending.values():
                if not fut.done():
                    fut.set_exception(DeviceOffline(f"{self._name(number)} の接続が切れました"))
            if self.portal.get(number) is link:
                del self.portal[number]
                self._portal_down(number, ws.close_code)
        return ws

    def _portal_up(self, number: str, info: dict) -> None:
        now = time.time()
        self.awaiting.discard(number)
        down = self.offline_since.pop(number, None)
        if number in self.alerted:
            self.alerted.discard(number)
            self.notify(f"【復帰】{self._name(number)} がつながりました" + (f"（{_ago(now - down)}ぶり）" if down else ""))
        self.reg.update(number, last_connected=now_iso(), portal=info)
        log.info("つながりました: %s %s", self._name(number), info.get("device_name", ""))

    def _portal_down(self, number: str, code) -> None:
        self.offline_since[number] = time.time()
        self.reg.update(number, last_disconnected=now_iso(), last_close_code=code)
        log.info("切れました: %s（code %s）", self._name(number), code)

    def _portal_text(self, link: Link, data: str) -> None:
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        rid = msg.get("id")
        if rid is not None and str(rid) in link.pending:
            fut = link.pending.pop(str(rid))
            if not fut.done():
                fut.set_result(msg)
            return
        if msg.get("method"):  # 端末からのお知らせ（events/device など）
            link.events.append({"at": now_iso(), "method": msg["method"], "params": msg.get("params")})
        elif msg.get("type") == "PING":
            asyncio.ensure_future(link.ws.send_json({"type": "PONG", "timestamp": msg.get("timestamp"), "payload": "pong"}))

    def _portal_binary(self, link: Link, data: bytes) -> None:
        # ファイルの取り出しなどは「先頭36バイトが依頼のID」の形で届く
        rid = data[:36].decode("ascii", "ignore")
        fut = link.pending.pop(rid, None)
        if fut is not None and not fut.done():
            fut.set_result({"id": rid, "status": "success", "binary": data[36:]})

    async def rpc(self, number: str, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        link = self.portal.get(number)
        if link is None:
            raise DeviceOffline(f"{self._name(number)} はいまつながっていません")
        rid = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        link.pending[rid] = fut
        try:
            await link.ws.send_json({"id": rid, "method": method, "params": params or {}})
            return await asyncio.wait_for(fut, timeout or self.cfg.rpc_timeout)
        finally:
            link.pending.pop(rid, None)

    # ------------------------------------------------------------------ 見張り役（復旧アプリ）
    async def guardian_ws(self, request: web.Request) -> web.StreamResponse:
        number = request.match_info["number"]
        if not self.reg.check(number, _bearer(request), "guardian"):
            log.warning("見張り役の認証に失敗しました（番号 %s）", number)
            raise web.HTTPUnauthorized(text="unauthorized")
        ws = web.WebSocketResponse(heartbeat=self.cfg.heartbeat or None, max_msg_size=1024 * 1024)
        await ws.prepare(request)
        link = Link(ws, {})
        old = self.guardian.get(number)
        self.guardian[number] = link
        if old is not None:
            await old.ws.close(code=4000, message=b"replaced by a newer connection")
        try:
            async for msg in ws:
                link.last_rx = time.time()
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") in ("hello", "status"):
                    link.info = {k: v for k, v in data.items() if k != "type"}
                    self.reg.update(number, guardian_last=link.info, guardian_seen=now_iso())
                elif data.get("type") == "recover_result":
                    fut = link.pending.pop(str(data.get("id")), None)
                    if fut is not None and not fut.done():
                        fut.set_result(data)
        finally:
            for fut in link.pending.values():
                if not fut.done():
                    fut.set_exception(DeviceOffline("見張り役の接続が切れました"))
            if self.guardian.get(number) is link:
                del self.guardian[number]
        return ws

    async def recover(self, number: str, reason: str = "manual", timeout: float = 60) -> dict:
        link = self.guardian.get(number)
        if link is None:
            raise DeviceOffline(f"{self._name(number)} の見張り役アプリがつながっていません")
        rid = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        link.pending[rid] = fut
        self.recover_log.setdefault(number, collections.deque(maxlen=20)).append(time.time())
        try:
            await link.ws.send_json({"type": "recover", "id": rid, "reason": reason})
            return await asyncio.wait_for(fut, timeout)
        finally:
            link.pending.pop(rid, None)

    def _recent_recovers(self, number: str, within: float = 3600) -> list[float]:
        now = time.time()
        return [t for t in self.recover_log.get(number, []) if now - t < within]

    # ------------------------------------------------------------------ 見回り（切断の通知・自動復旧）
    async def check_once(self) -> None:
        now = time.time()
        for number in list(self.reg.devices):
            if number in self.portal or number in self.awaiting:
                continue
            since = self.offline_since.setdefault(number, now)
            down = now - since
            if down >= self.cfg.alert_after and number not in self.alerted:
                self.alerted.add(number)
                g = "見張り役はつながっています" if number in self.guardian else "見張り役もつながっていません"
                self.notify(f"【切断】{self._name(number)} が{_ago(down)}つながっていません（{g}）")
            if self.cfg.auto_recover and number in self.guardian and down >= self.cfg.recover_after:
                recent = self._recent_recovers(number)
                if len(recent) < self.cfg.recover_per_hour and (not recent or now - recent[-1] >= self.cfg.recover_after):
                    asyncio.ensure_future(self._auto_recover(number))

    async def _auto_recover(self, number: str) -> None:
        try:
            res = await self.recover(number, reason="auto")
            log.info("自動復旧 %s: %s", self._name(number), res)
        except (DeviceOffline, asyncio.TimeoutError) as e:
            log.warning("自動復旧できませんでした %s: %s", self._name(number), e)

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.monitor_interval)
            try:
                await self.check_once()
            except Exception:  # 見回りが1回失敗しても続ける
                log.exception("見回りでエラー")

    async def start_background(self, app: web.Application) -> None:
        self._monitor = asyncio.ensure_future(self._monitor_loop())

    async def stop_background(self, app: web.Application) -> None:
        if self._monitor:
            self._monitor.cancel()
        for link in list(self.portal.values()) + list(self.guardian.values()):
            await link.ws.close(code=1001, message=b"hub shutting down")

    # ------------------------------------------------------------------ 台帳の見え方
    def view(self, number: str) -> dict:
        d = self.reg.devices[number]
        link, g = self.portal.get(number), self.guardian.get(number)
        since = self.offline_since.get(number)
        return {
            "number": number,
            "label": d.get("label", ""),
            "online": link is not None,
            "connected_at": _iso(link.connected_at) if link else None,
            "offline_for": None if link or not since else int(time.time() - since),
            "last_connected": d.get("last_connected"),
            "last_disconnected": d.get("last_disconnected"),
            "portal": d.get("portal", {}),
            "guardian_online": g is not None,
            "guardian": g.info if g else d.get("guardian_last", {}),
            "model": d.get("model", ""),
            "android": d.get("android", ""),
            "usb_serial": d.get("usb_serial", ""),
            "notes": d.get("notes", ""),
            "created": d.get("created"),
        }

    def urls(self, number: str) -> dict:
        return {"portal_url": f"{self.cfg.public_url}/join/{number}",
                "guardian_url": f"{self.cfg.public_url}/guardian/{number}"}


# ====================================================================== アプリ
def public_app(hub: Hub) -> web.Application:
    app = web.Application()
    assetlinks = hub.cfg.data_dir / "assetlinks.json"

    async def healthz(request):
        return web.json_response({"ok": True})  # 台数などは外に出さない

    async def well_known(request):
        if not assetlinks.exists():
            raise web.HTTPNotFound()
        return web.Response(body=assetlinks.read_bytes(), content_type="application/json")

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/.well-known/assetlinks.json", well_known)
    app.router.add_get("/join/{number}", hub.portal_ws)
    app.router.add_get("/join", hub.portal_ws)
    app.router.add_get("/guardian/{number}", hub.guardian_ws)
    app.on_startup.append(hub.start_background)
    app.on_shutdown.append(hub.stop_background)
    return app


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def admin_app(hub: Hub) -> web.Application:
    @web.middleware
    async def auth(request, handler):
        if request.path.startswith("/api/") and not _tokens_match(_bearer(request), hub.cfg.admin_token):
            return _error(401, "管理トークンが違います")
        return await handler(request)

    app = web.Application(middlewares=[auth], client_max_size=4 * 1024 * 1024)
    routes = web.RouteTableDef()

    def device_or_404(request) -> str:
        number = request.match_info["number"]
        if number not in hub.reg.devices:
            raise web.HTTPNotFound(text=json.dumps({"error": f"端末{number} は台帳にありません"}), content_type="application/json")
        return number

    @routes.get("/")
    async def page(request):
        return web.FileResponse(HERE / "admin.html")

    @routes.get("/api/devices")
    async def list_devices(request):
        return web.json_response({"devices": [hub.view(n) for n in sorted(hub.reg.devices, key=_sort_key)],
                                  "next_number": hub.reg.next_number()})

    @routes.get("/api/devices.csv")
    async def list_csv(request):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["端末番号", "名前", "接続中", "最後につながった", "最後に切れた", "機種", "Android", "見張り役", "メモ"])
        for n in sorted(hub.reg.devices, key=_sort_key):
            v = hub.view(n)
            w.writerow([n, v["label"], "○" if v["online"] else "×", v["last_connected"] or "", v["last_disconnected"] or "",
                        v["model"] or v["portal"].get("device_name", ""), v["android"], "○" if v["guardian_online"] else "×", v["notes"]])
        return web.Response(body=("﻿" + buf.getvalue()).encode("utf-8"), content_type="text/csv", charset="utf-8")

    @routes.post("/api/devices")
    async def add_device(request):
        body = await _json(request)
        number = str(body.get("number") or hub.reg.next_number())
        try:
            tokens = hub.reg.add(number, label=str(body.get("label") or ""), rotate=bool(body.get("rotate")))
        except ValueError as e:
            return _error(400, str(e))
        except KeyError as e:
            return _error(409, e.args[0])
        if number not in hub.portal:
            hub.awaiting.add(number)  # 登録直後の「まだつながっていない」は通知しない
        return web.json_response({**tokens, **hub.urls(number), "device": hub.view(number)})

    @routes.get("/api/devices/{number}")
    async def get_device(request):
        return web.json_response(hub.view(device_or_404(request)))

    @routes.patch("/api/devices/{number}")
    async def edit_device(request):
        number = device_or_404(request)
        hub.reg.edit(number, await _json(request))
        return web.json_response(hub.view(number))

    @routes.delete("/api/devices/{number}")
    async def remove_device(request):
        number = device_or_404(request)
        hub.reg.remove(number)
        for links in (hub.portal, hub.guardian):
            link = links.pop(number, None)
            if link:
                await link.ws.close(code=4001, message=b"removed from the ledger")
        hub.offline_since.pop(number, None)
        hub.alerted.discard(number)
        hub.awaiting.discard(number)
        return web.json_response({"removed": number})

    @routes.post("/api/devices/{number}/rpc")
    async def rpc(request):
        number = device_or_404(request)
        body = await _json(request)
        method = body.get("method")
        if not isinstance(method, str) or not method:
            return _error(400, "method がありません")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            return _error(400, "params はオブジェクトにしてください")
        try:
            timeout = min(float(body.get("timeout") or hub.cfg.rpc_timeout), 300.0)
        except (TypeError, ValueError):
            return _error(400, "timeout は秒数で指定してください")
        try:
            res = await hub.rpc(number, method, params, timeout=timeout)
        except DeviceOffline as e:
            return _error(409, str(e))
        except asyncio.TimeoutError:
            return _error(504, f"{hub._name(number)} から返事がありません（{method}）")
        if "binary" in res:
            res = {**res, "binary": base64.b64encode(res["binary"]).decode("ascii")}
        return web.json_response(res)

    @routes.get("/api/devices/{number}/screenshot")
    async def screenshot(request):
        number = device_or_404(request)
        try:
            res = await hub.rpc(number, "screenshot", {"hideOverlay": True}, timeout=60)
        except DeviceOffline as e:
            return _error(409, str(e))
        except asyncio.TimeoutError:
            return _error(504, "スクリーンショットの返事がありません")
        if res.get("status") != "success" or not isinstance(res.get("result"), str):
            return _error(502, f"スクリーンショットを撮れませんでした: {res.get('error') or res}")
        try:
            png = base64.b64decode(res["result"])
        except (binascii.Error, ValueError):
            return _error(502, "スクリーンショットの形式が読めません")
        return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})

    @routes.post("/api/devices/{number}/awake")
    async def awake(request):
        """画面の点灯を保つ／やめる（操作する間だけ点け、終わったら消す）。"""
        number = device_or_404(request)
        enabled = bool((await _json(request)).get("enabled", True))
        try:
            res = await hub.rpc(number, "screen/keepAwake/set", {"enabled": enabled})
        except DeviceOffline as e:
            return _error(409, str(e))
        except asyncio.TimeoutError:
            return _error(504, "点灯の設定に返事がありません")
        return web.json_response(res)

    @routes.post("/api/devices/{number}/recover")
    async def recover(request):
        number = device_or_404(request)
        try:
            res = await hub.recover(number, reason="manual")
        except DeviceOffline as e:
            return _error(409, str(e))
        except asyncio.TimeoutError:
            return _error(504, "見張り役アプリから復旧の結果が返ってきません")
        return web.json_response(res)

    @routes.get("/api/devices/{number}/events")
    async def events(request):
        number = device_or_404(request)
        link = hub.portal.get(number)
        return web.json_response({"events": list(link.events) if link else []})

    app.add_routes(routes)
    return app


def _tokens_match(given: str, expected: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), expected.encode())


def _sort_key(number: str):
    return (0, int(number), "") if number.isdigit() else (1, 0, number)


async def _json(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text=json.dumps({"error": "JSONが読めません"}), content_type="application/json")
    return data if isinstance(data, dict) else {}


async def serve(cfg: Config) -> None:
    hub = Hub(cfg)
    runners = []
    for app, host, port, what in ((public_app(hub), cfg.public_host, cfg.public_port, "HUB_PUBLIC_HOST"),
                                  (admin_app(hub), cfg.admin_host, cfg.admin_port, "HUB_ADMIN_HOST")):
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, host, port).start()
        except OSError as e:
            for r in runners + [runner]:
                await r.cleanup()
            raise SystemExit(f"{host}:{port} で待ち受けできません（{what} を確認。Tailscale のIPなら tailscale ip -4 の値か）: {e}")
        runners.append(runner)
    log.info("中継サーバーを起動しました（端末の受け口 %s:%s／管理 %s:%s／台帳 %d台）",
             cfg.public_host, cfg.public_port, cfg.admin_host, cfg.admin_port, len(hub.reg.devices))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows など
            pass
    try:
        await stop.wait()
    finally:
        # 端末にきちんと「閉じる」を送ってから止める（端末側がすぐにつなぎ直せるように）
        for runner in reversed(runners):
            await runner.cleanup()
