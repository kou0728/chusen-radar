"""コマンド。

  VPS で:
    python -m mobilerun_hub serve                   中継サーバーを動かす（systemd から呼ぶ）
    python -m mobilerun_hub token                   管理トークンにする乱数を作る
  iMac などで（Tailscale 経由で管理APIを使う）:
    python3 -m mobilerun_hub list                   端末一覧（台帳）
    python3 -m mobilerun_hub rpc 12 tap '{"x":200,"y":400}'
    python3 -m mobilerun_hub shot 12 [画面.png]      スクリーンショット
    python3 -m mobilerun_hub awake 12 on|off        操作する間だけ画面の点灯を保つ
    python3 -m mobilerun_hub recover 12             見張り役アプリに復旧を指示
    python3 -m mobilerun_hub remove 12              台帳から外す
  端末の登録は  python3 -m mobilerun_hub.register  （USB でつないで実行）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import sys

from .client import Client, HubError


def _when(iso: str | None) -> str:
    return iso[5:16].replace("T", " ") if iso else "－"


def cmd_serve(a) -> int:
    from .hub import Config, serve  # aiohttp は VPS 側だけで使う

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(serve(Config.from_env()))
    return 0


def cmd_token(a) -> int:
    print(secrets.token_urlsafe(32))
    return 0


def cmd_list(a) -> int:
    data = Client().devices()
    devs = data.get("devices", [])
    if not devs:
        print("まだ端末がありません（python3 -m mobilerun_hub.register で追加）")
        return 0
    print(f"{'番号':<6}{'Portal':<12}{'見張り役':<8}{'最後につながった':<16}名前・機種")
    for d in devs:
        state = "接続中" if d["online"] else f"切断 {int((d.get('offline_for') or 0) // 60)}分"
        model = d.get("model") or d.get("portal", {}).get("device_name", "")
        print(f"{d['number']:<8}{state:<12}{'○' if d['guardian_online'] else '－':<10}{_when(d.get('last_connected')):<18}{d['label']}  {model}")
    return 0


def cmd_rpc(a) -> int:
    try:
        params = json.loads(a.params) if a.params else {}
    except ValueError:
        print("params は JSON で書いてください（例 '{\"x\":200,\"y\":400}'）", file=sys.stderr)
        return 2
    res = Client().rpc(a.number, a.method, params, timeout=a.timeout)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:20000])
    return 0 if res.get("status") == "success" else 1


def cmd_shot(a) -> int:
    path = a.file or f"端末{a.number}.png"
    with open(path, "wb") as f:
        f.write(Client().screenshot(a.number))
    print(f"保存しました: {path}")
    return 0


def cmd_awake(a) -> int:
    res = Client().awake(a.number, a.state == "on")
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("status") == "success" else 1


def cmd_recover(a) -> int:
    print(json.dumps(Client().recover(a.number), ensure_ascii=False))
    return 0


def cmd_remove(a) -> int:
    Client().remove(a.number)
    print(f"台帳から外しました: 端末{a.number}（端末側の Portal の設定は残っています）")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="python3 -m mobilerun_hub", description="Mobilerun Portal の中継サーバー")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="中継サーバーを動かす（VPS）")
    sub.add_parser("token", help="管理トークン用の乱数を作る")
    sub.add_parser("list", help="端末一覧")
    p = sub.add_parser("rpc", help="端末に命令を送る（Portal の method をそのまま）")
    p.add_argument("number")
    p.add_argument("method")
    p.add_argument("params", nargs="?")
    p.add_argument("--timeout", type=float, default=30)
    p = sub.add_parser("shot", help="スクリーンショット")
    p.add_argument("number")
    p.add_argument("file", nargs="?")
    p = sub.add_parser("awake", help="画面の点灯を保つ／やめる")
    p.add_argument("number")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("recover", help="見張り役アプリに復旧を指示")
    p.add_argument("number")
    p = sub.add_parser("remove", help="台帳から外す")
    p.add_argument("number")
    a = ap.parse_args()
    try:
        sys.exit(globals()["cmd_" + a.cmd](a))
    except HubError as e:
        print(f"× {e}", file=sys.stderr)
        sys.exit(1)
