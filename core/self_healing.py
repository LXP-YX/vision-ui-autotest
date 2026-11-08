"""脚本自愈：三级定位 + 知识库回写（方案核心）。

find_element(page, anchor)：
  ① Locator Store 命中稳定选择器 → 直接用（零成本、最快）；
  ② Playwright 语义定位（:text-is / role / label）；
  ③ VLM 视觉定位（截图→坐标→反查 DOM→生成稳定选择器）。
成功后把（新）选择器回写知识库，命中/失败计数累加 —— 越跑越稳。

自愈 ≠ 掩盖 Bug：仅"选择器漂移（元素仍在、原选择器失配）"才自愈；
元素确实不存在时返回 None，由调用方判为功能缺陷。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from playwright.sync_api import Locator, Page

from core import vision

# 让平台 DB 可被 import（知识库落库）；不可用时自愈退化为"不落库"
_BACKEND = Path(__file__).resolve().parent.parent / "platform" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from db import Locator as LocatorRow, SessionLocal  # type: ignore
    _DB_OK = True
except Exception:  # noqa: BLE001
    _DB_OK = False


def _store_get(anchor: str) -> Optional[str]:
    if not _DB_OK:
        return None
    with SessionLocal() as s:
        row = s.query(LocatorRow).filter(LocatorRow.anchor == anchor).first()
        return row.selector if row and row.selector else None


def _store_save(anchor: str, selector: str, strategy: str, hit: bool) -> None:
    if not _DB_OK:
        return
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


def _try_selector(page: Page, selector: str, timeout: int = 3000) -> Optional[Locator]:
    """尝试用选择器定位可见元素，成功返回 locator。"""
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        return loc
    except Exception:  # noqa: BLE001
        return None


def _hint_to_selector(hint: dict) -> str | None:
    """把 locator_hint {type, value} 转为 Playwright 选择器字符串。"""
    t = (hint.get("type") or "").strip().lower()
    v = (hint.get("value") or "").strip()
    if not v:
        return None
    mapping = {
        "xpath":       f"xpath={v}",
        "css":         v,
        "text":        f':text-is("{v}")',
        "hastext":     f':has-text("{v}")',
        "role":        f"role={v}",
        "label":       f"label={v}",
        "placeholder": f'[placeholder="{v}"]',
        "testid":      f'[data-testid="{v}"]',
        "id":          f'#{v}' if not v.startswith("#") else v,
        "name":        f'[name="{v}"]',
    }
    return mapping.get(t, v)  # 未知类型直接当 CSS 用


def find_element(page: Page, anchor: str,
                 semantic_hint: Optional[str] = None,
                 locator_hint: Optional[dict] = None) -> Optional[dict]:
    """三级定位 + 自愈回写。

    Args:
        anchor:        目标元素的自然语言描述（知识库主键）。
        semantic_hint: 可选，语义定位用的文本（默认取 anchor）。
        locator_hint:  可选，用户手动指定的精确定位 {type, value}，优先级最高。
    Returns:
        {selector, strategy} 命中信息；彻底找不到返回 None。
    """
    # 0️⃣ 用户手动指定的定位提示：优先级高于一切，成功即返回，失败直接报错不兜底
    # 理由：用户已明确指定定位方式，若失效说明页面真实变化，应立即暴露而非掩盖
    if locator_hint:
        sel = _hint_to_selector(locator_hint)
        if sel and _try_selector(page, sel):
            return {"selector": sel, "strategy": "hint"}
        # hint 提供但定位失败：不回退三级定位，直接返回 None 暴露问题
        return None

    hint = semantic_hint or anchor

    # ① Store 命中
    cached = _store_get(anchor)
    if cached and _try_selector(page, cached):
        _store_save(anchor, cached, "store", hit=True)
        return {"selector": cached, "strategy": "store"}

    # ② 语义定位：精确 → 去后缀精确 → 包含匹配（均限可见节点）
    core = _strip_suffix(hint)
    for sel in (
        f":text-is(\"{hint}\"):visible",
        f":text-is(\"{core}\"):visible",
        f":visible:has-text(\"{core}\")",
    ):
        if _try_selector(page, sel):
            _store_save(anchor, sel, "semantic", hit=True)
            return {"selector": sel, "strategy": "semantic"}

    # ③ VLM 视觉定位 → 生成稳定选择器 → 回验命中元素是否与目标相关 → 回写
    healed = vision.locate(page, anchor)
    if healed and _try_selector(page, healed) and _verify_hit(page, healed, core):
        _store_save(anchor, healed, "vlm", hit=True)
        return {"selector": healed, "strategy": "vlm"}

    # 三级都失败：元素可能确实不存在 —— 判为功能缺陷，不伪造
    if cached:
        _store_save(anchor, cached, "store", hit=False)
    return None


# 语义定位时剥离的通用后缀词（"模块A 菜单" → "模块A"）
_SUFFIX = ("菜单", "按钮", "选项", "标签", "链接", "入口", "图标", "项")


def _strip_suffix(text: str) -> str:
    """去掉末尾的通用控件后缀词与空白，得到核心文本。"""
    t = (text or "").strip()
    for suf in _SUFFIX:
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
    return t or (text or "").strip()


def _verify_hit(page: Page, selector: str, core: str) -> bool:
    """回验：VLM 生成的选择器命中的元素文本是否与目标核心词相关。

    防止 VLM 坐标偏差定位到错误元素（如把"模块A"定到"模块B"）。
    core 为空时不校验（放行）。
    """
    if not core:
        return True
    try:
        txt = (page.locator(selector).first.inner_text(timeout=2000) or "").strip()
    except Exception:  # noqa: BLE001
        return False
    # 命中元素文本包含核心词，或核心词包含命中文本（短词），视为相关
    return core in txt or (len(txt) <= 8 and txt in core)
