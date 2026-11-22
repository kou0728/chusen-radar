"""端末の台帳（devices.json）。トークンは元の文字列を保存せず、ハッシュだけを持つ。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

NUMBER_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
EDITABLE = ("label", "notes", "usb_serial", "model", "android")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Registry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.devices: dict[str, dict] = {}
        if self.path.exists():
            self.devices = json.loads(self.path.read_text(encoding="utf-8")).get("devices", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": self.devices}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def add(self, number: str, label: str = "", rotate: bool = False) -> dict:
        """端末を登録し、Portal用と復旧アプリ用のトークンを発行する（トークンはこの時だけ返す）。"""
        number = str(number).strip()
        if not NUMBER_RE.match(number):
            raise ValueError("端末番号は英数字・ハイフン・アンダーバーの32文字以内にしてください")
        cur = self.devices.get(number)
        if cur and not rotate:
            raise KeyError(f"端末番号 {number} はもう登録されています（トークンを作り直すなら rotate）")
        portal_token, guardian_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        d = dict(cur or {"number": number, "created": now_iso()})
        d.update(
            label=label or d.get("label", ""),
            portal_token_sha256=digest(portal_token),
            guardian_token_sha256=digest(guardian_token),
            tokens_issued=now_iso(),
        )
        self.devices[number] = d
        self.save()
        return {"number": number, "portal_token": portal_token, "guardian_token": guardian_token}

    def check(self, number: str | None, token: str | None, kind: str = "portal") -> bool:
        d = self.devices.get(number or "")
        if not d or not token:
            return False
        return hmac.compare_digest(d.get(f"{kind}_token_sha256", ""), digest(token))

    def find_by_token(self, token: str | None, kind: str = "portal") -> str | None:
        if not token:
            return None
        h = digest(token)
        for number, d in self.devices.items():
            if hmac.compare_digest(d.get(f"{kind}_token_sha256", ""), h):
                return number
        return None

    def update(self, number: str, **fields) -> None:
        d = self.devices.get(number)
        if d is not None:
            d.update(fields)
            self.save()

    def edit(self, number: str, fields: dict) -> dict:
        d = self.devices[number]
        for k in EDITABLE:
            if k in fields:
                d[k] = str(fields[k])[:200]
        self.save()
        return d

    def remove(self, number: str) -> bool:
        if self.devices.pop(number, None) is None:
            return False
        self.save()
        return True

    def next_number(self) -> str:
        nums = [int(n) for n in self.devices if n.isdigit()]
        return str(max(nums) + 1 if nums else 1)
