"""Android UI Hierarchy XML → 统一 UiNode 模型（uiautomator dump 的归一化解析器）。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from core_app.drivers.base import UiNode

_BOUNDS_RE = re.compile(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')


def parse_bounds(b: str) -> tuple[int, int, int, int] | None:
    """解析 bounds 属性 "[x1,y1][x2,y2]" → (x1, y1, x2, y2)；格式非法返回 None。"""
    m = _BOUNDS_RE.search(b or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1, y1, x2, y2)


def parse(xml: str) -> list[UiNode]:
    """把 uiautomator dump 的 XML 归一化为 UiNode 列表。

    跳过零面积节点（不可见/折叠控件）；解析失败返回空列表（不抛错，
    语义定位与 VLM 反查层负责给出「未找到」结论）。
    """
    try:
        root = ET.fromstring(xml or "")
    except ET.ParseError:
        return []
    nodes: list[UiNode] = []
    for el in root.iter("node"):
        b = parse_bounds(el.get("bounds", ""))
        if not b or (b[2] - b[0]) <= 0 or (b[3] - b[1]) <= 0:
            continue
        nodes.append(UiNode(
            text=el.get("text", "") or "",
            desc=el.get("content-desc", "") or "",
            res_id=el.get("resource-id", "") or "",
            cls=el.get("class", "") or "",
            bounds=b,
            clickable=(el.get("clickable", "") == "true"),
            enabled=(el.get("enabled", "true") != "false"),
        ))
    return nodes
