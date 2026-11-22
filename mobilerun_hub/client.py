"""中継サーバーの管理APIを呼ぶ（iMac などから使う）。標準ライブラリだけで動く（macOS 付属の Python 3.9 でも可）。

接続先とトークンは環境変数か ~/.config/mobilerun-hub.env から読む:
  HUB_ADMIN_URL=http://hub:8701        ← VPS の Tailscale 上の名前
  HUB_ADMIN_TOKEN=...                  ← VPS の /etc/mobilerun-hub.env と同じもの
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_FILE = Path.home() / ".config" / "mobilerun-hub.env"


class HubError(Exception):
    pass


def load_env_file(path: Path = ENV_FILE) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


class Client:
    def __init__(self, url: str | None = None, token: str | None = None):
        file_env = load_env_file()
        self.url = (url or os.environ.get("HUB_ADMIN_URL") or file_env.get("HUB_ADMIN_URL") or "http://127.0.0.1:8701").rstrip("/")
        self.token = token or os.environ.get("HUB_ADMIN_TOKEN") or file_env.get("HUB_ADMIN_TOKEN") or ""
        if not self.token:
            raise HubError(f"HUB_ADMIN_TOKEN が未設定です（{ENV_FILE} に書いてください）")

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 60, raw: bool = False):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("error")
            except (ValueError, AttributeError):
                msg = None
            raise HubError(msg or f"HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise HubError(f"中継サーバーにつながりません（{self.url}）: {e.reason}") from e
        except OSError as e:  # 読み込みの時間切れなど（Python 3.9 では socket.timeout）
            raise HubError(f"中継サーバーから返事がありません（{self.url}）: {e}") from e
        return payload if raw else json.loads(payload or b"{}")

    def _dev(self, number: str) -> str:
        return "/api/devices/" + urllib.parse.quote(str(number), safe="")

    def devices(self) -> dict:
        return self.call("GET", "/api/devices")

    def device(self, number: str) -> dict:
        return self.call("GET", self._dev(number))

    def add(self, number: str | None, label: str = "", rotate: bool = False) -> dict:
        return self.call("POST", "/api/devices", {"number": number, "label": label, "rotate": rotate})

    def edit(self, number: str, **fields) -> dict:
        return self.call("PATCH", self._dev(number), fields)

    def remove(self, number: str) -> dict:
        return self.call("DELETE", self._dev(number))

    def rpc(self, number: str, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        return self.call("POST", self._dev(number) + "/rpc", {"method": method, "params": params or {}, "timeout": timeout},
                         timeout=timeout + 10)

    def screenshot(self, number: str) -> bytes:
        return self.call("GET", self._dev(number) + "/screenshot", timeout=70, raw=True)

    def awake(self, number: str, enabled: bool) -> dict:
        return self.call("POST", self._dev(number) + "/awake", {"enabled": enabled})

    def recover(self, number: str) -> dict:
        return self.call("POST", self._dev(number) + "/recover", {}, timeout=70)
