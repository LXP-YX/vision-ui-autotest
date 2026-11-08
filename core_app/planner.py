"""App Planner：把一条中文步骤解析为结构化 App 动作（function call）。

对标 platform/worker/planner.py，差异：动作集换成 App 适用动作。
- tap_element(target)        三级定位找元素坐标 → adb tap
- type_text(target, text)    定位输入框 → 聚焦 → adb input text
- swipe(direction)           方向滑动 up/down/left/right（也可显式坐标）
- press_key(keycode)         adb keyevent
- assert_visual(claim)       VLM 视觉断言
- launch_app(package, activity)  启动 App
- wait(seconds)              原地等待

LLM 不可用 / 解析失败时，调用方回退规则式解释器（app_runner._interpret_step）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm_client import LLMError, get_client

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tap_element",
            "description": "点击界面上某个元素（按钮/图标/列表项/输入框等）。当步骤表达'点击/按下/打开 X'且 X 是具体元素时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "目标元素的自然语言描述，如 '登录按钮' '搜索图标'"},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "在某个输入框输入文本。当步骤表达'在 X 输入 Y / 填写 Y 到 X'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "目标输入框的自然语言描述，如 '用户名输入框'"},
                    "text": {"type": "string", "description": "要输入的内容（仅支持 ASCII，中文不支持）"},
                },
                "required": ["target", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "swipe",
            "description": "屏幕滑动。当步骤表达'向上滑/向下滑/左滑/右滑/下拉刷新'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"],
                                   "description": "滑动方向"},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "按物理/系统键。当步骤表达'按返回键/按 HOME/按回车'时使用。keycode：3=HOME 4=BACK 66=回车 24/25=音量±",
            "parameters": {
                "type": "object",
                "properties": {
                    "keycode": {"type": "integer", "description": "Android keycode，如 4=BACK"},
                    "name": {"type": "string", "description": "按键中文名（可选），如 返回键"},
                },
                "required": ["keycode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assert_visual",
            "description": "对当前界面做视觉断言（截图交 VLM 判断，成本较高）。仅当步骤要求校验具体内容/画面细节时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "description": "断言描述，如 '页面显示欢迎语'"},
                },
                "required": ["claim"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "启动指定 App。当步骤表达'启动/打开 X App'或用例首步要拉起被测 App 时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {"type": "string", "description": "App 包名，如 com.example.myapp"},
                    "activity": {"type": "string", "description": "启动 Activity，如 .ui.LoginActivity"},
                },
                "required": ["package", "activity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "原地等待指定秒数，不进行任何操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "等待秒数"},
                },
                "required": ["seconds"],
            },
        },
    },
]

_SYSTEM = (
    "你是安卓 App 自动化测试的步骤解析器。用户给你一条中文测试步骤，"
    "你必须调用恰好一个最合适的工具来表达该步骤的意图，不要直接回答文本。"
)


def plan_step(step: str) -> dict | None:
    """把一条中文步骤解析为 {action, args}。

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
