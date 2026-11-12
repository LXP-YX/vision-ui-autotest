"""iOS XCUITest page_source XML → 统一 UiNode 模型（Appium 归一化解析器）。

Appium XCUITest 驱动的 page_source 为 XML 树（XCUIElementType* 节点），
坐标为逻辑点（point）。rect 属性各版本格式不一：
  - 直接属性 x / y / width / height
  - 或 rect="{{x, y}, {width, height}}"
本解析器两者都兼容，输出的点坐标由 IosDriver 统一换算为屏幕像素。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from core_app.drivers.base import UiNode

_RECT_RE = re.compile(r'\{\{\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)\s*\}\s*,\s*\{(\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)\s*\}\}')


def _element_rect(el) -> tuple[float, float, float, float] | None:
    """读取元素坐标（点）：优先 x/y/width/height 属性，回退 rect="{{x,y},{w,h}}"。"""
    def _num(v: str | None) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    x, y, w, h = _num(el.get("x")), _num(el.get("y")), _num(el.get("width")), _num(el.get("height"))
    if None not in (x, y, w, h):
        return (x, y, w, h)
    m = _RECT_RE.search(el.get("rect", "") or "")
    if m:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)))
    return None


def parse(xml: str) -> list[UiNode]:
    """把 XCUITest page_source XML 归一化为 UiNode 列表（点坐标，未换算像素）。

    text ← label（回退 value）；desc ← name（无障碍标识）。
    跳过 visible=false 与零面积节点；解析失败返回空列表。
    """
    try:
        root = ET.fromstring(xml or "")
    except ET.ParseError:
        return []
    nodes: list[UiNode] = []
    for el in root.iter():
        if el.get("visible") == "false":
            continue
        rect = _element_rect(el)
        if not rect or rect[2] <= 0 or rect[3] <= 0:
            continue
        x, y, w, h = rect
        nodes.append(UiNode(
            text=(el.get("label") or el.get("value") or ""),
            desc=el.get("name") or "",
            res_id=el.get("name") or "",  # iOS 的 accessibility identifier 走 name 属性
            cls=el.get("type") or el.tag,
            bounds=(int(x), int(y), int(x + w), int(y + h)),
            enabled=(el.get("enabled") != "false"),
        ))
    return nodes
