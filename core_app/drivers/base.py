"""设备驱动抽象层：平台无关的统一接口 + 统一 UI 节点模型。

DeviceDriver 是 Android（ADB）与 iOS（Appium/XCUITest）驱动的共同契约，
下游（app_runner / self_healing / vision）只面向本模块，不感知具体平台。
平台差异全部收敛在各驱动实现内：

- 坐标系：screenshot() 返回的 PNG 与 tap/swipe 的坐标均为「屏幕像素坐标」，
  两平台同义（iOS 驱动内部负责像素↔逻辑点换算）；
- 按键：press_key 用逻辑键名（home/back/enter/...），各实现自行映射
  （Android→keycode，iOS→WDA 按键），无对应键的抛清晰错误；
- 输入：input_text 向当前聚焦输入框输入（Android 仅 ASCII，iOS 原生 Unicode）；
  type_into 按定位结果直接输入（iOS 用元素级 send_keys，绕开聚焦语义）；
- App 标识：start_app 的 app_id 为 Android package / iOS bundle_id。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# 逻辑键名（press_key 的合法取值；Android 实现→keycode，iOS 实现→WDA 按键名）
LOGICAL_KEYS = ("home", "back", "enter", "menu",
                "volume_up", "volume_down", "power", "recent")


@dataclass
class UiNode:
    """统一 UI 节点模型：各平台原始控件树归一化后的产物。

    Android 来自 uiautomator dump 的 XML；iOS 来自 Appium page_source。
    bounds 为屏幕绝对像素坐标 (x1, y1, x2, y2)，与 screenshot()/tap() 同坐标系。
    """

    text: str = ""              # 显示文本（Android text / iOS label|value）
    desc: str = ""              # 无障碍描述（Android content-desc / iOS name）
    res_id: str = ""            # 资源标识（Android resource-id / iOS 一般为空）
    cls: str = ""               # 控件类名（如 android.widget.Button / XCUIElementTypeButton）
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    clickable: bool = False
    enabled: bool = True

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bounds
        return max(0, x2 - x1) * max(0, y2 - y1)

    def contains(self, px: int, py: int) -> bool:
        x1, y1, x2, y2 = self.bounds
        return x1 <= px <= x2 and y1 <= py <= y2


def node_locator(node: UiNode) -> dict | None:
    """把 UiNode 转成稳定定位 {by, value}，优先级 res_id > desc > text；全空返回 None。"""
    if node.res_id:
        return {"by": "resource_id", "value": node.res_id}
    if node.desc:
        return {"by": "desc", "value": node.desc}
    if node.text:
        return {"by": "text", "value": node.text}
    return None


def node_by_value(node: UiNode, by: str, value: str) -> bool:
    """UiNode 是否命中 {by, value} 定位（self_healing Store 校验用）。"""
    attr = {"text": node.text, "desc": node.desc,
            "resource_id": node.res_id}.get(by)
    return attr is not None and attr == value


class DeviceDriver(ABC):
    """设备驱动统一契约：截图 / 控件树 / 触控 / 输入 / 按键 / App 管理。"""

    platform: str = "unknown"  # android | ios

    # ---- 设备状态 ----

    @abstractmethod
    def is_online(self) -> bool: ...

    @abstractmethod
    def status(self) -> dict:
        """{device_id, online, width, height}。离线时宽高为 0。"""

    @abstractmethod
    def screen_size(self) -> tuple[int, int]:
        """屏幕分辨率（像素），缓存避免重复查询。"""

    # ---- 截图 / 控件树 ----

    @abstractmethod
    def screenshot(self) -> bytes:
        """当前屏幕 PNG bytes（坐标系与 tap 一致）。"""

    @abstractmethod
    def ui_tree(self) -> list[UiNode]:
        """当前界面控件树（已归一化为 UiNode 列表）。"""

    # ---- 触控 / 按键 / 输入 ----

    @abstractmethod
    def tap(self, x: int, y: int) -> None: ...

    @abstractmethod
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None: ...

    @abstractmethod
    def press_key(self, key: str) -> None:
        """按逻辑键（LOGICAL_KEYS 之一，或 Android 平台直接给 keycode 数字）。"""

    @abstractmethod
    def input_text(self, text: str) -> None:
        """向当前聚焦输入框输入文本（Android 仅 ASCII；iOS 原生 Unicode）。"""

    def type_into(self, hit: dict, text: str) -> None:
        """按定位结果输入：默认实现为点击中心聚焦后 input_text。

        iOS 覆写为「按 hit 的 {by,value} 找到元素 → 元素级 send_keys」，
        不依赖焦点语义且支持任意 Unicode。
        """
        cx, cy = hit["center"]
        self.tap(cx, cy)
        self.input_text(text)

    # ---- App 管理 ----

    @abstractmethod
    def start_app(self, app_id: str, activity: str = "") -> None:
        """启动 App（Android: package + 可选 activity；iOS: bundle_id，activity 忽略）。"""

    @abstractmethod
    def stop_app(self, app_id: str) -> None: ...

    def launch(self, app_id: str, activity: str = "") -> str:
        """启动 App 并返回简要描述（执行日志用）。"""
        if not self.is_online():
            raise RuntimeError(f"设备 {getattr(self, 'device_id', '')} 离线，无法启动 App")
        self.start_app(app_id, activity)
        return f"已启动 {app_id}" + (f"/{activity}" if activity else "")

    # ---- 会话生命周期 ----

    def connect(self) -> None:
        """建立底层会话（iOS 的 Appium session，可能较慢）；Android 无操作。"""

    def close(self) -> None:
        """释放会话资源（iOS 的 Appium session；Android 无操作）。"""
