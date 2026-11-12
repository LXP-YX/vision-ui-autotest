"""设备驱动抽象层（APP 执行内核的底层接缝）。

分层：
base.py              DeviceDriver 统一契约 + UiNode 统一节点模型 + 逻辑键名
android/             ADB 实现（纯 stdlib，Android 真机/模拟器）
ios/                 Appium(XCUITest) 实现（需 Appium Server + WebDriverAgent）

create_driver(env) 按环境平台选择实现；下游（app_runner/self_healing/vision）
只 import 本包与 base，不感知具体平台。
"""

from __future__ import annotations

from core_app.drivers.base import DeviceDriver, LOGICAL_KEYS, UiNode, node_by_value, node_locator
from core_app.drivers.android.driver import AndroidDriver
from core_app.drivers.ios.driver import IosDriver, default_server_url

__all__ = [
    "DeviceDriver", "UiNode", "LOGICAL_KEYS", "node_locator", "node_by_value",
    "AndroidDriver", "IosDriver", "default_server_url", "create_driver",
]


def create_driver(platform: str, device_id: str, app_id: str = "",
                  app_activity: str = "", device_name: str = "") -> DeviceDriver:
    """按环境平台构造驱动实例。

    Args:
        platform: android | ios
        device_id: Android serial / iOS udid
        app_id: 被测 App（package / bundle_id）
        app_activity: Android 启动 Activity（iOS 忽略）
        device_name: iOS 设备名（Appium capability，可选）
    Raises:
        ValueError: 平台不支持。
    """
    p = (platform or "android").lower()
    if p == "android":
        return AndroidDriver(device_id)
    if p == "ios":
        return IosDriver(udid=device_id, bundle_id=app_id, device_name=device_name)
    raise ValueError(f"不支持的 APP 平台：{platform}（仅支持 android / ios）")
