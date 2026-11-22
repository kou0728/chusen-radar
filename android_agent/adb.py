"""adb（Android Debug Bridge）の薄いラッパー。端末への操作はすべてここを通す。

使うのは Android 標準のコマンド（uiautomator / input / am / screencap）だけなので、
端末側に追加のアプリを入れる必要はありません。
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time

DUMP_PATH = "/sdcard/chusen_radar_dump.xml"


class AdbError(Exception):
    pass


class Device:
    def __init__(self, serial: str | None = None, adb: str | None = None, timeout: float = 30):
        self.adb = adb or os.environ.get("ADB") or shutil.which("adb") or "adb"
        self.serial = serial or os.environ.get("ANDROID_SERIAL") or None
        self.timeout = timeout
        self._size: tuple[int, int] | None = None

    # --- 低レベル ---
    def run(self, *args: str, timeout: float | None = None, check: bool = True) -> bytes:
        cmd = [self.adb] + (["-s", self.serial] if self.serial else []) + list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout or self.timeout)
        except FileNotFoundError as e:
            raise AdbError("adb が見つかりません。Android SDK Platform-Tools（adb）を入れてください") from e
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"adb が応答しません（{' '.join(args)[:60]}）。USBを挿し直してください") from e
        if check and p.returncode != 0:
            msg = (p.stderr or p.stdout).decode("utf-8", "replace").strip()
            raise AdbError(msg[:300] or f"adb {' '.join(args)[:60]} が失敗しました")
        return p.stdout

    def shell(self, command: str, timeout: float | None = None, check: bool = True) -> str:
        return self.run("shell", command, timeout=timeout, check=check).decode("utf-8", "replace")

    # --- 接続と状態 ---
    def ensure_ready(self) -> None:
        out = self.run("devices", check=False).decode("utf-8", "replace")
        rows = [line.split() for line in out.splitlines()[1:] if line.strip() and not line.startswith("*")]
        if self.serial:
            rows = [r for r in rows if r[0] == self.serial]
        if not rows:
            raise AdbError("Android端末が見つかりません（USBケーブル・USBデバッグがONか確認してください）")
        if len(rows) > 1:
            raise AdbError("端末が2台以上つながっています。ANDROID_SERIAL に使う端末のシリアルを入れてください")
        state = rows[0][1] if len(rows[0]) > 1 else "?"
        if state == "unauthorized":
            raise AdbError("端末の画面で「USBデバッグを許可」を押してください（unauthorized）")
        if state != "device":
            raise AdbError(f"端末の状態が {state} です。USBを挿し直してください")
        self.serial = rows[0][0]

    def prop(self, name: str) -> str:
        return self.shell(f"getprop {name}", check=False).strip()

    def screen_size(self) -> tuple[int, int]:
        if self._size is None:
            out = self.shell("wm size")
            sizes = re.findall(r"(\d+)x(\d+)", out)
            if not sizes:
                raise AdbError(f"画面サイズが取れません: {out.strip()[:80]}")
            w, h = sizes[-1]  # Override size があれば、そちらが実際の表示サイズ
            self._size = (int(w), int(h))
        return self._size

    def battery(self) -> dict:
        out = self.shell("dumpsys battery", check=False)

        def num(key: str) -> int | None:
            m = re.search(rf"^\s*{key}:\s*(-?\d+)", out, re.M)
            return int(m.group(1)) if m else None

        temp = num("temperature")
        return {"level": num("level"), "temp": temp / 10 if temp is not None else None}

    def focus(self) -> tuple[str, str]:
        """前面に出ているアプリの (パッケージ名, 画面名)。"""
        out = self.shell("dumpsys window", check=False)
        for key in ("mCurrentFocus", "mFocusedApp"):
            m = re.search(key + r"=.*?\s([\w.]+)/([\w.$]+)", out)
            if m:
                return m.group(1), m.group(2)
        return "", ""

    def packages(self) -> list[str]:
        out = self.shell("pm list packages -3", check=False)
        return sorted(line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("package:"))

    def installed(self, package: str) -> bool:
        out = self.shell(f"pm path {shlex.quote(package)}", check=False)
        return "package:" in out

    def settings_get(self, namespace: str, key: str) -> str:
        return self.shell(f"settings get {namespace} {key}", check=False).strip()

    def settings_put(self, namespace: str, key: str, value: str) -> None:
        self.shell(f"settings put {namespace} {key} {shlex.quote(str(value))}")

    # --- 画面の点灯 ---
    def wake(self) -> None:
        self.shell("input keyevent KEYCODE_WAKEUP")
        time.sleep(0.6)
        self.shell("wm dismiss-keyguard", check=False)  # 画面ロック「なし」なら、これでホームが出る
        time.sleep(0.6)

    def sleep(self) -> None:
        self.shell("input keyevent KEYCODE_SLEEP", check=False)

    # --- 操作 ---
    def home(self) -> None:
        self.shell("input keyevent KEYCODE_HOME", check=False)

    def back(self) -> None:
        self.shell("input keyevent KEYCODE_BACK")

    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 450) -> None:
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")

    def scroll(self, direction: str = "down") -> None:
        """down = 続きを表示（指を下から上へ）、up = 前に戻る。"""
        w, h = self.screen_size()
        lo, hi = int(h * 0.72), int(h * 0.32)
        if direction == "up":
            lo, hi = hi, lo
        self.swipe(w // 2, lo, w // 2, hi)

    def launch(self, package: str) -> None:
        out = self.shell(f"monkey -p {shlex.quote(package)} -c android.intent.category.LAUNCHER 1", check=False)
        if "No activities found" in out or "monkey aborted" in out:
            raise AdbError(f"アプリ {package} を起動できません（インストールされているか確認してください）")

    def force_stop(self, package: str) -> None:
        self.shell(f"am force-stop {shlex.quote(package)}", check=False)

    def open_url(self, url: str, package: str | None = None) -> None:
        cmd = f"am start -W -a android.intent.action.VIEW -d {shlex.quote(url)}"
        if package:
            cmd += f" {shlex.quote(package)}"
        out = self.shell(cmd, check=False)
        if "Error" in out:
            raise AdbError(f"URLを開けません: {out.strip()[:120]}")

    # --- 読み取り ---
    def dump(self, retries: int = 3) -> str:
        """いまの画面の部品（文字・位置）を XML で返す。"""
        last = ""
        for i in range(retries):
            out = self.shell(f"uiautomator dump {DUMP_PATH}", timeout=45, check=False)
            if "dumped to" in out:
                xml = self.run("exec-out", f"cat {DUMP_PATH}", timeout=20).decode("utf-8", "replace")
                if "<hierarchy" in xml:
                    return xml
            last = out.strip()
            time.sleep(1.5 * (i + 1))  # アニメーション中は読めないことがあるので、少し待ってやり直す
        raise AdbError(f"画面を読み取れませんでした: {last[:120]}")

    def screenshot(self) -> bytes:
        data = self.run("exec-out", "screencap -p", timeout=30)
        if not data.startswith(b"\x89PNG"):
            raise AdbError("スクリーンショットが取れませんでした")
        return data
