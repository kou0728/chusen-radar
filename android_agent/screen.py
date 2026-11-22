"""uiautomator の画面ダンプ（XML）を扱う。文字を探す・押す場所を決める・抽出用のHTMLに直す。"""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

SYSTEM_PACKAGES = {"com.android.systemui"}  # ステータスバーなど。読まない・押さない
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_CHAR_REF = re.compile(r"&#(?:x([0-9a-fA-F]+)|(\d+));")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass
class Node:
    text: str = ""
    desc: str = ""
    rid: str = ""
    cls: str = ""
    pkg: str = ""
    clickable: bool = False
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    children: list["Node"] = field(default_factory=list)

    @property
    def label(self) -> str:
        return (self.text or self.desc or "").strip()

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def visible(self) -> bool:
        x1, y1, x2, y2 = self.bounds
        return x2 > x1 and y2 > y1

    @property
    def system(self) -> bool:
        return self.pkg in SYSTEM_PACKAGES

    def contains(self, x: int, y: int) -> bool:
        x1, y1, x2, y2 = self.bounds
        return x1 <= x < x2 and y1 <= y < y2

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def norm(text: str) -> str:
    """全角・半角や空白の違いを吸収して比べるための正規化。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def _sanitize(xml: str) -> str:
    # 絵文字のサロゲートなど、XMLとして不正な文字参照が混ざることがあるので取り除く
    def fix(m: re.Match) -> str:
        n = int(m.group(1), 16) if m.group(1) else int(m.group(2))
        bad = 0xD800 <= n <= 0xDFFF or (n < 0x20 and n not in (9, 10, 13))
        return "" if bad else m.group(0)

    return _CTRL.sub("", _CHAR_REF.sub(fix, xml))


def parse(xml: str) -> Node:
    try:
        root = ET.fromstring(_sanitize(xml).strip())
    except ET.ParseError as e:
        raise ValueError(f"画面ダンプを読めません: {e}") from e
    return _node(root)


def _node(el: ET.Element) -> Node:
    b = _BOUNDS.match(el.get("bounds", "") or "")
    n = Node(
        text=el.get("text", "") or "",
        desc=el.get("content-desc", "") or "",
        rid=el.get("resource-id", "") or "",
        cls=el.get("class", "") or "",
        pkg=el.get("package", "") or "",
        clickable=el.get("clickable") == "true",
        bounds=tuple(int(v) for v in b.groups()) if b else (0, 0, 0, 0),
    )
    n.children = [_node(c) for c in el if c.tag == "node"]
    return n


def labels(root: Node) -> list[str]:
    """画面に出ている文字を上から順に。"""
    return [n.label for n in root.walk() if n.label and n.visible and not n.system]


def signature(root: Node) -> str:
    """画面の中身が変わったかを比べるための指紋（スクロールの終わりの判定に使う）。"""
    return hashlib.sha1("\n".join(labels(root)).encode()).hexdigest()


def find_all(root: Node, text: str | None = None, contains: bool = False, rid: str | None = None) -> list[Node]:
    t = norm(text) if text is not None else None
    out = []
    for n in root.walk():
        if n.system or not n.visible:
            continue
        if rid and not (n.rid == rid or n.rid.endswith(":id/" + rid)):
            continue
        if t is not None:
            lab = norm(n.label)
            if not lab or (t not in lab if contains else lab != t):
                continue
        out.append(n)
    return out


def find(root: Node, text: str | None = None, contains: bool = False, rid: str | None = None, index: int = 0) -> Node | None:
    hits = find_all(root, text=text, contains=False, rid=rid)
    if not hits and contains and text is not None:
        hits = find_all(root, text=text, contains=True, rid=rid)
    return hits[index] if len(hits) > index else None


def node_at(root: Node, x: int, y: int) -> Node | None:
    """その座標にある一番小さい部品。"""
    best = None
    for n in root.walk():
        if n.system or not n.visible or not n.contains(x, y):
            continue
        if best is None or _area(n) <= _area(best):
            best = n
    return best


def _area(n: Node) -> int:
    x1, y1, x2, y2 = n.bounds
    return (x2 - x1) * (y2 - y1)


def to_html(root: Node) -> str:
    """部品の入れ子をそのまま <div> の入れ子にする。Web巡回と同じ抽出ルール（collector.extract）で読めるようにするため。"""

    def rec(n: Node) -> str:
        if n.system:
            return ""
        inner = "".join(rec(c) for c in n.children)
        head = f"<b>{html.escape(n.label)}</b>" if n.label and n.visible else ""
        if not head and not inner:
            return ""
        return f"<div>{head}{inner}</div>"

    return rec(root)
