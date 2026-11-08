"""AppDriver：设备句柄 + 面向执行器的高层操作（基于 adb_bridge）。

对标方案 driver.py 的「Appium 会话管理」职责；本仓库走纯 ADB 路线，
不存在 session 生命周期，driver 即「serial 绑定 + 便捷方法集合」。

AppRunner / self_healing / vision 均通过 AppDriver 间接调 ADB，便于后续替换
底层实现（如换成 Appium）时只改本文件。
"""

from __future__ import annotations

from typing import Optional

from core_app import adb_bridge


class AppDriver:
    """绑定单个设备 serial 的 ADB 驱动。"""

    def __init__(self, serial: str) -> None:
        self.serial = serial
        self._size: Optional[tuple[int, int]] = None

    # ---- 设备状态 ----

    def is_online(self) -> bool:
        return adb_bridge.is_online(self.serial)

    def status(self) -> dict:
        """{serial, online, width, height}。"""
        st = adb_bridge.device_status(self.serial)
        self._size = (st["width"], st["height"]) if st["online"] else None
        return st

    def screen_size(self) -> tuple[int, int]:
        """设备分辨率，缓存避免重复 wm size。"""
        if self._size is None:
            self._size = adb_bridge.screen_size(self.serial)
        return self._size

    # ---- 截图 / UI dump ----

    def screenshot(self) -> bytes:
        """当前屏幕 PNG bytes。"""
        return adb_bridge.screenshot(self.serial)

    def dump_ui(self) -> str:
        """当前界面 UI Hierarchy XML 文本（语义定位基础）。"""
        return adb_bridge.dump_ui(self.serial)

    # ---- 触控 / 按键 / 输入 ----

    def tap(self, x: int, y: int) -> None:
        adb_bridge.tap(self.serial, x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        adb_bridge.swipe(self.serial, x1, y1, x2, y2, duration_ms)

    def keyevent(self, keycode: int) -> None:
        adb_bridge.keyevent(self.serial, keycode)

    def input_text(self, text: str) -> None:
        adb_bridge.input_text(self.serial, text)

    # ---- App ----

    def start_app(self, package: str, activity: str) -> None:
        adb_bridge.start_app(self.serial, package, activity)

    def stop_app(self, package: str) -> None:
        adb_bridge.stop_app(self.serial, package)

    def launch(self, package: str, activity: str) -> str:
        """启动 App 并返回简要描述（执行日志用）。"""
        if not self.is_online():
            raise RuntimeError(f"设备 {self.serial} 离线，无法启动 App")
        adb_bridge.start_app(self.serial, package, activity)
        return f"已启动 {package}/{activity}"
