"""IosDriver：DeviceDriver 的 Appium(XCUITest) 实现。

通过 Appium Server（Node 常驻进程）驱动 WebDriverAgent，不直接依赖 Appium 客户端
之外的东西。与 AndroidDriver 完全同构：

- 坐标系：对外统一屏幕「像素」坐标（与 screenshot() 一致）；
  Appium/XCUITest 内部用逻辑点（point），本驱动负责 px↔pt 换算；
- 输入：iOS 原生支持 Unicode（元素级 send_keys），type_into 覆写为元素级输入；
- 按键：逻辑键名 → mobile: pressButton；iOS 无返回键（back）等则抛清晰错误；
- App 管理：activate_app / terminate_app（bundle_id）。

运行前提：
- Appium Server 已启动（pip 安装 Appium-Python-Client；npm 安装 appium + xcuitest 驱动）；
  地址读 config.yaml 的 appium.server_url（默认 http://127.0.0.1:4723）；
- 设备已安装 WebDriverAgent（xcodebuild 部署，真机/模拟器均可）。
"""

from __future__ import annotations

import io
from typing import Optional

from core_app.drivers.base import DeviceDriver, LOGICAL_KEYS
from core_app.drivers.ios import ui_parser

# mobile: pressButton 支持的物理键（iOS 侧逻辑键映射）
_PRESS_BUTTON = {"home": "home", "volume_up": "volumeUp", "volume_down": "volumeDown"}


def default_server_url() -> str:
    """Appium Server 地址：config.yaml 的 appium.server_url，默认本机 4723。"""
    try:
        from core.config import load_config
        cfg = load_config()
        return (cfg.get("appium", {}) or {}).get("server_url") or "http://127.0.0.1:4723"
    except Exception:  # noqa: BLE001  （无 config.yaml 等极端场景）
        return "http://127.0.0.1:4723"


