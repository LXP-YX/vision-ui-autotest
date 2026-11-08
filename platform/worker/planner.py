"""AI Planner：用大模型把一条中文步骤解析为结构化动作。

设计（方案 P4）：
- LLM 只负责"理解意图 → 结构化动作"，不负责实际操作；
- 实际操作仍由 core/ 执行器完成（login / nav_to / health_check）；
- 通过 function call 强制结构化输出，避免解析自由文本；
- LLM 不可用 / 解析失败时，调用方回退到规则式解释器。

支持的动作（工具）：
- login()                     打开已登录首页并确认就绪
- navigate(top, sub)          顶部→侧边导航
- check_health()              UI 健康检查（无报错/破图/白屏）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 让 core 可被 import
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm_client import LLMError, get_client

# function call 工具定义：把中文步骤映射到这三类动作之一
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "login",
            "description": "打开被测系统的已登录首页并确认加载就绪。当步骤表达'登录/进入系统/打开首页'时使用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "从顶部一级菜单导航到侧边二级菜单。当步骤表达'进入 X → Y / 打开 X 下的 Y'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "top": {"type": "string", "description": "一级菜单名"},
                    "sub": {"type": "string", "description": "二级菜单名"},
                },
                "required": ["top", "sub"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_health",
            "description": (
                "对当前页面做 UI 健康检查（错误提示/破图/白屏）——纯 DOM 检查，不看画面内容，零成本。"
                "当步骤只是笼统地表达'确认无报错/页面正常/加载成功/页面正常显示/无异常/健康检查'，"
                "并未要求校验某个具体内容时，优先用本工具，不要用 assert_visual。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "点击页面上某个具体元素（按钮/链接/图标/行内操作等）。当步骤表达'点击 X / 打开 X 详情 / 展开 X'且 X 不是二级菜单导航时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "目标元素的自然语言描述，如 『搜索按钮』『第一行的查看详情』",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assert_visual",
            "description": (
                "对当前页面做视觉断言（截图交给视觉模型判断，成本较高）。"
                "仅当步骤要求校验某个'具体内容/画面细节'时使用，如"
                "'确认列表中展示了数据/确认弹窗标题为详情/校验界面上出现了统计图表'。"
                "若步骤只是笼统确认页面正常无报错、并无具体内容要看，请改用 check_health，不要用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": "要判定成立的断言描述，如 『列表中展示了数据』『弹窗标题为详情』",
                    },
                },
                "required": ["claim"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "原地等待指定秒数，不进行任何页面操作。当步骤表达'等待N秒/暂停N秒/停留N秒'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "description": "等待秒数，如 20",
                    },
                },
                "required": ["seconds"],
            },
        },
    },
]

_SYSTEM = (
    "你是 UI 自动化测试的步骤解析器。用户给你一条中文测试步骤，"
    "你必须调用恰好一个最合适的工具来表达该步骤的意图，不要直接回答文本。"
)


def plan_step(step: str) -> dict | None:
    """把一条中文步骤解析为 {action, args}。

    Returns:
        {"action": "login|navigate|check_health|...", "args": {...}}；
        LLM 不可用或未返回工具调用时返回 None（调用方回退规则式）。
    """
    try:
        msg = get_client().chat(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": step},
            ],
            tools=_TOOLS,
        )
    except LLMError:
        return None

    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        return None

    fn = tool_calls[0].get("function", {})
    name = fn.get("name")
    if not name:
        return None
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except (json.JSONDecodeError, TypeError):
        args = {}
    return {"action": name, "args": args}
