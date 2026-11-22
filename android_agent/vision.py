"""スクリーンショットから抽選の告知を読む（任意・有料：Claude API）。

画像だけの告知（バナー）は画面の文字情報に出てこないので、ここで読みます。
config/apps.yaml で vision: true にして、母艦に ANTHROPIC_API_KEY を設定した時だけ動きます。
料金の目安と上限の決め方は docs/paid-plan.md を見てください。
"""
from __future__ import annotations

import base64
import io
import json
import logging

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
MAX_EDGE = 1600      # 長辺をこのピクセルまで縮める（料金は画素数にほぼ比例するため）
CROP_TOP = 0.05      # 上端のステータスバー（時計）を切る。毎分変わって「画面が変わった」扱いになるのを防ぐ

SCHEMA = {
    "type": "object",
    "properties": {
        "lotteries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "product": {"type": "string"},
                    "apply_period": {"type": "string"},
                    "announce": {"type": "string"},
                    "purchase_period": {"type": "string"},
                    "conditions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "product", "apply_period", "announce", "purchase_period", "conditions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lotteries"],
    "additionalProperties": False,
}

PROMPT = """これは日本のお店の公式スマホアプリの画面です。
トレーディングカード（ポケモンカード、ワンピースカード、遊戯王など）の抽選販売の告知が写っていれば、1件ずつ書き出してください。

- title: 告知の見出し（画面の表記のまま）
- product: 商品名（わからなければ空文字）
- apply_period: 応募・受付の期間（画面の表記のまま。例「11月21日(金)10:00〜11月24日(月)23:59」）
- announce: 当選発表の日時（書かれていなければ空文字）
- purchase_period: 購入・引き取りの期間（書かれていなければ空文字）
- conditions: 応募の条件を短く、最大3つ

画面に書かれていないことは推測で埋めないでください。トレカの抽選販売が写っていなければ lotteries は空の配列にしてください。"""


class VisionError(Exception):
    pass


def shrink(png: bytes, max_edge: int = MAX_EDGE, crop_top: float = CROP_TOP) -> bytes:
    """ステータスバーを切り、長辺を max_edge まで縮めて JPEG にする。"""
    try:
        from PIL import Image
    except ImportError as e:
        raise VisionError("画像読み取りには Pillow が必要です（pip install -r android_agent/requirements-vision.txt）") from e
    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    im = im.crop((0, int(h * crop_top), w, h))
    scale = max_edge / max(im.size)
    if scale < 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def read(jpeg: bytes, store: str, model: str = DEFAULT_MODEL, effort: str = "low", client=None) -> tuple[list[dict], dict]:
    """画像1枚を読んで (抽選のリスト, 使ったトークン数) を返す。"""
    try:
        import anthropic
    except ImportError as e:
        raise VisionError("画像読み取りには anthropic が必要です（pip install -r android_agent/requirements-vision.txt）") from e

    request = dict(
        model=model,
        max_tokens=16000,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(jpeg).decode("ascii")}},
                {"type": "text", "text": f"お店: {store}\n\n{PROMPT}"},
            ],
        }],
    )
    try:
        client = client or anthropic.Anthropic()
        if model.startswith("claude-haiku"):
            # Haiku にはサーバー側の自動フォールバックが無いので、そのまま送る
            resp = client.messages.create(**request)
        else:
            # 安全判定で断られた時は、Anthropic推奨の別モデルでサーバー側が自動でやり直す
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
    except anthropic.AuthenticationError as e:
        raise VisionError("Claude APIのキーが正しくありません（ANTHROPIC_API_KEY を確認）") from e
    except anthropic.PermissionDeniedError as e:
        raise VisionError("このAPIキーではこのモデルを使えません") from e
    except anthropic.RateLimitError as e:
        raise VisionError("Claude APIの利用上限に達しました。しばらく待つか、Consoleで上限を確認してください") from e
    except anthropic.APIStatusError as e:
        raise VisionError(f"Claude APIがエラーを返しました（HTTP {e.status_code}）") from e
    except anthropic.APIConnectionError as e:
        raise VisionError("Claude APIにつながりません（ネットワークを確認）") from e
    except anthropic.AnthropicError as e:  # キー未設定など、送信前のエラー
        raise VisionError(f"Claude APIを使う準備ができていません: {e}") from e

    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    if resp.stop_reason == "refusal":
        log.warning("画像読み取りが断られました（%s）", getattr(resp, "stop_details", None))
        return [], usage
    if resp.stop_reason == "max_tokens":
        log.warning("画像読み取りの出力が途中で切れました")
        return [], usage
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        log.warning("画像読み取りの結果がJSONになっていません")
        return [], usage
    return [x for x in data.get("lotteries", []) if isinstance(x, dict)], usage
