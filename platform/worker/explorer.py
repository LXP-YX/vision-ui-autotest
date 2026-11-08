"""页面探索式用例生成（方案 P3.6 方式 B）。

流程：登录 → 导航到目标页 → 截图 → VLM"看懂"页面上有哪些可读控件/数据
→ 结合页面描述让对话模型生成贴合真实页面的用例草稿。

与方式 A（纯需求描述）的区别：方式 B 基于**真实页面**，生成的用例更贴合实际控件，
而非凭空推测。仍守只读原则、产出草稿。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_BK = ROOT / "platform" / "backend"
if str(_BK) not in sys.path:
    sys.path.insert(0, str(_BK))

from core import browser as browser_mod
from core import config, vision
from core.llm_client import LLMError, get_client
from core.login_page import LoginPage
from core.main_page import MainPage, ready_menu_selector


def _describe_page(page) -> str:
    """用 VLM 描述当前页面上有哪些可读控件与数据（供生成用例参考）。"""
    b64, _, _ = vision._screenshot_b64(page)
    prompt = (
        "这是一张网页管理后台的截图。请简要列出页面上可见的：\n"
        "1) 主要功能区/控件（如搜索框、筛选、标签页、按钮、表格）；\n"
        "2) 表格主要列名或展示的数据类型。\n"
        "用简洁中文分点描述，不要臆造不存在的元素。"
    )
    try:
        return get_client().ask_with_image(prompt, b64)
    except LLMError as e:
        return f"（页面视觉描述失败：{e}）"


_GEN_TOOL = [{
    "type": "function",
    "function": {
        "name": "emit_cases",
        "description": "产出一批贴合该页面的只读测试用例草稿",
        "parameters": {
            "type": "object",
            "properties": {
                "cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "steps": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "steps"],
                    },
                },
            },
            "required": ["cases"],
        },
    },
}]

# 破坏性关键词（与 generator 一致，双保险剔除）
_RISKY = ("新增", "新建", "创建", "添加", "删除", "移除", "编辑", "修改",
          "提交", "保存", "确定", "导入", "上传")


def explore_and_generate(top: str, sub: str, count: int = 3) -> list[dict]:
    """探索 top→sub 页面并生成用例草稿。

    Returns:
        [{name, module, tags, steps}]
    """
    import os
    os.environ["PW_HEADLESS"] = "1"

    with browser_mod.launch_context() as (_b, ctx):
        page = ctx.new_page()
        login = LoginPage(page)
        login.open()
        creds = config.get("account")
        login.login(creds["username"], creds["password"])
        page.goto(config.get("base_url").rstrip("/"), wait_until="domcontentloaded")
        MainPage(page).wait_visible(ready_menu_selector(), timeout=15_000)

        main = MainPage(page)
        main.nav_to(top, sub)
        page_desc = _describe_page(page)

    system = (
        "你是 UI 自动化测试用例设计师。基于给定页面的真实内容，设计只读测试用例。\n"
        "步骤只能用以下动作，用中文表达：\n"
        "『登录系统』；『进入 X → Y』（一级→二级菜单）；『点击 X』；\n"
        "『确认 X』（视觉断言页面应出现的内容）；『确认页面正常无报错』。\n"
        "严格只读：绝不生成新增/删除/提交/保存/上传等破坏性操作。\n"
        f"用例前两步固定为：『登录系统』『进入 {top} → {sub}』。"
    )
    user = (
        f"目标页面：{top} → {sub}。该页面的真实内容如下：\n{page_desc}\n\n"
        f"请据此设计约 {count} 条只读测试用例，调用 emit_cases 返回。"
    )
    msg = get_client().chat(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        tools=_GEN_TOOL,
    )
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        raise LLMError("模型未返回结构化用例（无 tool_calls）")
    try:
        args = json.loads(tool_calls[0]["function"]["arguments"] or "{}")
    except (json.JSONDecodeError, KeyError, TypeError):
        raise LLMError("生成结果解析失败")

    out = []
    for c in args.get("cases") or []:
        steps = [s.strip() for s in (c.get("steps") or []) if s.strip()]
        steps = [s for s in steps if not any(k in s for k in _RISKY)]
        if not steps:
            continue
        out.append({
            "name": c.get("name") or f"{sub}页面用例",
            "module": top,
            "tags": ["AI生成", "页面探索"],
            "steps": steps,
        })
    return out
