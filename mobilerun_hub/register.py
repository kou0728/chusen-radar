"""iMac で、USB でつないだ Android を中継サーバーに登録する。標準ライブラリだけで動く（Python 3.9 以上）。

  python3 -m mobilerun_hub.register                 つないでいる端末を選んで登録
  python3 -m mobilerun_hub.register --watch         USB でつないだら自動で登録（番号は次の空き番号）
  python3 -m mobilerun_hub.register --number 12 --label "Pixel 赤" --portal-apk ~/Downloads/portal.apk

やること（1台ごと）:
  1. 中継サーバーの台帳に端末を追加し、その端末専用のトークンをもらう
  2. 登録のあいだだけ、USB給電中の画面点灯を保つ
  3. 公式の Mobilerun Portal を入れる（入っていなければ）
  4. ユーザー補助・重ねて表示・電池の最適化の対象外・バックグラウンド動作を許可する
  5. Portal の接続先を中継サーバーに設定し、再起動後の再接続をONにする
  6. 中継サーバーにつながり、操作に返事をするかを確かめる
  7. 機種・Androidの版・USBシリアルを台帳に書き、点灯を元に戻す
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
import time

from .client import Client, HubError, load_env_file

PORTAL = "com.mobilerun.portal"
A11Y_DEFAULT = f"{PORTAL}/{PORTAL}.service.MobilerunAccessibilityService"


class RegisterError(Exception):
    pass


class Adb:
    def __init__(self, serial: str | None = None):
        self.bin = os.environ.get("ADB") or shutil.which("adb") or "adb"
        self.serial = serial

    def run(self, *args: str, timeout: float = 60, check: bool = True) -> str:
        cmd = [self.bin] + (["-s", self.serial] if self.serial else []) + list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except FileNotFoundError:
            raise RegisterError("adb が見つかりません（brew install android-platform-tools）")
        except subprocess.TimeoutExpired:
            raise RegisterError(f"adb が応答しません: {' '.join(args)[:60]}")
        out = (p.stdout or b"").decode("utf-8", "replace")
        if check and p.returncode != 0:
            raise RegisterError(((p.stderr or b"").decode("utf-8", "replace") or out).strip()[:300])
        return out

    def shell(self, command: str, check: bool = True, timeout: float = 60) -> str:
        return self.run("shell", command, check=check, timeout=timeout)

    def devices(self) -> list[tuple[str, str]]:
        out = self.run("devices", check=False)
        rows = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and not line.startswith("*"):
                rows.append((parts[0], parts[1]))
        return rows

    def prop(self, name: str) -> str:
        return self.shell(f"getprop {name}", check=False).strip()

    def content_insert(self, uri: str, binds: list[str]) -> str:
        args = ["content", "insert", "--uri", uri]
        for b in binds:
            args += ["--bind", b]
        out = self.shell(" ".join(_q(a) for a in args), check=False)
        if "error" in out.lower() or "exception" in out.lower():
            raise RegisterError(f"Portal の設定に失敗しました（{uri.rsplit('/', 1)[-1]}）: {out.strip()[:200]}")
        return out


def _q(s: str) -> str:
    """adb shell に渡す1語を、端末側のシェル用にクォートする。"""
    return "'" + s.replace("'", "'\\''") + "'"


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def say(msg: str) -> None:
    print(f"・{msg}", flush=True)


# ---------------------------------------------------------------------------- 手順
def find_a11y_component(adb: Adb) -> str:
    out = adb.shell(f"dumpsys package {PORTAL}", check=False)
    m = re.search(r"(com\.mobilerun\.portal/[\w.$]*AccessibilityService)", out)
    if m:
        comp = m.group(1)
        return comp if "/." not in comp else comp.replace("/.", f"/{PORTAL}.")
    return A11Y_DEFAULT


def enable_accessibility(adb: Adb) -> str:
    comp = find_a11y_component(adb)
    cur = adb.shell("settings get secure enabled_accessibility_services", check=False).strip()
    items = [x for x in cur.split(":") if x and x != "null"]
    if comp not in items:
        items.append(comp)
        adb.shell("settings put secure enabled_accessibility_services " + _q(":".join(items)))
    adb.shell("settings put secure accessibility_enabled 1")
    return comp


def configure_portal(adb: Adb, url: str, token: str) -> bool:
    """接続先とトークンを入れて接続をONにする。戻り値は「再起動後の再接続」をONにできたか。"""
    root = f"content://{PORTAL}"
    adb.content_insert(f"{root}/configure_reverse_connection",
                       [f"url_base64:s:{_b64(url)}", f"token_base64:s:{_b64(token)}", "enabled:b:true"])
    try:
        adb.content_insert(f"{root}/cloud/reconnect_after_restart", ["enabled:b:true"])
    except RegisterError:
        return False
    return True


def allow_background(adb: Adb) -> None:
    for cmd in (
        f"dumpsys deviceidle whitelist +{PORTAL}",          # 電池の最適化の対象外（Unrestricted Battery）
        f"cmd appops set {PORTAL} RUN_ANY_IN_BACKGROUND allow",
        f"cmd appops set {PORTAL} RUN_IN_BACKGROUND allow",
        f"appops set {PORTAL} SYSTEM_ALERT_WINDOW allow",    # 重ねて表示（画面に枠を出すため）
        f"pm grant {PORTAL} android.permission.POST_NOTIFICATIONS",
    ):
        adb.shell(cmd, check=False)  # 機種やAndroidの版によっては不要・未対応なので失敗しても続ける


def wait_online(client: Client, number: str, seconds: float = 90) -> dict:
    end = time.time() + seconds
    while time.time() < end:
        d = client.device(number)
        if d.get("online"):
            return d
        time.sleep(2)
    raise RegisterError("Portal が中継サーバーにつながりません。端末の画面に許可・PINの画面が出ていないか確認してください")


def verify(client: Client, number: str) -> list[str]:
    notes = []
    res = client.rpc(number, "version", timeout=20)
    notes.append(f"Portal {res.get('result')}" if res.get("status") == "success" else f"version の応答: {res}")
    res = client.rpc(number, "state", {"filter": True}, timeout=30)
    if res.get("status") != "success":
        raise RegisterError(f"画面の読み取りができません（ユーザー補助がONか確認）: {res.get('error') or res}")
    notes.append("画面の読み取りOK")
    return notes


def register_device(adb: Adb, client: Client, number: str | None, label: str, apk: str | None,
                    rotate: bool = False) -> str:
    model, android, usb = adb.prop("ro.product.model"), adb.prop("ro.build.version.release"), adb.prop("ro.serialno")
    print(f"\n{model}（Android {android}／シリアル {adb.serial}）を登録します", flush=True)
    res = client.add(number, label=label or model, rotate=rotate)
    number = res["number"]
    say(f"台帳に端末{number}を追加しました")
    adb.shell("svc power stayon usb", check=False)  # 登録中だけ点灯を保つ
    adb.shell("input keyevent KEYCODE_WAKEUP", check=False)
    try:
        if PORTAL not in adb.shell(f"pm list packages {PORTAL}", check=False):
            if not apk:
                raise RegisterError("Portal が入っていません。--portal-apk に公式のAPKを指定してください")
            say("Portal を入れています…")
            adb.run("install", "-r", "-g", os.path.expanduser(apk), timeout=300)
        say(f"ユーザー補助をONにしました（{enable_accessibility(adb)}）")
        allow_background(adb)
        say("電池の最適化の対象外・バックグラウンド動作・重ねて表示を許可しました")
        reconnect = configure_portal(adb, res["portal_url"], res["portal_token"])
        say(f"接続先を {res['portal_url']} にしました")
        if reconnect:
            say("再起動後の再接続をONにしました")
        else:
            say("※ 再起動後の再接続をONにできませんでした。Portal の画面で「Reconnect After Restart」をONにしてください")
        adb.shell(f"monkey -p {PORTAL} -c android.intent.category.LAUNCHER 1", check=False)  # 初回の許可画面を出すため一度開く
        say("中継サーバーにつながるのを待っています…（端末に許可の画面が出たら押してください）")
        wait_online(client, number)
        for n in verify(client, number):
            say(n)
        client.edit(number, model=model, android=android, usb_serial=usb)
        say(f"台帳を更新しました（端末{number}）")
    finally:
        adb.shell("svc power stayon false", check=False)
    print(f"\n登録完了：端末{number}。USB を抜いてかまいません。", flush=True)
    print(f"  見張り役（復旧アプリ）の接続先: {res['guardian_url']}\n  見張り役のトークン: {res['guardian_token']}（この画面にしか出ません）", flush=True)
    return number


def choose(adb: Adb) -> str:
    rows = adb.devices()
    for serial, state in rows:
        if state == "unauthorized":
            print(f"※ {serial} は端末側で「USBデバッグを許可」を押してください")
    ready = [s for s, st in rows if st == "device"]
    if not ready:
        raise RegisterError("USB でつながっている端末がありません（USBデバッグがONか、ケーブルを確認）")
    if len(ready) == 1:
        return ready[0]
    for i, s in enumerate(ready, 1):
        print(f"  {i}. {s}  {Adb(s).prop('ro.product.model')}")
    pick = input("登録する端末の番号を選んでください: ").strip()
    if not pick.isdigit() or not 1 <= int(pick) <= len(ready):
        raise RegisterError("選び直してください")
    return ready[int(pick) - 1]


def watch(client: Client, apk: str | None) -> None:
    """USB でつながった端末を見張り、新しい端末なら自動で登録する。"""
    print("USB でつながるのを待っています（止めるときは Ctrl+C）…", flush=True)
    seen: set[str] = set()
    hinted: set[str] = set()
    while True:
        rows = Adb().devices()
        present = {s for s, _ in rows}
        seen &= present  # 抜かれた端末は、次に挿した時にまた見る
        registered = {d.get("usb_serial") for d in client.devices().get("devices", [])}
        for serial, state in rows:
            if state == "unauthorized" and serial not in hinted:
                hinted.add(serial)
                print(f"※ {serial}: 端末の画面で「USBデバッグを許可」を押してください", flush=True)
            if state != "device" or serial in seen:
                continue
            seen.add(serial)
            adb = Adb(serial)
            if adb.prop("ro.serialno") in registered:
                print(f"（{serial} は登録済みです。設定をやり直すなら --number を付けて実行）", flush=True)
                continue
            try:
                register_device(adb, client, None, "", apk)
            except (RegisterError, HubError) as e:
                print(f"× 登録できませんでした（{serial}）: {e}", flush=True)
        time.sleep(2)


def main() -> None:
    ap = argparse.ArgumentParser(prog="python3 -m mobilerun_hub.register", description="USB の Android を中継サーバーに登録する")
    ap.add_argument("--serial", help="adb のシリアル（2台以上つないでいる時）")
    ap.add_argument("--number", help="端末番号（省略すると次の空き番号）")
    ap.add_argument("--label", default="", help="台帳に出す名前（省略すると機種名）")
    ap.add_argument("--portal-apk", help="Portal が入っていない時に入れる、公式 Mobilerun Portal の APK")
    ap.add_argument("--rotate", action="store_true", help="登録済みの番号のトークンを作り直して設定し直す")
    ap.add_argument("--watch", action="store_true", help="USB でつないだら自動で登録し続ける")
    a = ap.parse_args()
    apk = a.portal_apk or os.environ.get("PORTAL_APK") or load_env_file().get("PORTAL_APK")
    try:
        client = Client()
        if a.watch:
            watch(client, apk)
        adb = Adb(a.serial or choose(Adb()))
        register_device(adb, client, a.number, a.label, apk, rotate=a.rotate)
    except (RegisterError, HubError) as e:
        print(f"\n× {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
