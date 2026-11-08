"""VLM 视觉能力（App 版）：视觉定位 + 视觉断言。

对标 core/vision.py，差异：
- 截图来源：adb screencap（PNG），非 Playwright page.screenshot；
- 坐标反查：不能 elementFromPoint，改解析 uiautomator dump 的 XML bounds，
  找包含坐标的最内层 <node> → 生成稳定定位 {by, value}（resource_id/text/content_desc）。
- 大模型：完全复用 core.llm_client.get_client()，由平台「大模型配置」统一管理。
"""

from __future__ import annotations

import base64
import io
import json
import re
import xml.etree.ElementTree as ET
from typing import Optional

from core.llm_client import LLMError, get_client


def _screenshot_b64(driver) -> tuple[str, int, int]:
    """adb 截图并按比例缩成 JPEG base64，返回 (base64, 原宽, 原高)。降 VLM token。"""
    png = driver.screenshot()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        if im.mode != "RGB":
            im = im.convert("RGB")  # screencap PNG 常为 RGBA，JPEG 不支持 alpha
        w0, h0 = im.size
        max_w = 900
        target_w = min(int(w0 * 0.4), max_w)
        ratio = target_w / w0
        im = im.resize((target_w, int(h0 * ratio)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode(), w0, h0
    except Exception:  # noqa: BLE001
        w, h = driver.screen_size()
        return base64.b64encode(png).decode(), w, h


def _parse_xy(text: str) -> Optional[tuple[float, float]]:
    """从模型回复解析归一化坐标 {x,y}（0~1）。"""
    m = re.search(r'\{[^{}]*"x"\s*:\s*([0-9.]+)[^{}]*"y"\s*:\s*([0-9.]+)[^{}]*\}', text)
    if not m:
        m = re.search(r'([0-9]*\.?[0-9]+)\s*[,，]\s*([0-9]*\.?[0-9]+)', text)
    if not m:
        return None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None


_BOUNDS_RE = re.compile(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')


def _node_at_point(xml: str, px: int, py: int) -> Optional[dict]:
    """在 UI Hierarchy XML 里找包含坐标 (px,py) 的最内层 <node>。

    遍历所有 node，bounds 形如 [x1,y1][x2,y2]，取包含该点且面积最小者（最内层叶子）。
    返回 {resource_id, text, content_desc, bounds:[x1,y1,x2,y2]}。
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    best: Optional[dict] = None
    best_area = None
    for node in root.iter("node"):
        m = _BOUNDS_RE.search(node.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if not (x1 <= px <= x2 and y1 <= py <= y2):
            continue
        area = (x2 - x1) * (y2 - y1)
        if best_area is None or area < best_area:
            best_area = area
            best = {
                "resource_id": node.get("resource-id", "") or "",
                "text": node.get("text", "") or "",
                "content_desc": node.get("content-desc", "") or "",
                "bounds": [x1, y1, x2, y2],
            }
    return best


def _stable_locator(node: dict) -> Optional[dict]:
    """把 VLM 命中的 node 转成稳定定位 {by, value}。优先 resource_id > content_desc > text。"""
    rid = node.get("resource_id", "")
    # resource-id 形如 com.foo:id/btn_login，取 id 段兜底全量
    if rid:
        return {"by": "resource_id", "value": rid}
    cd = node.get("content_desc", "")
    if cd:
        return {"by": "content_desc", "value": cd}
    txt = node.get("text", "")
    if txt:
        return {"by": "text", "value": txt}
    return None


def locate(driver, description: str, xml: Optional[str] = None) -> Optional[dict]:
    """视觉定位：VLM 给归一化坐标 → 换算设备像素 → 反查 XML node → 稳定定位。

    Args:
        driver: AppDriver
        description: 目标元素的自然语言描述
        xml: 可选，外部已 dump 的 UI XML（复用，省一次 dump）
    Returns:
        {by, value, bounds:[x1,y1,x2,y2], center:[cx,cy]}；失败 None。
    """
    img_b64, w0, h0 = _screenshot_b64(driver)
    prompt = (
        f"这是一张安卓手机界面截图。请定位元素：{description}。\n"
        "只返回该元素中心点的归一化坐标（相对整图，范围 0~1），"
        '格式严格为 JSON：{"x":0.xx,"y":0.xx}，不要其他文字。'
    )
    try:
        reply = get_client().ask_with_image(prompt, img_b64)
    except LLMError:
        return None

    xy = _parse_xy(reply)
    if not xy:
        return None
    x, y = xy
    if x > 1 or y > 1:
        x, y = x / w0, y / h0
    px, py = int(x * w0), int(y * h0)

    if xml is None:
        try:
            xml = driver.dump_ui()
        except Exception:  # noqa: BLE001
            xml = ""
    node = _node_at_point(xml, px, py) if xml else None
    if not node:
        return None
    loc = _stable_locator(node)
    if not loc:
        return None
    x1, y1, x2, y2 = node["bounds"]
    return {
        "by": loc["by"],
        "value": loc["value"],
        "bounds": [x1, y1, x2, y2],
        "center": [(x1 + x2) // 2, (y1 + y2) // 2],
    }


def assert_visual(driver, claim: str) -> dict:
    """视觉断言：VLM 判定当前界面是否满足描述。返回 {passed, reason}。"""
    img_b64, _, _ = _screenshot_b64(driver)
    prompt = (
        f"这是一张安卓手机界面截图。请判断下面的断言是否成立：{claim}。\n"
        '严格返回 JSON：{"passed":true/false,"reason":"简短理由"}，不要其他文字。'
    )
    try:
        reply = get_client().ask_with_image(prompt, img_b64)
    except LLMError as e:
        return {"passed": False, "reason": f"VLM 调用失败：{e}"}

    m = re.search(r'\{.*\}', reply, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            return {"passed": bool(d.get("passed")), "reason": str(d.get("reason", ""))[:200]}
        except json.JSONDecodeError:
            pass
    passed = ("true" in reply.lower()) or ("成立" in reply) or ("通过" in reply)
    return {"passed": passed, "reason": reply[:200]}
