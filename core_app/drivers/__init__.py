"""设备驱动抽象层（APP 执行内核的底层接缝）。

分层：
base.py              DeviceDriver 统一契约 + UiNode 统一节点模型 + 逻辑键名
appium_driver.py     统一 Appium 实现（Android=UiAutomator2 / iOS=XCUITest，
                     执行链路双端同框架、用例/脚本可跨平台复用）
android/             ADB 工具（工作台投屏/录屏/包管理等非执行场景仍用 adb）
ios/                 iOS 侧工具

create_driver(env) 按环境平台选择实现；下游（app_runner/self_healing/vision）
只 import 本包与 base，不感知具体平台。
"""

from __future__ import annotations

from core_app.drivers.base import DeviceDriver, LOGICAL_KEYS, UiNode, node_by_value, node_locator
from core_app.drivers.appium_driver import AppiumDriver, default_server_url
from core_app.drivers.android.driver import AndroidDriver  # 兼容别名（纯 ADB，工作台用）
from core_app.drivers.ios.driver import IosDriver  # 兼容别名（= AppiumDriver，ios 平台）

__all__ = [
    "DeviceDriver", "UiNode", "LOGICAL_KEYS", "node_locator", "node_by_value",
    "AppiumDriver", "default_server_url", "create_driver",
    "AndroidDriver", "IosDriver",
]


def create_driver(platform: str, device_id: str, app_id: str = "",
                  app_activity: str = "", device_name: str = "") -> DeviceDriver:
    """按环境平台构造驱动实例。

    Android 与 iOS 均返回统一的 AppiumDriver（同一执行框架，用例/脚本跨平台复用）。
    Args:
        platform: android | ios
        device_id: Android serial / iOS udid
        app_id: 被测 App（package / bundle_id）
        app_activity: Android 启动 Activity（iOS 忽略）
        device_name: iOS 设备名（Appium capability，可选）
    Raises:
        ValueError: 平台不支持。
    """
    return AppiumDriver(platform=platform, device_id=device_id, app_id=app_id,
                        app_activity=app_activity, device_name=device_name)
