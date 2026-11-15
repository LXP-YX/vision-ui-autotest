"""iOS 驱动：统一 AppiumDriver 的 iOS 平台别名（兼容旧 import）。

执行链路已统一到 core_app.drivers.appium_driver.AppiumDriver（Android/iOS 同框架），
本模块仅保留 IosDriver / default_server_url 两个兼容符号。
"""

from __future__ import annotations

from core_app.drivers.appium_driver import AppiumDriver, default_server_url


class IosDriver(AppiumDriver):
    """iOS 平台的 AppiumDriver（platform 固定为 ios）。"""

    def __init__(self, udid: str, bundle_id: str = "", server_url: str = "",
                 device_name: str = "") -> None:
        super().__init__(platform="ios", device_id=udid, app_id=bundle_id,
                         server_url=server_url, device_name=device_name)


__all__ = ["IosDriver", "default_server_url"]
