"""执行分发器：按用例类型分流到 WEB / APP 执行器。

分叉点从 web runner 内部上提到 manager 层（解耦关键）：
- driver_type='app' → core_app.app_runner.run_app_case
  （DeviceDriver 抽象：android=ADB / ios=Appium；不 import Playwright/web 内核）；
- 其余（缺省 'web'）→ worker.runner.run_case（惰性 import，
  纯 APP 部署不装 Playwright 也能跑 APP 用例）。

两侧事件协议一致（step / frame / log / heal / done），由 RunSession 统一桥接。
"""

from __future__ import annotations

from typing import Callable

EventCb = Callable[[str, dict], None]


def _noop(kind: str, payload: dict) -> None:
    pass


def run_any_case(case: dict, emit: EventCb = _noop, run_id: int | None = None,
                 cancel_event=None) -> dict:
    """执行一条用例（WEB 或 APP，按 driver_type 分流）。"""
    if (case.get("driver_type") or "web") == "app":
        from core_app.app_runner import run_app_case  # 惰性：APP 链路不加载 Playwright
        return run_app_case(case, emit=emit, run_id=run_id, cancel_event=cancel_event)
    from runner import run_case  # 惰性：worker/ 目录（模块加载时 sys.path 已就绪）
    return run_case(case, emit=emit, run_id=run_id, cancel_event=cancel_event)
