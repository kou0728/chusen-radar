"""新着の抽選をDiscordに通知する（任意）。環境変数 DISCORD_WEBHOOK_URL を入れたときだけ動きます。

X投稿用の下書きも兼ねるので、運営アカウントでの告知にそのまま使えます。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import requests

log = logging.getLogger(__name__)
FORM = {"web": "WEB", "app": "アプリ", "store": "店頭", "x": "X"}


def _fmt(iso: str | None) -> str:
    if not iso:
        return "未定"
    d = datetime.fromisoformat(iso)
    w = "月火水木金土日"[d.weekday()]
    return f"{d.month}/{d.day}({w}) {d:%H:%M}"


def _line(d: dict) -> str:
    head = f"【{d['store']}】{d.get('product') or d['title']}（{FORM.get(d.get('form'), '-')}）"
    period = f"受付 {_fmt(d.get('apply_start'))}〜{_fmt(d.get('apply_end'))}" if d.get("apply_end") else "受付中（期限未定）"
    return f"{head}\n{period}\n{d.get('url') or ''}"


def send(new: list[dict], changed: list[dict]):
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not hook or not (new or changed):
        return
    parts = []
    if new:
        parts.append("**新着の抽選**\n" + "\n\n".join(_line(d) for d in new[:10]))
    if changed:
        parts.append("**更新があったページ（中身を確認して manual.yaml に追加）**\n" + "\n".join(f"・{s['name']} {s['url']}" for s in changed))
    try:
        requests.post(hook, json={"content": "\n\n".join(parts)[:1900]}, timeout=10)
    except requests.RequestException as e:
        log.warning("Discord通知に失敗: %s", e)
