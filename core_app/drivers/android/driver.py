"""AndroidDriver：DeviceDriver 的 ADB 实现。

设备句柄 + 面向执行器的高层操作，底层命令全部经 android.adb 封装。
多设备互不干扰（所有命令带 -s serial）；无会话生命周期，close() 为无操作。
"""

from __future__ import annotations

from typing import Optional

from core_app.drivers import android
from core_app.drivers.android import ui_parser
from core_app.drivers.base import DeviceDriver, LOGICAL_KEYS

# 逻辑键名 → Android keycode
_KEYCODES = {"home": 3, "back": 4, "enter": 66, "menu": 82,
             "volume_up": 24, "volume_down": 25, "power": 26, "recent": 187}


class AndroidDriver(DeviceDriver):
    """绑定单个设备 serial 的 ADB 驱动（platform=android）。"""

    platform = "android"

    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.device_id = serial
        self._size: Optional[tuple[int, int]] = None

    # ---- 设备状态 ----

    def is_online(self) -> bool:
        return android.is_online(self.serial)

    def status(self) -> dict:
        """{device_id, online, width, height}。"""
        st = android.device_status(self.serial)
        self._size = (st["width"], st["height"]) if st["online"] else None
        return {"device_id": self.serial, "online": st["online"],
                "width": st["width"], "height": st["height"]}

    def screen_size(self) -> tuple[int, int]:
        """设备分辨率（像素），缓存避免重复 wm size。"""
        if self._size is None:
            self._size = android.screen_size(self.serial)
        return self._size

    # ---- 截图 / 控件树 ----

    def screenshot(self) -> bytes:
        """当前屏幕 PNG bytes（adb exec-out screencap）。"""
        return android.screenshot(self.serial)

    def ui_tree(self) -> list:
        """当前界面控件树：uiautomator dump XML → UiNode 列表。"""
        return ui_parser.parse(android.dump_ui(self.serial))

    # ---- 触控 / 按键 / 输入 ----

    def tap(self, x: int, y: int) -> None:
        android.tap(self.serial, x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        android.swipe(self.serial, x1, y1, x2, y2, duration_ms)

    def press_key(self, key: str) -> None:
        """按逻辑键；纯数字直接当 keycode（Android 特有兼容）。"""
        k = str(key).strip()
        if k.isdigit():
            android.keyevent(self.serial, int(k))
            return
        kc = _KEYCODES.get(k.lower())
        if kc is None:
            raise AssertionError(
                f"未知按键：{key}（支持 {', '.join(LOGICAL_KEYS)} 或 keycode 数字）")
        android.keyevent(self.serial, kc)

    def input_text(self, text: str) -> None:
        android.input_text(self.serial, text)

    # ---- App 管理 ----

    def start_app(self, app_id: str, activity: str = "") -> None:
        if activity:
            android.start_app(self.serial, app_id, activity)
        else:
            android.start_app_by_package(self.serial, app_id)

    def stop_app(self, app_id: str) -> None:
        android.stop_app(self.serial, app_id)
