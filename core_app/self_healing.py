"""脚本自愈（App 版）：三层定位 + 知识库回写。

对标 core/self_healing.py，定位手段替换为「统一控件树 UiNode」：
  ① Store 命中稳定定位 {by, value} → 在当前 UiNode 树校验仍存在 → 用（零成本、最快）；
  ② 语义定位：UiNode 按 text / desc / resource-id 匹配（各平台归一化后共用同一逻辑）；
  ③ VLM 视觉定位：截图→坐标→反查 UiNode→生成稳定定位 {by, value}。
成功后把 {by, value} 回写知识库，命中/失败计数累加 —— 越跑越稳。

自愈 ≠ 掩盖 Bug：仅「定位漂移（元素仍在、原定位失配）」才自愈；
元素确实不存在时返回 None，由调用方判为功能缺陷。

知识库：APP 专属 app_locator 表（与 WEB 的 locator 表级拆分，互不混存），
selector 存 JSON {"by":..,"value":..}；行维度 platform（android|ios）+
env_id（app_env.id），查询按 环境精确 → 平台精确 → 全局 优先级回退。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from core_app.drivers.base import DeviceDriver, UiNode, node_by_value, node_locator
from core_app import vision

# 让平台 DB 可被 import（知识库落库）；不可用时自愈退化为「不落库」
_BACKEND = Path(__file__).resolve().parent.parent / "platform" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from db import AppLocator as AppLocatorRow, SessionLocal  # type: ignore
    _DB_OK = True
except Exception:  # noqa: BLE001
    _DB_OK = False

# 语义定位时剥离的通用后缀词（「登录按钮」→「登录」）
_SUFFIX = ("按钮", "选项", "图标", "输入框", "链接", "入口", "项", "菜单", "标签", "文本框")


def _strip_suffix(text: str) -> str:
    t = (text or "").strip()
    for suf in _SUFFIX:
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
    return t or (text or "").strip()


# ------------------------- 知识库读写（app_locator 表，按平台/环境隔离） -------------------------

def _pick_row(rows: list, env_id: Optional[int], platform: Optional[str]):
    """按优先级挑行：环境+平台精确 > 环境精确 > 平台精确 > 全局。"""
    conds = (
        lambda r: r.env_id == env_id and r.platform == platform,
        lambda r: r.env_id == env_id and r.platform is None,
        lambda r: r.env_id is None and r.platform == platform,
        lambda r: r.env_id is None and r.platform is None,
    )
    for cond in conds:
        for r in rows:
            if cond(r):
                return r
    return None


def _store_get(anchor: str, env_id: Optional[int],
               platform: Optional[str]) -> Optional[dict]:
    """读知识库：anchor → {by, value}。selector 字段存 JSON。"""
    if not _DB_OK:
        return None
    with SessionLocal() as s:
        rows = s.query(AppLocatorRow).filter(AppLocatorRow.anchor == anchor).all()
        row = _pick_row(rows, env_id, platform)
        if not row or not row.selector:
            return None
        try:
            d = json.loads(row.selector)
            if d.get("by") and d.get("value"):
                return d
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _store_save(anchor: str, locator: dict, strategy: str, hit: bool,
                env_id: Optional[int], platform: Optional[str]) -> None:
    if not _DB_OK:
        return
    selector = json.dumps(locator, ensure_ascii=False)
    with SessionLocal() as s:
        rows = s.query(AppLocatorRow).filter(AppLocatorRow.anchor == anchor).all()
        row = _pick_row(rows, env_id, platform)
        if not row:
            row = AppLocatorRow(anchor=anchor, selector=selector, strategy=strategy,
                                env_id=env_id, platform=platform)
            s.add(row)
        else:
            row.selector = selector
            row.strategy = strategy
        if hit:
            row.hit_count = (row.hit_count or 0) + 1
        else:
            row.fail_count = (row.fail_count or 0) + 1
        s.commit()


# ------------------------- UiNode 查找 -------------------------

def _find_by_locator(nodes: list[UiNode], locator: dict) -> Optional[dict]:
    """在 UiNode 树里按 {by, value} 精确查找，返回 {by, value, bounds, center}。"""
    by, val = locator.get("by"), locator.get("value", "")
    if not by or not val:
        return None
    for n in nodes:
        if n.area <= 0:
            continue
        if node_by_value(n, by, val):
            cx, cy = n.center
            return {"by": by, "value": val,
                    "bounds": list(n.bounds), "center": [cx, cy]}
    return None


def _find_semantic(nodes: list[UiNode], anchor: str) -> Optional[dict]:
    """UiNode 语义匹配：按 anchor 在 text/desc/resource_id 查找最匹配节点。

    优先级：text 精确 > desc 精确 > text 包含 > desc 包含 > resource_id 包含（英文 id 兜底）。
    """
    core = _strip_suffix(anchor)
    if not core:
        return None

    def _hit(n: UiNode, by: str, value: str) -> Optional[dict]:
        if n.area <= 0:
            return None
        cx, cy = n.center
        return {"by": by, "value": value, "bounds": list(n.bounds), "center": [cx, cy]}

    # 精确匹配：text → desc
    for n in nodes:
        if n.text and n.text == core:
            r = _hit(n, "text", n.text)
            if r:
                return r
    for n in nodes:
        if n.desc and n.desc == core:
            r = _hit(n, "desc", n.desc)
            if r:
                return r
    # 包含匹配：text → desc
    for n in nodes:
        if n.text and core in n.text:
            r = _hit(n, "text", n.text)
            if r:
                return r
    for n in nodes:
        if n.desc and core in n.desc:
            r = _hit(n, "desc", n.desc)
            if r:
                return r
    # resource-id 兜底：英文 id 含 core（小写）
    for n in nodes:
        rid = (n.res_id or "").lower()
        if rid and core.lower() in rid:
            r = _hit(n, "resource_id", n.res_id)
            if r:
                return r
    return None


# ------------------------- 对外入口 -------------------------

def find_element(driver: DeviceDriver, anchor: str,
                 locator_hint: Optional[dict] = None,
                 env_id: Optional[int] = None) -> Optional[dict]:
    """三级定位 + 自愈回写。

    Args:
        driver:       DeviceDriver（platform 自动作为知识库行维度）
        anchor:       目标元素的自然语言描述（知识库主键）
        locator_hint: 可选，手动指定 {by, value}，优先级最高；失败直接返回 None
        env_id:       APP 测试环境 id（app_env.id），知识库按环境隔离
    Returns:
        {by, value, bounds, center, strategy}；彻底找不到返回 None。
    """
    platform = driver.platform
    # dump 一次控件树供 ①校验 / ②语义 / ③VLM 反查共用
    try:
        nodes = driver.ui_tree()
    except Exception:  # noqa: BLE001
        nodes = []

    # 0️⃣ 手动定位提示：优先级最高，失效直接暴露
    if locator_hint and locator_hint.get("by") and locator_hint.get("value"):
        hit = _find_by_locator(nodes, locator_hint) if nodes else None
        if hit:
            hit["strategy"] = "hint"
            return hit
        return None

    # ① Store 命中
    cached = _store_get(anchor, env_id, platform)
    if cached and nodes:
        hit = _find_by_locator(nodes, cached)
        if hit:
            _store_save(anchor, cached, "store", hit=True, env_id=env_id, platform=platform)
            hit["strategy"] = "store"
            return hit

    # ② 语义定位
    if nodes:
        hit = _find_semantic(nodes, anchor)
        if hit:
            loc = {"by": hit["by"], "value": hit["value"]}
            _store_save(anchor, loc, "xml", hit=True, env_id=env_id, platform=platform)
            hit["strategy"] = "xml"
            return hit

    # ③ VLM 视觉定位 → 反查 UiNode → 回写
    try:
        v = vision.locate(driver, anchor, nodes=nodes)
    except Exception:  # noqa: BLE001
        v = None
    if v:
        loc = {"by": v["by"], "value": v["value"]}
        # 回验：用生成的 locator 再查一次控件树，确认可复现
        if not nodes or _find_by_locator(nodes, loc):
            _store_save(anchor, loc, "vlm", hit=True, env_id=env_id, platform=platform)
            return {
                "by": v["by"], "value": v["value"],
                "bounds": v["bounds"], "center": v["center"],
                "strategy": "vlm",
            }

    # 三级都失败：判为功能缺陷，不伪造
    if cached:
        _store_save(anchor, cached, "store", hit=False, env_id=env_id, platform=platform)
    return None
