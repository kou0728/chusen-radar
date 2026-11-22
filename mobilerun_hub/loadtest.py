"""負荷試験：偽の Portal を大量につなぎ、一斉再接続とスクショの同時取得を測る。

本番に入れる前に、VPS の上（または同じ構成の試験機）で動かす:
  python -m mobilerun_hub.loadtest --devices 200 --shot-kb 1500 --parallel 20

中継サーバーを自分で立ち上げてから測るので、本番の台帳には触りません（一時フォルダを使う）。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import resource
import statistics
import tempfile
import time
from pathlib import Path

from aiohttp import ClientSession, TCPConnector, WSMsgType, web

from .hub import Config, Hub, admin_app, public_app

ADMIN = "load-test-admin-token-xxxxxxxxxxxx"


def rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def fake_portal(session, url, token, shot_b64, stop: asyncio.Event):
    ws = await session.ws_connect(url, headers={"Authorization": f"Bearer {token}"}, max_msg_size=0)
    async def loop():
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            req = json.loads(msg.data)
            result = shot_b64 if req["method"] == "screenshot" else {"ok": True}
            await ws.send_json({"id": req["id"], "status": "success", "result": result})
    task = asyncio.ensure_future(loop())
    return ws, task


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0


async def main_async(a):
    tmp = Path(tempfile.mkdtemp())
    cfg = Config(data_dir=tmp, admin_token=ADMIN, public_url="ws://127.0.0.1", heartbeat=25, monitor_interval=3600)
    hub = Hub(cfg, notify=lambda t: asyncio.sleep(0))
    runners = []
    for app, port in ((public_app(hub), a.port), (admin_app(hub), a.port + 1)):
        r = web.AppRunner(app, access_log=None)
        await r.setup()
        await web.TCPSite(r, "127.0.0.1", port).start()
        runners.append(r)
    tokens = {str(i): hub.reg.add(str(i))["portal_token"] for i in range(1, a.devices + 1)}
    shot = base64.b64encode(os.urandom(a.shot_kb * 1024)).decode()
    base = rss_mb()
    print(f"中継サーバー起動直後のメモリ: {base:.0f} MB（台帳 {a.devices} 台）")

    async with ClientSession(connector=TCPConnector(limit=0)) as s:  # 既定の同時100本の上限を外す
        for round_ in ("初回の接続", "一斉再接続"):
            t0 = time.perf_counter()
            conns = await asyncio.gather(*(fake_portal(s, f"ws://127.0.0.1:{a.port}/join/{n}", tok, shot, None)
                                           for n, tok in tokens.items()))
            while len(hub.portal) < a.devices:
                await asyncio.sleep(0.01)
            print(f"{round_}: {a.devices}台がつながるまで {time.perf_counter() - t0:.2f} 秒／メモリ {rss_mb():.0f} MB")
            if round_ == "初回の接続":
                for ws, task in conns:  # 全台が同時に切れる（VPS再起動や回線断を想定）
                    await ws.close()
                    task.cancel()
                while hub.portal:
                    await asyncio.sleep(0.01)

        headers = {"Authorization": f"Bearer {ADMIN}"}
        admin = f"http://127.0.0.1:{a.port + 1}"

        async def one(n, method):
            t = time.perf_counter()
            async with s.post(f"{admin}/api/devices/{n}/rpc", json={"method": method}, headers=headers) as r:
                await r.read()
                return r.status, time.perf_counter() - t

        for method, label in (("tap", "軽い命令"), ("screenshot", f"スクショ（{a.shot_kb}KB）")):
            sem = asyncio.Semaphore(a.parallel)
            async def limited(n):
                async with sem:
                    return await one(n, method)
            t0 = time.perf_counter()
            res = await asyncio.gather(*(limited(n) for n in tokens))
            took = [d for st, d in res if st == 200]
            bad = sum(1 for st, _ in res if st != 200)
            print(f"{label}を全{a.devices}台に（同時{a.parallel}本）: 合計 {time.perf_counter() - t0:.2f} 秒／"
                  f"1件 中央値 {statistics.median(took) * 1000:.0f} ms・95% {pct(took, .95) * 1000:.0f} ms／失敗 {bad}件／メモリ {rss_mb():.0f} MB")
        for ws, task in conns:
            await ws.close()
            task.cancel()
    for r in reversed(runners):
        await r.cleanup()
    print(f"最大メモリ（このプロセス全体。偽Portal分も含む）: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")


def main():
    ap = argparse.ArgumentParser(prog="python -m mobilerun_hub.loadtest")
    ap.add_argument("--devices", type=int, default=200)
    ap.add_argument("--shot-kb", type=int, default=1500, help="スクショ1枚の大きさ（base64にする前）")
    ap.add_argument("--parallel", type=int, default=20, help="同時に取るスクショの数")
    ap.add_argument("--port", type=int, default=18800)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
