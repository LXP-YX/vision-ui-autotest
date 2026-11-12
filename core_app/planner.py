"""App Planner：把一条中文步骤解析为结构化 App 动作（function call，平台感知）。

动作集（iOS / Android 共用，能力差异由驱动层吸收）：
- tap_element(target)        三级定位找元素 → 驱动点击
- type_text(target, text)    定位输入框 → 输入（iOS 支持 Unicode；Android 仅 ASCII）
- swipe(direction)           方向滑动 up/down/left/right
- press_key(key)             逻辑键名（home/back/enter/...，各平台映射见 drivers/base.LOGICAL_KEYS）
- assert_visual(claim)       VLM 视觉断言
- launch_app(app_id)         启动被测 App（Android=package，iOS=bundle_id；activity 仅 Android 可选）
- wait(seconds)              原地等待

LLM 不可用 / 解析失败时返回 None，调用方回退规则式解释器（app_runner._interpret_step_rule）。
function-call 解析骨架复用 core.llm_client.plan_with_tools（与 WEB Planner 共用）。
"""

from __future__ import annotations

from core.llm_client import plan_with_tools


def _tools(platform: str) -> list[dict]:
    """按平台构造动作集（差异仅体现在描述文案与参数说明里）。"""
    input_note = (
        "要输入的内容（iOS 原生支持中文等任意 Unicode 字符）"
        if platform == "ios"
        else "要输入的内容（Android 的 adb input 仅支持 ASCII，中文不支持）"
    )
    key_note = (
        "逻辑键名，如 home/volume_up/volume_down/enter（iOS 无 back 键）"
        if platform == "ios"
        else "逻辑键名，如 home/back/enter/volume_up/volume_down"
    )
    launch_desc = (
        "启动指定 App。当步骤表达'启动/打开 X App'或用例首步要拉起被测 App 时使用。"
        "app_id 填 bundle_id，如 com.example.myapp。"
        if platform == "ios"
        else "启动指定 App。当步骤表达'启动/打开 X App'或用例首步要拉起被测 App 时使用。"
             "app_id 填包名 package，如 com.example.myapp；activity 为启动 Activity（可选）。"
    )
    return [
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
                        "text": {"type": "string", "description": input_note},
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
                "description": "按物理/系统键。当步骤表达'按返回键/按 HOME/按回车'时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": key_note},
                        "name": {"type": "string", "description": "按键中文名（可选），如 返回键"},
                    },
                    "required": ["key"],
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
                "description": launch_desc,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app_id": {"type": "string", "description": "被测 App 标识（Android=package / iOS=bundle_id）"},
                        "activity": {"type": "string", "description": "Android 启动 Activity（可选，iOS 忽略），如 .ui.LoginActivity"},
                    },
                    "required": ["app_id"],
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
    "你是 App 自动化测试的步骤解析器。用户给你一条中文测试步骤，"
    "你必须调用恰好一个最合适的工具来表达该步骤的意图，不要直接回答文本。"
)


def plan_step(step: str, platform: str = "android") -> dict | None:
    """把一条中文步骤解析为 {action, args}（按目标平台适配动作集描述）。

    LLM 不可用或未返回工具调用时返回 None（调用方回退规则式）。
    """
    return plan_with_tools(step, _SYSTEM, _tools(platform))
