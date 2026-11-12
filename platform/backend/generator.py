"""AI 用例生成器：自然语言需求 → 结构化用例草稿（方案 P3.6，方式 A）。

关键约束：
- 生成的步骤必须是 Planner 能执行的动作类型（见 _ALLOWED_ACTIONS 说明），
  否则生成了也跑不通；故在 prompt 中明确约束步骤写法；
- 守只读原则：只生成查看/导航/断言类步骤，不生成新增/删除/提交等破坏性操作；
- 用 function call 强制结构化输出；
- 产出一律为草稿（status=draft），由人工审核后入库。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm_client import LLMError, get_client

# 站点已知导航树（帮助模型生成可执行的导航步骤）。
# 与具体站点无关：默认为空，接入被测站点后可按实际菜单结构在此补充，
# 或改造成从平台/配置读取。为空时模型将按需求描述自行推断菜单。
_MENU_HINT: dict[str, list[str]] = {}

_SYSTEM = (
    "你是 UI 自动化测试用例设计师，为当前被测 Web 站点设计中文测试用例。\n"
    "每条用例包含若干中文步骤。步骤必须只使用以下可执行动作，用自然语言表达：\n"
    "1) 登录：写『登录系统』；\n"
    "2) 导航：写『进入 X → Y』（X 为一级菜单，Y 为二级菜单）；\n"
    "3) 点击：写『点击 X』（X 为按钮/菜单/链接等元素）；\n"
    "4) 视觉断言：写『确认 X』（X 为界面上应出现的内容，如 确认列表中展示了数据）；\n"
    "5) 健康检查：写『确认页面正常无报错』。\n"
    "严格遵守只读原则：只做查看/导航/搜索/断言，绝不生成新增/新建/删除/提交/保存/上传等破坏性操作。\n"
    "每条用例第一步通常是『登录系统』。"
    + (f"可参考站点菜单结构：\n{json.dumps(_MENU_HINT, ensure_ascii=False)}" if _MENU_HINT else "")
)

# APP 用例生成系统提示（动作集与 core_app/planner 对齐）
_APP_SYSTEM = (
    "你是 App 自动化测试用例设计师，为当前被测 App（Android/iOS 原生应用）设计中文测试用例。\n"
    "每条用例包含若干中文步骤。步骤必须只使用以下可执行动作，用自然语言表达：\n"
    "1) 启动 App：写『启动被测 App』（通常为首步）；\n"
    "2) 点击：写『点击 X』（X 为按钮/图标/列表项/Tab 等元素）；\n"
    "3) 输入：写『在 X 输入 Y』（X 为输入框元素，Y 为内容）；\n"
    "4) 滑动：写『上滑』『下滑』『左滑』『右滑』；\n"
    "5) 按键：写『按返回键』『按 HOME 键』；\n"
    "6) 视觉断言：写『确认 X』（X 为界面上应出现的内容）；\n"
    "7) 等待：写『等待 N 秒』。\n"
    "严格遵守只读原则：只做查看/浏览/搜索/断言，绝不生成新增/新建/删除/提交/保存/上传等破坏性操作。"
)

_TOOL = [{
    "type": "function",
    "function": {
        "name": "emit_cases",
        "description": "产出一批测试用例草稿",
        "parameters": {
            "type": "object",
            "properties": {
                "cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "用例名"},
                            "module": {"type": "string", "description": "所属模块/一级菜单名"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "steps": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "中文步骤，只用允许的动作写法",
                            },
                        },
                        "required": ["name", "steps"],
                    },
                },
            },
            "required": ["cases"],
        },
    },
}]


# 破坏性关键词：含这些词的步骤一律剔除（守只读原则，双保险）
_RISKY = ("新增", "新建", "创建", "添加", "删除", "移除", "编辑", "修改",
          "提交", "保存", "确定", "确认删除", "导入", "上传")


def _is_risky(step: str) -> bool:
    return any(k in step for k in _RISKY)


def generate_cases(requirement: str, count: int = 3, driver_type: str = "web") -> list[dict]:
    """根据需求描述生成用例草稿列表（driver_type='app' 走 APP 动作集提示）。

    Returns:
        [{name, module, tags, steps}]；失败抛 LLMError。
    """
    system = _APP_SYSTEM if (driver_type or "web") == "app" else _SYSTEM
    user = (
        f"请针对以下测试需求，设计约 {count} 条测试用例：\n{requirement}\n"
        "调用 emit_cases 返回结果，不要输出多余文字。"
    )
    msg = get_client().chat(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        tools=_TOOL,
    )
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        raise LLMError("模型未返回结构化用例（无 tool_calls）")
    try:
        args = json.loads(tool_calls[0]["function"]["arguments"] or "{}")
    except (json.JSONDecodeError, KeyError, TypeError):
        raise LLMError("生成结果解析失败")

    cases = args.get("cases") or []
    out = []
    for c in cases:
        steps = [s.strip() for s in (c.get("steps") or []) if s.strip()]
        steps = [s for s in steps if not _is_risky(s)]  # 剔除破坏性步骤
        if not steps:
            continue
        out.append({
            "name": c.get("name") or "未命名用例",
            "module": c.get("module") or "",
            "tags": c.get("tags") or ["AI生成"],
            "steps": steps,
        })
    return out
