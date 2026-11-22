"""読み取った結果を GitHub へ送る／不調を Discord に知らせる。

GitHub へは「data/app_found.json の1ファイルだけ」を Contents API で書き込みます。
母艦のフォルダは読むだけ（git pull）なので、自動更新のコミットとぶつかりません。
"""
from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
from pathlib import Path

import requests

log = logging.getLogger(__name__)
API = "https://api.github.com"


class PushError(Exception):
    pass


def guess_repo(root: Path) -> str | None:
    """git の origin から owner/repo を読む（GITHUB_REPO が無い時）。"""
    try:
        url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def put_file(repo: str, path: str, content: bytes, token: str, branch: str, message: str) -> bool:
    """ファイルを作成・更新する。中身が同じなら何もしない（False）。"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{API}/repos/{repo}/contents/{path}"
    b64 = base64.b64encode(content).decode("ascii")
    for _ in range(3):
        try:
            r = requests.get(url, headers=headers, params={"ref": branch}, timeout=20)
        except requests.RequestException as e:
            raise PushError(f"GitHubにつながりません: {e}") from e
        if r.status_code == 401:
            raise PushError("GITHUB_TOKEN が無効です（期限切れ・権限不足）")
        if r.status_code not in (200, 404):
            raise PushError(f"GitHubからファイルを読めません（HTTP {r.status_code}）")
        cur = r.json() if r.status_code == 200 else {}
        if cur.get("content") and base64.b64decode(cur["content"]) == content:
            return False
        body = {"message": message, "content": b64, "branch": branch}
        if cur.get("sha"):
            body["sha"] = cur["sha"]
        try:
            r = requests.put(url, headers=headers, json=body, timeout=30)
        except requests.RequestException as e:
            raise PushError(f"GitHubにつながりません: {e}") from e
        if r.status_code in (200, 201):
            return True
        if r.status_code in (409, 422):  # 同時に更新された。読み直してやり直す
            continue
        if r.status_code in (403, 404):
            raise PushError("書き込めません。トークンの権限（Contents: Read and write）と対象リポジトリを確認してください")
        raise PushError(f"GitHubへの書き込みに失敗しました（HTTP {r.status_code}）")
    raise PushError("GitHubへの書き込みが3回ぶつかりました。次の回でやり直します")


def discord(lines: list[str]) -> None:
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not hook or not lines:
        return
    try:
        requests.post(hook, json={"content": "\n".join(lines)[:1900]}, timeout=10)
    except requests.RequestException as e:
        log.warning("Discord通知に失敗: %s", e)
