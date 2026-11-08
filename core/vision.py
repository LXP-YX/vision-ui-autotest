"""VLM 视觉能力：视觉定位 + 视觉断言（方案核心支柱）。

- 视觉定位：截图 + 自然语言描述 → VLM 返回归一化坐标 → elementFromPoint 反查
  真实 DOM 节点 → 生成稳定选择器（优先 id / data-* / 文本，避开 hash 类名）。
  只用于"找到节点"，成功后沉淀为稳定选择器，不长期靠坐标点击。
- 视觉断言：截图 + 断言描述 → VLM 判定 pass/fail + 理由。

均按需触发（DOM/语义手段失败时），以控成本。
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Optional

from playwright.sync_api import Page

from core.llm_client import LLMError, get_client


def _screenshot_b64(page: Page, scale: float = 0.4) -> tuple[str, int, int]:
    """视口截图并按比例缩小 + 限制最大宽，返回 (base64, 原宽, 原高)。降低 VLM token/超时。"""
    png = page.screenshot(full_page=False)
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        w0, h0 = im.size
        # 目标宽度上限，过大图按比例缩到不超过 max_w
        max_w = 900
        target_w = min(int(w0 * scale), max_w)
        ratio = target_w / w0
        im = im.resize((target_w, int(h0 * ratio)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70)  # JPEG 比 PNG 小很多
        return base64.b64encode(buf.getvalue()).decode(), w0, h0
    except Exception:
        vp = page.viewport_size or {"width": 1440, "height": 900}
        return base64.b64encode(png).decode(), vp["width"], vp["height"]


def _parse_xy(text: str) -> Optional[tuple[float, float]]:
    """从模型回复解析归一化坐标 {x,y}（0~1）。"""
    m = re.search(r'\{[^{}]*"x"\s*:\s*([0-9.]+)[^{}]*"y"\s*:\s*([0-9.]+)[^{}]*\}', text)
    if not m:
        m = re.search(r'([0-9]*\.?[0-9]+)\s*[,，]\s*([0-9]*\.?[0-9]+)', text)
    if not m:
        return None
    try:
        x, y = float(m.group(1)), float(m.group(2))
        # 若像素坐标（>1），后续按图像尺寸归一，这里只保证拿到数
        return x, y
    except ValueError:
        return None


def locate(page: Page, description: str) -> Optional[str]:
    """视觉定位：返回为目标元素生成的稳定选择器；失败返回 None。

    流程：截图 → VLM 给归一化坐标 → 换算像素 → elementFromPoint 反查 →
    生成稳定选择器。
    """
    img_b64, w0, h0 = _screenshot_b64(page)
    prompt = (
        f"这是一张网页截图。请定位元素：{description}。\n"
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
    # 归一化到像素（若模型返回像素值 >1，则按图像宽高归一后再乘视口）
    if x > 1 or y > 1:
        x, y = x / w0, y / h0
    vp = page.viewport_size or {"width": w0, "height": h0}
    px, py = int(x * vp["width"]), int(y * vp["height"])

    selector = page.evaluate(_GEN_SELECTOR_JS, {"x": px, "y": py})
    return selector or None


# 反查 DOM 并生成稳定选择器：优先 id / data-testid / 文本，避开 hash 类名
_GEN_SELECTOR_JS = """
({x, y}) => {
    const el = document.elementFromPoint(x, y);
    if (!el) return null;
    // 1) id
    if (el.id && !/^[0-9]/.test(el.id)) return '#' + CSS.escape(el.id);
    // 2) data-* 稳定属性
    for (const attr of ['data-testid','data-test','data-id','name','aria-label']) {
        const v = el.getAttribute && el.getAttribute(attr);
        if (v) return `[${attr}="${v}"]`;
    }
    // 3) 精确文本（短文本节点）——加 :visible 命中可见节点，避开隐藏同名 span
    const txt = (el.textContent || '').trim();
    if (txt && txt.length <= 20 && !txt.includes('\\n')) {
        return `:text-is("${txt.replace(/"/g,'\\\\"')}"):visible`;
    }
    // 4) 角色 + 标签兜底
    const role = el.getAttribute && el.getAttribute('role');
    if (role) return `[role="${role}"]`;
    return el.tagName ? el.tagName.toLowerCase() : null;
}
"""


def assert_visual(page: Page, description: str) -> dict:
    """视觉断言：VLM 判定当前页面是否满足描述。返回 {passed, reason}。"""
    img_b64, _, _ = _screenshot_b64(page)
    prompt = (
        f"这是一张网页截图。请判断下面的断言是否成立：{description}。\n"
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
    # 解析失败：按文本兜底判断
    passed = ("true" in reply.lower()) or ("成立" in reply) or ("通过" in reply)
    return {"passed": passed, "reason": reply[:200]}
