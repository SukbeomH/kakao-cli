"""UIAutomator engine: dump the current screen, find nodes, tap / type.

Selectors operate on the parsed `uiautomator dump` XML. KakaoTalk resource-ids are
NOT obfuscated (e.g. com.kakao.talk:id/sendButton), so resource-id + optional parent
scoping is the primary strategy; text/content-desc are fallbacks. Live dumps are the
source of truth — static ids from the APK are only candidates.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET


@dataclass
class Node:
    cls: str
    rid: str
    text: str
    desc: str
    bounds: tuple[int, int, int, int]
    _el: ET.Element

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2


_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _mk(el: ET.Element) -> Node:
    b = _BOUNDS.search(el.get("bounds", "[0,0][0,0]"))
    bounds = tuple(int(v) for v in b.groups()) if b else (0, 0, 0, 0)
    return Node(el.get("class", ""), el.get("resource-id", ""), el.get("text", ""),
                el.get("content-desc", ""), bounds, el)


class Screen:
    """Parsed hierarchy with parent links for scoping."""

    def __init__(self, xml: str):
        self.root = ET.fromstring(xml)
        self._parent: dict[ET.Element, ET.Element] = {}
        for p in self.root.iter():
            for c in p:
                self._parent[c] = p

    def all(self) -> list[Node]:
        return [_mk(el) for el in self.root.iter("node")]

    def find(self, *, rid: str | None = None, rid_suffix: str | None = None,
             text: str | None = None, cls: str | None = None,
             within_rid: str | None = None) -> list[Node]:
        out = []
        for el in self.root.iter("node"):
            if rid and el.get("resource-id") != rid:
                continue
            if rid_suffix and not el.get("resource-id", "").endswith(rid_suffix):
                continue
            if text and el.get("text") != text:
                continue
            if cls and cls not in el.get("class", ""):
                continue
            if within_rid and not self._has_ancestor_rid(el, within_rid):
                continue
            out.append(_mk(el))
        return out

    def first(self, **kw) -> Node | None:
        r = self.find(**kw)
        return r[0] if r else None

    def _has_ancestor_rid(self, el: ET.Element, rid: str) -> bool:
        cur = self._parent.get(el)
        while cur is not None:
            if cur.get("resource-id") == rid or cur.get("resource-id", "").endswith(rid):
                return True
            cur = self._parent.get(cur)
        return False

    def list_items(self, container_suffix: str) -> list[dict]:
        """Return each direct-child row of a container (e.g. recycler_view) as a dict
        of {leaf_resource_id: text_or_desc}, preserving row grouping."""
        containers = [el for el in self.root.iter("node")
                      if el.get("resource-id", "").endswith(container_suffix)]
        rows = []
        for c in containers:
            for row in list(c):  # direct children = recycler items
                fields: dict[str, str] = {}
                b = _BOUNDS.search(row.get("bounds", "[0,0][0,0]"))
                for d in row.iter("node"):
                    rid = d.get("resource-id", "").split("/")[-1]
                    val = d.get("text", "") or d.get("content-desc", "")
                    if rid and val and rid not in fields:
                        fields[rid] = val
                if fields:
                    if b:
                        g = tuple(int(v) for v in b.groups())
                        fields["_center"] = ((g[0] + g[2]) // 2, (g[1] + g[3]) // 2)
                    rows.append(fields)
        return rows
