"""config/apps.yaml の steps（アプリの開き方）を上から順に実行する。

このプログラムは「読むだけ」です。応募・購入などのボタンは、レシピに書いても押しません。
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from . import screen
from .adb import Device

log = logging.getLogger(__name__)

# これらの文字を含むボタンは押さない（誤って応募・購入・送信しないため）
FORBIDDEN = (
    "応募する", "応募を確定", "応募確定", "応募を完了", "応募完了", "エントリーする", "購入する", "購入手続",
    "カートに入れる", "カートへ", "注文", "決済", "支払", "申し込む", "申込む", "申し込みを確定", "申込を確定",
    "予約する", "確定する", "送信する", "同意して",
)
COMMANDS = ("restart", "launch", "stop", "wait", "wait_for", "tap", "dismiss", "scroll", "back", "home",
            "read", "read_pages", "each", "open_url")


class RecipeError(Exception):
    pass


def check_safe(texts: list[str]) -> None:
    for t in texts:
        n = screen.norm(t)
        for w in FORBIDDEN:
            if w in n:
                raise RecipeError(f"安全のため「{t[:30]}」は押しません（このプログラムは読むだけです）")


def parse_step(step) -> tuple[str, object, dict]:
    """'read' / {wait: 3} / {tap: "お知らせ", optional: true} を (命令, 引数, オプション) にする。"""
    if isinstance(step, str):
        name, arg, opts = step, None, {}
    elif isinstance(step, dict):
        # 最初に書いたキーが命令。残りはオプション（{tap: お知らせ, wait: 1} の wait はオプション）
        name = next((k for k in step if k in COMMANDS), None)
        if name is None:
            raise RecipeError(f"命令が読めません: {step}（使えるのは {', '.join(COMMANDS)}）")
        arg = step[name]
        opts = {k: v for k, v in step.items() if k != name}
    else:
        raise RecipeError(f"命令が読めません: {step!r}")
    if name not in COMMANDS:
        raise RecipeError(f"知らない命令です: {name}（使えるのは {', '.join(COMMANDS)}）")
    return name, arg, opts


class Runner:
    def __init__(self, dev: Device, app: dict, shots: bool = False, sleep: Callable[[float], None] = time.sleep,
                 say: Callable[[str], None] | None = None):
        self.dev = dev
        self.app = app
        self.package = app.get("package") or ""
        self.want_shots = shots
        self.sleep = sleep
        self.say = say or log.info
        self.pages: list[str] = []      # 読んだ画面（抽出用HTML）
        self.texts: list[list[str]] = []  # 読んだ画面の文字（確認用）
        self.shots: list[bytes] = []    # 画像読み取り用のスクリーンショット
        self._root: screen.Node | None = None

    # --- 画面 ---
    def root(self) -> screen.Node:
        if self._root is None:
            self._root = screen.parse(self.dev.dump())
        return self._root

    def changed(self) -> None:
        self._root = None

    def capture(self) -> screen.Node:
        root = self.root()
        self.pages.append(screen.to_html(root))
        self.texts.append(screen.labels(root))
        if self.want_shots:
            self.shots.append(self.dev.screenshot())
        return root

    # --- 実行 ---
    def run(self, steps: list) -> None:
        if not self.package:
            raise RecipeError("package が未設定です（python -m android_agent current で調べて config/apps.yaml に書く）")
        for step in steps:
            name, arg, opts = parse_step(step)
            self.say(f"  - {name} {arg if arg is not None else ''}".rstrip())
            getattr(self, "do_" + name)(arg, opts)

    def do_restart(self, arg, opts) -> None:
        self.dev.force_stop(self.package)
        self.sleep(1)
        self.do_launch(arg, opts)

    def do_launch(self, arg, opts) -> None:
        self.dev.launch(self.package)
        self.sleep(float(arg or self.app.get("launch_wait", 6)))
        self.changed()

    def do_stop(self, arg, opts) -> None:
        self.dev.force_stop(self.package)
        self.changed()

    def do_wait(self, arg, opts) -> None:
        self.sleep(float(arg or 2))
        self.changed()

    def do_wait_for(self, arg, opts) -> None:
        limit = float(opts.get("timeout", 15))
        waited = 0.0
        while True:
            self.changed()
            if screen.find(self.root(), text=str(arg), contains=True):
                return
            if waited >= limit:
                if opts.get("optional"):
                    return
                raise RecipeError(f"「{arg}」が {limit:.0f} 秒待っても出てきません（画面が変わった可能性）")
            self.sleep(1.5)
            waited += 1.5

    def _target(self, arg) -> tuple[int, int, list[str]] | None:
        spec = arg if isinstance(arg, dict) else {"text": str(arg), "contains": True}
        root = self.root()
        if spec.get("xy"):
            x, y = (int(v) for v in spec["xy"])
            node = screen.node_at(root, x, y)
            texts = [n.label for n in node.walk() if n.label] if node else []
            return x, y, texts
        node = screen.find(root, text=spec.get("text"), contains=bool(spec.get("contains", True)),
                           rid=spec.get("id"), index=int(spec.get("index", 0)))
        if node is None:
            return None
        x, y = node.center
        return x, y, [n.label for n in node.walk() if n.label]

    def do_tap(self, arg, opts) -> None:
        hit = self._target(arg)
        if hit is None:
            if opts.get("optional") or (isinstance(arg, dict) and arg.get("optional")):
                return
            raise RecipeError(f"押す場所「{arg}」が画面に見つかりません（アプリの見た目が変わった可能性）")
        x, y, texts = hit
        check_safe(texts)
        self.dev.tap(x, y)
        self.sleep(float(opts.get("wait", 2.5)))
        self.changed()

    def do_dismiss(self, arg, opts) -> None:
        """ポップアップの「閉じる」など。出ていなければ何もしない。"""
        words = arg if isinstance(arg, list) else [arg]
        for _ in range(3):  # ポップアップが続けて出ることがある
            node = next((n for w in words for n in [screen.find(self.root(), text=str(w))] if n), None)
            if node is None:
                return
            check_safe([node.label])
            self.dev.tap(*node.center)
            self.sleep(1.5)
            self.changed()

    def do_scroll(self, arg, opts) -> None:
        spec = arg if isinstance(arg, dict) else {"dir": arg or "down"}
        for _ in range(int(spec.get("times", 1))):
            self.dev.scroll(str(spec.get("dir", "down")))
            self.sleep(1.2)
        self.changed()

    def do_back(self, arg, opts) -> None:
        self.dev.back()
        self.sleep(float(arg or 1.5))
        self.changed()

    def do_home(self, arg, opts) -> None:
        self.dev.home()
        self.sleep(1)
        self.changed()

    def do_read(self, arg, opts) -> None:
        self.capture()

    def do_read_pages(self, arg, opts) -> None:
        """読む → スクロール → 読む … を最大 arg 画面分。画面が動かなくなったら終わり。"""
        last = screen.signature(self.capture())
        for _ in range(int(arg or 3) - 1):
            self.dev.scroll("down")
            self.sleep(1.2)
            self.changed()
            sig = screen.signature(self.root())
            if sig == last:
                break
            last = sig
            self.capture()

    def do_each(self, arg, opts) -> None:
        """一覧の中で match の文字を含む項目を、上から順に開いて読み、戻る。"""
        spec = arg if isinstance(arg, dict) else {"match": [arg]}
        words = [screen.norm(w) for w in (spec.get("match") or ["抽選"])]
        done: set[str] = set()
        for _ in range(int(spec.get("max", 5))):
            node = None
            for n in self.root().walk():
                lab = screen.norm(n.label)
                if not lab or n.system or not n.visible or lab in done:
                    continue
                if any(w in lab for w in words) and not any(f in lab for f in FORBIDDEN):
                    node = n
                    break
            if node is None:
                return
            done.add(screen.norm(node.label))
            self.say(f"    開く: {node.label[:40]}")
            self.dev.tap(*node.center)
            self.sleep(float(spec.get("wait", 3)))
            self.changed()
            self.do_read_pages(spec.get("pages", 2), {})
            self.dev.back()
            self.sleep(float(spec.get("back_wait", 2)))
            self.changed()

    def do_open_url(self, arg, opts) -> None:
        self.dev.open_url(str(arg), self.package)
        self.sleep(float(opts.get("wait", 4)))
        self.changed()
