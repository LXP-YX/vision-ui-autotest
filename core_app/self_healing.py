"""脚本自愈（App 版）：三层定位 + 知识库回写。

对标 core/self_healing.py，差异（定位手段替换为 ADB+UIAutomator XML）：
  ① Locator Store 命中稳定定位 {by, value} → 在当前 XML 校验仍存在 → 用（零成本、最快）；
  ② XML 语义定位：uiautomator dump 的 XML 按 resource-id / text / content-desc 匹配；
  ③ VLM 视觉定位：截图→坐标→反查 XML node→生成稳定定位 {by, value}。
成功后把 {by, value} 回写知识库，命中/失败计数累加 —— 越跑越稳。

自愈 ≠ 掩盖 Bug：仅「定位漂移（元素仍在、原定位失配）」才自愈；
元素确实不存在时返回 None，由调用方判为功能缺陷。

知识库：复用 platform/backend 的 locator 表（与 Web 端共用），selector 字段存
JSON 字符串 {"by":..,"value":..}，strategy 标记来源。方案原文写 locator_table.json，
这里统一落 DB（跨 run 共享、前端自愈知识库页可统一查看），不再维护独立 json 文件。
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# 让平台 DB 可被 import（知识库落库）；不可用时自愈退化为「不落库」
_BACKEND = Path(__file__).resolve().parent.parent / "platform" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from db import Locator as LocatorRow, SessionLocal  # type: ignore
    _DB_OK = True
except Exception:  # noqa: BLE001
    _DB_OK = False

from core_app import vision

# 语义定位时剥离的通用后缀词（「登录按钮」→「登录」）
_SUFFIX = ("按钮", "选项", "图标", "输入框", "链接", "入口", "项", "菜单", "标签", "文本框")


def _strip_suffix(text: str) -> str:
    t = (text or "").strip()
    for suf in _SUFFIX:
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
    return t or (text or "").strip()


# ------------------------- 知识库读写（locator 表） -------------------------

def _store_get(anchor: str) -> Optional[dict]:
    """读知识库：anchor → {by, value}。selector 字段存 JSON。"""
    if not _DB_OK:
        return None
    with SessionLocal() as s:
        row = s.query(LocatorRow).filter(LocatorRow.anchor == anchor).first()
        if not row or not row.selector:
            return None
        try:
            d = json.loads(row.selector)
            if d.get("by") and d.get("value"):
                return d
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _store_save(anchor: str, locator: dict, strategy: str, hit: bool) -> None:
    if not _DB_OK:
        return
    selector = json.dumps(locator, ensure_ascii=False)
    with SessionLocal() as s:
        row = s.query(LocatorRow).filter(LocatorRow.anchor == anchor).first()
        if not row:
            row = LocatorRow(anchor=anchor, selector=selector, strategy=strategy)
            s.add(row)
        else:
            row.selector = selector
            row.strategy = strategy
        if hit:
            row.hit_count = (row.hit_count or 0) + 1
        else:
            row.fail_count = (row.fail_count or 0) + 1
        s.commit()


# ------------------------- XML 节点查找 -------------------------

import re as _re
_BOUNDS_RE = _re.compile(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')


def _parse_bounds(b: str) -> Optional[list[int]]:
    m = _BOUNDS_RE.search(b or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return [x1, y1, x2, y2]


def _center(bounds: list[int]) -> list[int]:
    x1, y1, x2, y2 = bounds
    return [(x1 + x2) // 2, (y1 + y2) // 2]


def _iter_nodes(xml: str):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return
    yield from root.iter("node")


def _find_by_locator(xml: str, locator: dict) -> Optional[dict]:
    """在 XML 里按 {by, value} 精确查找可见节点，返回 {by, value, bounds, center}。"""
    by = locator.get("by")
    val = locator.get("value", "")
    if not by or not val:
        return None
    attr = {"resource_id": "resource-id", "text": "text",
            "content_desc": "content-desc"}.get(by)
    if not attr:
        return None
    for node in _iter_nodes(xml):
        if (node.get(attr, "") or "") == val:
            b = _parse_bounds(node.get("bounds", ""))
            if not b:
                continue
            # 过滤零面积/不可见节点
            if b[2] - b[0] <= 0 or b[3] - b[1] <= 0:
                continue
            return {"by": by, "value": val, "bounds": b, "center": _center(b)}
    return None


def _find_semantic(xml: str, anchor: str) -> Optional[dict]:
    """XML 语义匹配：按 anchor 在 text/content-desc/resource-id 查找最匹配节点。

    优先级：text 精确 > content-desc 精确 > text 包含 > content-desc 包含
    > resource-id 包含（英文 id 兜底，一般不命中）。
    """
    core = _strip_suffix(anchor)
    if not core:
        return None

    def _hit(node, attr):
        b = _parse_bounds(node.get("bounds", ""))
        if not b or b[2] - b[0] <= 0 or b[3] - b[1] <= 0:
            return None
        return {"by": attr_map[attr], "value": node.get(attr, "") or "",
                "bounds": b, "center": _center(b)}

    attr_map = {"text": "text", "content-desc": "content_desc", "resource-id": "resource_id"}
    # 按优先级多趟扫描
    for attr, _by in [("text", "text"), ("content-desc", "content_desc")]:
        for node in _iter_nodes(xml):
            if (node.get(attr, "") or "") == core:
                r = _hit(node, attr)
                if r:
                    return r
    for attr, _by in [("text", "text"), ("content-desc", "content_desc")]:
        for node in _iter_nodes(xml):
            v = node.get(attr, "") or ""
            if v and core in v:
                r = _hit(node, attr)
                if r:
                    return r
    # resource-id 兜底：英文 id 含 core（小写）
    for node in _iter_nodes(xml):
        rid = (node.get("resource-id", "") or "").lower()
        if rid and core.lower() in rid:
            r = _hit(node, "resource-id")
            if r:
                return r
    return None


# ------------------------- 对外入口 -------------------------

def find_element(driver, anchor: str, locator_hint: Optional[dict] = None,
                 xml: Optional[str] = None) -> Optional[dict]:
    """三级定位 + 自愈回写。

    Args:
        driver:        AppDriver
        anchor:        目标元素的自然语言描述（知识库主键）
        locator_hint:  可选，手动指定 {by, value}，优先级最高；失败直接返回 None
        xml:           可选，外部已 dump 的 UI XML（复用省 dump）
    Returns:
        {by, value, bounds, center, strategy}；彻底找不到返回 None。
    """
    # dump 一次 XML 供 ①校验 / ②语义 / ③VLM 反查共用
    if xml is None:
        try:
            xml = driver.dump_ui()
        except Exception:  # noqa: BLE001
            xml = ""

    # 0️⃣ 手动定位提示：优先级最高，失效直接暴露
    if locator_hint and locator_hint.get("by") and locator_hint.get("value"):
        hit = _find_by_locator(xml, locator_hint) if xml else None
        if hit:
            hit["strategy"] = "hint"
            return hit
        return None

    # ① Store 命中
    cached = _store_get(anchor)
    if cached and xml:
        hit = _find_by_locator(xml, cached)
        if hit:
            _store_save(anchor, cached, "store", hit=True)
            hit["strategy"] = "store"
            return hit

    # ② XML 语义定位
    if xml:
        hit = _find_semantic(xml, anchor)
        if hit:
            loc = {"by": hit["by"], "value": hit["value"]}
            _store_save(anchor, loc, "xml", hit=True)
            hit["strategy"] = "xml"
            return hit

    # ③ VLM 视觉定位 → 反查 XML → 回写
    try:
        v = vision.locate(driver, anchor, xml=xml)
    except Exception:  # noqa: BLE001
        v = None
    if v:
        loc = {"by": v["by"], "value": v["value"]}
        # 回验：用生成的 locator 再查一次 XML，确认可复现
        if not xml or _find_by_locator(xml, loc):
            _store_save(anchor, loc, "vlm", hit=True)
            return {
                "by": v["by"], "value": v["value"],
                "bounds": v["bounds"], "center": v["center"],
                "strategy": "vlm",
            }

    # 三级都失败：判为功能缺陷，不伪造
    if cached:
        _store_save(anchor, cached, "store", hit=False)
    return None
