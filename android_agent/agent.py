"""Android端末で公式アプリを開き、アプリ限定の抽選を読む。

  python -m android_agent doctor        つながり・端末・設定の確認
  python -m android_agent setup         読み取りが安定する設定を端末に入れる（アニメーションOFFなど）
  python -m android_agent current       いま前面に出ているアプリのパッケージ名（レシピ作り用）
  python -m android_agent packages      端末に入っているアプリの一覧
  python -m android_agent screen        いまの画面の文字と位置の一覧（レシピ作り用）
  python -m android_agent shot [file]   スクリーンショットを保存
  python -m android_agent try <id>      1つのアプリを試しに読む（表示するだけ。GitHubへは送らない）
  python -m android_agent run           有効なアプリを読み、結果を GitHub の data/app_found.json へ送る
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from collector import dates
from collector.main import load_keywords

from . import push, screen, vision
from .adb import AdbError, Device
from .extract import dedup, extract_pages, from_fields
from .recipe import RecipeError, Runner

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "config" / "apps.yaml"
HERE = Path(__file__).resolve().parent
LOCAL = Path(os.environ.get("CHUSEN_ANDROID_STATE") or HERE / ".state.json")  # 母艦だけで使う記録
FOUND = HERE / ".found.json"            # 最後に送った内容（母艦側の控え）
PAUSE = HERE / "PAUSE"                  # このファイルがある間は自動で読まない（手で端末を触る時に置く）
REMOTE_PATH = "data/app_found.json"     # GitHub 上の置き場所（サイトの自動更新がここを読む）
HEARTBEAT = timedelta(hours=2)          # 変化がなくても、この間隔で「動いている」ことを送る
DEFAULT_STEPS = ["restart", {"read_pages": 3}]
HOT_C = 42                              # 電池温度がこれ以上なら知らせる

log = logging.getLogger("android_agent")


# --- 設定と記録 ---
def load_apps() -> dict:
    cfg = (yaml.safe_load(APPS.read_text(encoding="utf-8")) if APPS.exists() else None) or {}
    cfg["defaults"] = cfg.get("defaults") or {}
    cfg["apps"] = cfg.get("apps") or []
    ids = [a.get("id") for a in cfg["apps"]]
    bad = sorted({str(i) for i in ids if not i or ids.count(i) > 1})
    if bad:
        raise SystemExit(f"config/apps.yaml: id が空か重複しています: {', '.join(bad)}")
    return cfg


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def store_name(app: dict) -> str:
    return app.get("store") or app.get("name") or app["id"]


def is_due(app: dict, local: dict, now: datetime, defaults: dict) -> bool:
    every = int(app.get("every_minutes") or defaults.get("every_minutes") or 60)
    last = (local.get("last_run") or {}).get(app["id"])
    if not last:
        return True
    return now - datetime.fromisoformat(last) >= timedelta(minutes=every - 2)  # タイマーの揺れを少し許す


def in_quiet_hours(now: datetime, defaults: dict) -> bool:
    q = defaults.get("quiet_hours")
    if not q or len(q) != 2:
        return False
    a, b = int(q[0]), int(q[1])
    return a <= now.hour < b if a <= b else (now.hour >= a or now.hour < b)


def device_info(dev: Device) -> dict:
    return {"model": dev.prop("ro.product.model"), "android": dev.prop("ro.build.version.release"), **dev.battery()}


# --- 1アプリ分 ---
def vision_items(shots: list[bytes], app: dict, kw, now: datetime, defaults: dict, local: dict):
    """スクリーンショットを Claude API で読む。同じ画面は読み直さない・1日の枚数に上限あり。"""
    vs = local.setdefault("vision", {})
    today, month = now.date().isoformat(), now.strftime("%Y-%m")
    if vs.get("day") != today:
        vs.update(day=today, count=0)
    if vs.get("month") != month:
        vs.update(month=month, calls=0, input_tokens=0, output_tokens=0)
    cache = vs.setdefault("cache", {})
    old = (now.date() - timedelta(days=14)).isoformat()
    for k in [k for k, v in cache.items() if v.get("day", "") < old]:
        del cache[k]
    limit = int(defaults.get("vision_daily_limit", 20))
    model = defaults.get("vision_model") or vision.DEFAULT_MODEL
    effort = defaults.get("vision_effort") or "low"
    lots, note = [], ""
    for png in shots:
        try:
            img = vision.shrink(png)
        except vision.VisionError as e:
            return lots, str(e)
        except OSError:
            continue  # 壊れた画像
        key = hashlib.sha1(img).hexdigest()
        if key in cache:
            results = cache[key]["r"]
        elif vs["count"] >= limit:
            note = f"画像読み取りは今日の上限（{limit}枚）に達しました"
            continue
        else:
            try:
                results, usage = vision.read(img, store_name(app), model=model, effort=effort)
            except vision.VisionError as e:
                return lots, str(e)
            vs["count"] += 1
            vs["calls"] = vs.get("calls", 0) + 1
            vs["input_tokens"] = vs.get("input_tokens", 0) + usage["input_tokens"]
            vs["output_tokens"] = vs.get("output_tokens", 0) + usage["output_tokens"]
            cache[key] = {"r": results, "day": today}
        for r in results:
            lot = from_fields(r, app, kw, now)
            if lot:
                lots.append(lot)
    return lots, note


def run_app(dev: Device, app: dict, kw, now: datetime, defaults: dict, local: dict, say=None, use_vision: bool | None = None):
    st = {"name": store_name(app), "url": app.get("url") or "", "checked": dates.iso(now)}
    vision_on = bool(app.get("vision", defaults.get("vision", False))) if use_vision is None else use_vision
    runner = Runner(dev, app, shots=vision_on, say=say)
    lots = []
    try:
        if app.get("package") and not dev.installed(app["package"]):
            raise RecipeError(f"アプリ {app['package']} が端末に入っていません")
        runner.run(app.get("steps") or DEFAULT_STEPS)
        lots = extract_pages(runner.pages, app, kw, now)
        notes = []
        if vision_on and runner.shots:
            vlots, vnote = vision_items(runner.shots, app, kw, now, defaults, local)
            lots = dedup(lots + vlots)
            if vnote:
                notes.append(vnote)
        if not runner.pages:
            notes.append("画面を読む命令（read / read_pages / each）がありません")
        st.update(ok=True, found=len(lots))
        if notes:
            st["note"] = " / ".join(notes)[:160]
    except (RecipeError, AdbError, ValueError) as e:
        st.update(ok=False, note=str(e)[:160])
        lots = []
    finally:
        if app.get("package"):
            try:
                dev.force_stop(app["package"])
            except AdbError:
                pass
    return st, lots, runner


# --- 送信・通知 ---
def content_digest(payload: dict) -> str:
    core = {
        "device_ok": (payload.get("device") or {}).get("ok"),
        "apps": {k: [v.get("ok"), v.get("found"), v.get("note")] for k, v in sorted((payload.get("apps") or {}).items())},
        "items": payload.get("items") or [],
    }
    return hashlib.sha1(json.dumps(core, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def send(payload: dict, local: dict, now: datetime, force: bool = False) -> str:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO") or push.guess_repo(ROOT)
    branch = os.environ.get("GITHUB_BRANCH") or "main"
    if not token or not repo:
        return "GITHUB_TOKEN / GITHUB_REPO が無いので GitHub へは送りません"
    digest = content_digest(payload)
    last = local.get("last_push")
    beat = not last or now - datetime.fromisoformat(last) >= HEARTBEAT
    if digest == local.get("digest") and not beat and not force:
        return "変化がないので送信は省略しました"
    body = (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    try:
        wrote = push.put_file(repo, REMOTE_PATH, body, token, branch, f"アプリの抽選情報を更新 {now:%m/%d %H:%M}")
    except push.PushError as e:
        return f"GitHub へ送れませんでした: {e}"
    local["digest"] = digest
    local["last_push"] = now.isoformat()
    return f"GitHub（{repo} の {REMOTE_PATH}）へ送りました" if wrote else "GitHub 側と同じ内容でした"


def health_lines(statuses: dict, device: dict, touched: bool, local: dict) -> list[str]:
    """調子が変わった時だけ知らせる（毎回は送らない）。"""
    prev = local.get("health") or {}
    cur = dict(prev)
    lines = []
    if touched:
        ok = device.get("ok", True)
        if not ok and prev.get("_device", True):
            lines.append(f"【注意】Android端末につながりません: {device.get('note', '')}")
        if ok and prev.get("_device") is False:
            lines.append("【復旧】Android端末に再びつながりました")
        cur["_device"] = ok
        temp = device.get("temp")
        hot = bool(ok and temp is not None and temp >= HOT_C)
        if hot and not prev.get("_hot"):
            lines.append(f"【注意】Android端末が熱くなっています（電池 {temp}℃）。充電器・置き場所を確認してください")
        cur["_hot"] = hot
    for aid, st in statuses.items():
        if st.get("ok") is None:
            continue
        was = prev.get(aid, True)
        if was and not st["ok"] and device.get("ok", True):
            lines.append(f"【注意】{st.get('name', aid)} のアプリを読めませんでした: {st.get('note', '')}")
        elif not was and st["ok"]:
            lines.append(f"【復旧】{st.get('name', aid)} のアプリを読めるようになりました")
        cur[aid] = bool(st["ok"])
    local["health"] = cur
    return lines


# --- コマンド ---
def connect(cfg: dict) -> Device:
    dev = Device(serial=cfg.get("device") or None)
    dev.ensure_ready()
    return dev


@contextmanager
def single_run():
    """前の回が終わっていない時に、同じ端末を2つのプログラムで同時に触らないようにする。"""
    try:
        import fcntl
    except ImportError:  # Windows
        yield True
        return
    HERE.mkdir(parents=True, exist_ok=True)
    with open(HERE / ".run.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True


def cmd_run(args) -> int:
    if PAUSE.exists() and not args.force:
        log.info("一時停止中です（%s を消すと再開）", PAUSE)
        return 0
    with single_run() as ok:
        if not ok:
            log.info("前の回がまだ動いているので、今回は休みます")
            return 0
        return _run(args)


def _run(args) -> int:
    cfg = load_apps()
    defaults = cfg["defaults"]
    enabled = [a for a in cfg["apps"] if a.get("enabled")]
    if args.only:
        enabled = [a for a in enabled if a["id"] in args.only]
    now = datetime.now(dates.JST)
    local = load_json(LOCAL, {})
    prev = load_json(FOUND, {})
    if not enabled:
        log.info("有効なアプリがありません（config/apps.yaml で enabled: true にしてください）")
    quiet = in_quiet_hours(now, defaults) and not args.force
    due = [] if quiet else [a for a in enabled if args.force or is_due(a, local, now, defaults)]
    if quiet:
        log.info("読まない時間帯（quiet_hours）なので、今回は読みません")

    statuses = dict(prev.get("apps") or {})
    items: dict[str, list] = {}
    for d in prev.get("items") or []:
        items.setdefault(str(d.get("source_id", "")).removeprefix("app:"), []).append(d)
    device = dict(prev.get("device") or {})

    if due:
        kw = load_keywords()
        done: set[str] = set()
        try:
            dev = connect(cfg)
            device = {**device_info(dev), "ok": True}
            dev.wake()
            for app in due:
                log.info("%s を読みます", store_name(app))
                st, lots, _ = run_app(dev, app, kw, now, defaults, local)
                statuses[app["id"]] = st
                if st["ok"]:
                    items[app["id"]] = [lot.to_dict() for lot in lots]
                local.setdefault("last_run", {})[app["id"]] = now.isoformat()
                done.add(app["id"])
                log.info("  → %s", f"{st['found']}件" if st["ok"] else f"失敗: {st['note']}")
            dev.home()
            dev.sleep()
        except AdbError as e:
            device = {**device, "ok": False, "note": str(e)[:160]}
            for app in due:
                if app["id"] not in done:
                    statuses[app["id"]] = {"name": store_name(app), "url": app.get("url") or "",
                                           "checked": dates.iso(now), "ok": False, "note": str(e)[:160]}
            log.error("端末を操作できません: %s", e)

    alive = {a["id"] for a in cfg["apps"] if a.get("enabled")}
    payload = {
        "updated": dates.iso(now),
        "device": device,
        "apps": {k: v for k, v in statuses.items() if k in alive},
        "items": [d for aid, lst in items.items() if aid in alive for d in lst],
    }
    save_json(FOUND, payload)
    push.discord(health_lines(payload["apps"], device, bool(due), local))
    log.info(send(payload, local, now, force=args.push_now))
    save_json(LOCAL, local)
    return 0 if all(st.get("ok") for st in payload["apps"].values()) else 1


def cmd_try(args) -> int:
    with single_run() as ok:
        if not ok:
            print("自動の読み取りが動いている最中です。少し待ってからやり直してください")
            return 1
        return _try(args)


def _try(args) -> int:
    cfg = load_apps()
    app = next((a for a in cfg["apps"] if a["id"] == args.id), None)
    if not app:
        print(f"config/apps.yaml に id: {args.id} がありません")
        return 2
    dev = connect(cfg)
    dev.wake()
    now = datetime.now(dates.JST)
    local = load_json(LOCAL, {})
    print(f"{store_name(app)}（{app.get('package') or 'package 未設定'}）を試します")
    st, lots, runner = run_app(dev, app, load_keywords(), now, cfg["defaults"], local, say=print,
                               use_vision=True if args.vision else None)
    for i, texts in enumerate(runner.texts, 1):
        print(f"\n--- 読んだ画面 {i}（文字 {len(texts)} 個）---")
        for t in texts[:60]:
            print("  " + t[:80])
    print()
    if not st["ok"]:
        print(f"失敗: {st['note']}")
        return 1
    print(f"見つかった抽選: {len(lots)}件" + (f"（{st['note']}）" if st.get("note") else ""))
    for lot in lots:
        period = f"{lot.apply_start or ''}〜{lot.apply_end}"
        print(f"  ・{lot.title}\n    受付 {period}  当選発表 {lot.announce or '-'}  商品 {lot.product or '-'}")
    if runner.shots:
        save_json(LOCAL, local)  # 画像読み取りの回数を記録
    return 0


def cmd_screen(args) -> int:
    dev = connect(load_apps())
    xml = dev.dump()
    if args.xml:
        print(xml)
        return 0
    root = screen.parse(xml)
    pkg, act = dev.focus()
    print(f"前面のアプリ: {pkg or '?'}  画面: {act or '?'}\n")

    def rec(n: screen.Node, clickable: bool) -> None:
        clickable = clickable or n.clickable
        if n.label and n.visible and not n.system:
            x, y = n.center
            rid = f"  id={n.rid.split(':id/')[-1]}" if n.rid else ""
            print(f"{'押せる' if clickable else '      '} ({x:4d},{y:4d})  {n.label[:60]}{rid}")
        for c in n.children:
            rec(c, clickable)

    rec(root, False)
    return 0


def cmd_shot(args) -> int:
    dev = connect(load_apps())
    path = Path(args.file or HERE / "shots" / f"{datetime.now():%Y%m%d-%H%M%S}.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dev.screenshot())
    print(f"保存しました: {path}")
    return 0


def cmd_current(args) -> int:
    pkg, act = connect(load_apps()).focus()
    print(f"package: {pkg or '?'}\nactivity: {act or '?'}")
    return 0


def cmd_packages(args) -> int:
    cfg = load_apps()
    used = {a.get("package"): a["id"] for a in cfg["apps"] if a.get("package")}
    for p in connect(cfg).packages():
        print(p + (f"   ← apps.yaml の {used[p]}" if p in used else ""))
    return 0


def cmd_setup(args) -> int:
    dev = connect(load_apps())
    for key in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
        dev.settings_put("global", key, "0")
    dev.settings_put("system", "screen_off_timeout", "180000")
    print("設定しました: アニメーションOFF（画面を読み取りやすくする）、画面が消えるまで3分")
    print("あとは端末の設定画面で、次の3つを手で確認してください:")
    print("  1. 画面ロック → 「なし」（スワイプも無し）")
    print("  2. 電池 → 充電の上限（80〜90%で止める機能）をON")
    print("  3. Google Play → アプリの自動更新は「Wi-Fi経由のみ」")
    return 0


def cmd_doctor(args) -> int:
    cfg = load_apps()
    problems = 0
    try:
        dev = connect(cfg)
    except AdbError as e:
        print(f"× 端末: {e}")
        return 1
    info = device_info(dev)
    w, h = dev.screen_size()
    print(f"○ 端末: {info['model']}（Android {info['android']}）画面 {w}x{h}  シリアル {dev.serial}")
    print(f"  電池 {info.get('level')}%  温度 {info.get('temp')}℃")
    anim = [dev.settings_get("global", k) for k in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale")]
    if any(a not in ("0", "0.0") for a in anim):
        print("△ アニメーションがONです → python -m android_agent setup で切ると読み取りが安定します")
    apps = [a for a in cfg["apps"] if a.get("enabled")]
    print(f"\n有効なアプリ: {len(apps)}件（config/apps.yaml）")
    for a in apps:
        if not a.get("package"):
            print(f"  × {store_name(a)}: package が未設定")
            problems += 1
        elif not dev.installed(a["package"]):
            print(f"  × {store_name(a)}: {a['package']} が端末に入っていません")
            problems += 1
        else:
            print(f"  ○ {store_name(a)}: {a['package']}")
    if cfg["defaults"].get("vision"):
        vs = load_json(LOCAL, {}).get("vision") or {}
        print(f"\n画像読み取り（Claude API）: 今月 {vs.get('calls', 0)}回  入力 {vs.get('input_tokens', 0)} / 出力 {vs.get('output_tokens', 0)} トークン")
    repo = os.environ.get("GITHUB_REPO") or push.guess_repo(ROOT)
    print(f"\nGitHubへの送信: {'○' if os.environ.get('GITHUB_TOKEN') and repo else '× GITHUB_TOKEN / GITHUB_REPO が未設定'}（{repo or '?'}）")
    print(f"Discord通知: {'○' if os.environ.get('DISCORD_WEBHOOK_URL') else '－（任意）'}")
    return 1 if problems else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%m/%d %H:%M:%S")
    ap = argparse.ArgumentParser(prog="python -m android_agent", description="Android端末で公式アプリの抽選のお知らせを読む")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="有効なアプリを読んで GitHub へ送る（タイマーから呼ぶ）")
    p.add_argument("--only", nargs="+", help="このidのアプリだけ")
    p.add_argument("--force", action="store_true", help="間隔・時間帯を無視して今すぐ全部読む")
    p.add_argument("--push-now", action="store_true", help="変化がなくても GitHub へ送る")
    p = sub.add_parser("try", help="1つのアプリを試しに読む（送信しない）")
    p.add_argument("id")
    p.add_argument("--vision", action="store_true", help="画像読み取り（Claude API）も試す")
    p = sub.add_parser("screen", help="いまの画面の文字と位置の一覧")
    p.add_argument("--xml", action="store_true", help="元のXMLをそのまま出す")
    p = sub.add_parser("shot", help="スクリーンショットを保存")
    p.add_argument("file", nargs="?")
    sub.add_parser("current", help="前面のアプリのパッケージ名")
    sub.add_parser("packages", help="入っているアプリの一覧")
    sub.add_parser("setup", help="読み取りが安定する設定を端末に入れる")
    sub.add_parser("doctor", help="つながり・設定の確認")
    args = ap.parse_args()
    handler = globals()["cmd_" + args.cmd]
    try:
        sys.exit(handler(args))
    except AdbError as e:
        print(f"端末を操作できません: {e}", file=sys.stderr)
        sys.exit(1)