class IosDriver(DeviceDriver):
    """绑定单台 iOS 设备（udid）的 Appium 驱动（platform=ios）。

    会话惰性建立：connect() 显式建立（session 创建含 WDA 启动，真机首次约 30s+）；
    screenshot/ui_tree 等操作在未连接时自动尝试连接。
    """

    platform = "ios"

    def __init__(self, udid: str, bundle_id: str = "", server_url: str = "",
                 device_name: str = "") -> None:
        self.udid = udid
        self.device_id = udid
        self.bundle_id = bundle_id
        self.server_url = server_url or default_server_url()
        self.device_name = device_name or "iPhone"
        self._session = None
        # 逻辑点窗口尺寸 (w_pt, h_pt)；截图像素尺寸 (w_px, h_px)；换算比例缓存
        self._pt_size: Optional[tuple[float, float]] = None
        self._px_size: Optional[tuple[int, int]] = None

    # ---- 会话生命周期 ----

    def connect(self) -> None:
        """创建 Appium session（幂等；失败抛 RuntimeError，信息含排障提示）。"""
        if self._session is not None:
            return
        try:
            from appium import webdriver
            from appium.options.ios import XCUITestOptions
        except ImportError as e:
            raise RuntimeError(
                "未安装 Appium-Python-Client，请执行：pip install Appium-Python-Client") from e
        opts = XCUITestOptions()
        opts.platform_name = "iOS"
        opts.automation_name = "XCUITest"
        opts.device_name = self.device_name
        if self.udid:
            opts.udid = self.udid
        if self.bundle_id:
            opts.bundle_id = self.bundle_id  # session 创建即拉起被测 App
        opts.new_command_timeout = 600
        opts.wda_launch_timeout = 60000   # WDA 首次安装/启动较慢
        opts.wda_connection_timeout = 60000
        try:
            self._session = webdriver.Remote(self.server_url, options=opts)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Appium session 创建失败（server={self.server_url}, udid={self.udid}）：{e}；"
                "请确认 Appium Server 已启动且设备已安装 WebDriverAgent") from e
        try:
            rect = self._session.get_window_rect()
            self._pt_size = (float(rect["width"]), float(rect["height"]))
        except Exception:  # noqa: BLE001
            self._pt_size = None

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.quit()
            except Exception:  # noqa: BLE001
                pass
            self._session = None

    def _require_session(self):
        if self._session is None:
            self.connect()
        return self._session

    # ---- 设备状态 ----

    def is_online(self) -> bool:
        """Appium Server 可达即视为在线（设备在线性由 connect 时校验）。"""
        try:
            import requests
            r = requests.get(f"{self.server_url.rstrip('/')}/status", timeout=5)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def status(self) -> dict:
        """{device_id, online, width, height}。宽高为屏幕像素（session 建立后可得）。"""
        online = self.is_online()
        w = h = 0
        if online and self._session is not None:
            try:
                w, h = self.screen_size()
            except Exception:  # noqa: BLE001
                w = h = 0
        return {"device_id": self.udid, "online": online, "width": w, "height": h}

    def screen_size(self) -> tuple[int, int]:
        """屏幕分辨率（像素），即 screenshot() PNG 的实际尺寸（缓存）。"""
        if self._px_size is None:
            self._require_session()
            from PIL import Image
            im = Image.open(io.BytesIO(self._session.get_screenshot_as_png()))
            self._px_size = (im.size[0], im.size[1])
        return self._px_size

    # ---- 坐标换算（px 像素 ↔ pt 逻辑点） ----

    def _scale_pt_to_px(self) -> float:
        """1 pt = scale px。session 建立后以截图像素尺寸 / 窗口点尺寸计算。"""
        if self._pt_size and self._pt_size[0] > 0:
            px_w = self.screen_size()[0]
            return px_w / self._pt_size[0]
        return 1.0

    def _px_to_pt(self, x: int, y: int) -> tuple[int, int]:
        s = self._scale_pt_to_px()
        return int(x / s), int(y / s)

    # ---- 截图 / 控件树 ----

    def screenshot(self) -> bytes:
        """当前屏幕 PNG bytes（Appium session 截图，设备原生分辨率）。"""
        import base64
        session = self._require_session()
        return base64.b64decode(session.get_screenshot_as_png())

    def ui_tree(self) -> list:
        """当前界面控件树：page_source XML → UiNode（坐标已换算为屏幕像素）。"""
        session = self._require_session()
        xml = session.page_source
        nodes = ui_parser.parse(xml)  # 点坐标
        if not nodes:
            return []
        s = self._scale_pt_to_px()
        for n in nodes:
            n.bounds = (int(n.bounds[0] * s), int(n.bounds[1] * s),
                        int(n.bounds[2] * s), int(n.bounds[3] * s))
        return nodes

    # ---- 触控 / 按键 / 输入 ----

    def _pointer_action(self):
        """W3C 触控指针（touch）。"""
        from selenium.webdriver.common.actions import interaction
        from selenium.webdriver.common.actions.pointer_input import PointerInput
        return PointerInput(interaction.POINTER_TOUCH, "finger")

    def tap(self, x: int, y: int) -> None:
        """点击屏幕像素坐标（内部换算为逻辑点后走 W3C actions）。"""
        session = self._require_session()
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        px, py = self._px_to_pt(int(x), int(y))
        builder = ActionBuilder(session, pointer=self._pointer_action())
        builder.pointer_action.move_to_location(px, py).pointer_down().pointer_up()
        builder.perform()

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """滑动（像素坐标换算为逻辑点，duration 控制手势时长）。"""
        session = self._require_session()
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        sx, sy = self._px_to_pt(int(x1), int(y1))
        ex, ey = self._px_to_pt(int(x2), int(y2))
        builder = ActionBuilder(session, pointer=self._pointer_action(),
                                duration=max(1, int(duration_ms)))
        builder.pointer_action.move_to_location(sx, sy).pointer_down().pause(
            max(1, int(duration_ms))).move_to_location(ex, ey).release()
        builder.perform()

    def press_key(self, key: str) -> None:
        """按逻辑键：home/音量→ mobile: pressButton；enter→聚焦元素发送回车。"""
        session = self._require_session()
        k = str(key).strip().lower()
        if k in _PRESS_BUTTON:
            session.execute_script("mobile: pressButton", {"name": _PRESS_BUTTON[k]})
            return
        if k == "enter":
            # 回车：优先向聚焦元素发送换行符（等价键盘 return），失败再试 pressButton
            try:
                session.switch_to.active_element.send_keys("\n")
                return
            except Exception:  # noqa: BLE001
                session.execute_script("mobile: pressButton", {"name": "return"})
                return
        if k == "back":
            raise AssertionError("iOS 无返回键（back），请改用具体页面元素或 home 键")
        raise AssertionError(
            f"iOS 不支持按键：{key}（支持 {', '.join(LOGICAL_KEYS)} 中 iOS 侧可用的子集）")

    def input_text(self, text: str) -> None:
        """向当前聚焦输入框输入（依赖 active element；主路径建议用 type_into）。"""
        session = self._require_session()
        if not text:
            return
        session.switch_to.active_element.send_keys(text)

    def _find_element_by_hit(self, hit: dict):
        """把定位结果 {by, value} 翻译成 Appium predicate 定位并查找元素。"""
        from appium.webdriver.common.appiumby import AppiumBy
        by, value = hit.get("by", ""), hit.get("value", "")
        if not by or not value:
            return None
        v = value.replace('\\', '\\\\').replace('"', '\\"')
        clause = {"text": f'label == "{v}" OR value == "{v}"',
                  "desc": f'name == "{v}" OR label == "{v}"',
                  "resource_id": f'name == "{v}"'}.get(by)
        if not clause:
            return None
        session = self._require_session()
        try:
            return session.find_element(AppiumBy.IOS_PREDICATE, clause)
        except Exception:  # noqa: BLE001
            return None

    def type_into(self, hit: dict, text: str) -> None:
        """按定位结果输入：优先元素级 send_keys（原生 Unicode）；找不到元素回退点击+聚焦输入。"""
        el = self._find_element_by_hit(hit)
        if el is not None:
            el.send_keys(text)
            return
        super().type_into(hit, text)

    # ---- App 管理 ----

    def start_app(self, app_id: str, activity: str = "") -> None:
        """启动 App（bundle_id；activity 参数 iOS 不使用，保留签名对称）。"""
        session = self._require_session()
        session.activate_app(app_id)

    def stop_app(self, app_id: str) -> None:
        session = self._require_session()
        session.terminate_app(app_id)
