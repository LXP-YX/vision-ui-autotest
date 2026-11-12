"""VLM 视觉能力（App 版）：视觉定位 + 视觉断言。

对标 core/vision.py，差异：
- 截图来源：driver.screenshot()（各平台驱动统一提供），非 Playwright；
- 坐标反查：不能 elementFromPoint，改在统一 UiNode 控件树里找包含坐标的
  最内层节点 → 生成稳定定位 {by, value}（resource_id / desc / text）；
- 大模型：完全复用 core.llm_client.get_client()，由平台「大模型配置」统一管理。
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Optional

from core.llm_client import LLMError, get_client
from core_app.drivers.base import DeviceDriver, UiNode, node_locator


def _screenshot_b64(driver: DeviceDriver) -> tuple[str, int, int]:
    """截图并按比例缩成 JPEG base64，返回 (base64, 原宽, 原高)。降 VLM token。"""
    png = driver.screenshot()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        if im.mode != "RGB":
            im = im.convert("RGB")  # PNG 常为 RGBA，JPEG 不支持 alpha
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


def _node_at_point(nodes: list[UiNode], px: int, py: int) -> Optional[UiNode]:
    """在 UiNode 树里找包含坐标 (px,py) 的最内层节点（面积最小者）。"""
    best: Optional[UiNode] = None
    best_area = None
    for n in nodes:
        if n.area <= 0 or not n.contains(px, py):
            continue
        if best_area is None or n.area < best_area:
            best_area = n.area
            best = n
    return best


def locate(driver: DeviceDriver, description: str,
           nodes: Optional[list[UiNode]] = None) -> Optional[dict]:
    """视觉定位：VLM 给归一化坐标 → 换算屏幕像素 → 反查 UiNode → 稳定定位。

    Args:
        driver: DeviceDriver
        description: 目标元素的自然语言描述
        nodes: 可选，外部已取的控件树（复用，省一次 ui_tree）
    Returns:
        {by, value, bounds:[x1,y1,x2,y2], center:[cx,cy]}；失败 None。
    """
    img_b64, w0, h0 = _screenshot_b64(driver)
    prompt = (
        f"这是一张手机界面截图。请定位元素：{description}。\n"
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

    if nodes is None:
        try:
            nodes = driver.ui_tree()
        except Exception:  # noqa: BLE001
            nodes = []
    node = _node_at_point(nodes, px, py) if nodes else None
    if not node:
        return None
    loc = node_locator(node)
    if not loc:
        return None
    x1, y1, x2, y2 = node.bounds
    return {
        "by": loc["by"],
        "value": loc["value"],
        "bounds": [x1, y1, x2, y2],
        "center": list(node.center),
    }


def assert_visual(driver: DeviceDriver, claim: str) -> dict:
    """视觉断言：VLM 判定当前界面是否满足描述。返回 {passed, reason}。"""
    img_b64, _, _ = _screenshot_b64(driver)
    prompt = (
        f"这是一张手机界面截图。请判断下面的断言是否成立：{claim}。\n"
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
