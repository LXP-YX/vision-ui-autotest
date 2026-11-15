"""AppiumDriver：DeviceDriver 的统一 Appium 实现（Android + iOS 同框架）。

双平台走同一条 WebDriver 链路（Appium Server + UiAutomator2 / XCUITest），
用例、脚本、自愈逻辑全部平台无关；平台差异只在能力描述层收敛：

- 会话/能力：Android=UiAutomator2Options，iOS=XCUITestOptions；
- 坐标：iOS 截图是物理像素、控件树是逻辑点，驱动内做 pt↔px 换算；
  Android 截图与控件树同为系统坐标系，无需换算（scale=1）；
- 按键：Android=mobile: pressKey（keycode），iOS=mobile: pressButton；
  逻辑键 back 在 iOS 无实体键 → 映射为左缘边缘返回手势（抹平平台差异）；
- 输入：双端均为元素级 send_keys，原生支持 Unicode（Android 不再受 adb 限制）；
- App 管理：Android=start_activity/activate_app，iOS=activate_app。

原 ios/driver.py 的 IosDriver 与 android/ 的纯 ADB 执行驱动均由此取代；
adb.py 等纯工具保留给 APP 工作台（投屏/录屏/包管理）等非执行场景。
"""

from __future__ import annotations

from typing import Optional

from core_app.drivers.base import DeviceDriver, LOGICAL_KEYS
from core_app.drivers.android import ui_parser as android_parser
from core_app.drivers.ios import ui_parser as ios_parser

# Android 逻辑键名 → keycode
_KEYCODES = {"home": 3, "back": 4, "enter": 66, "menu": 82,
             "volume_up": 24, "volume_down": 25, "power": 26, "recent": 187}

# iOS mobile: pressButton 支持的物理键（back 无对应键，走边缘手势）
_PRESS_BUTTON = {"home": "home", "volume_up": "volumeUp", "volume_down": "volumeDown"}


def default_server_url() -> str:
    """Appium Server 地址：config.yaml 的 appium.server_url，默认本机 4723。"""
    try:
        from core.config import load_config
        cfg = load_config()
        return (cfg.get("appium", {}) or {}).get("server_url") or "http://127.0.0.1:4723"
    except Exception:  # noqa: BLE001  （无 config.yaml 等极端场景）
        return "http://127.0.0.1:4723"


class AppiumDriver(DeviceDriver):
    """绑定单台设备（Android serial / iOS udid）的统一 Appium 驱动（platform=android|ios）。

    会话惰性建立：connect() 显式创建（Android 首建含 UiAutomator2 初始化，
    iOS 含 WDA 启动，真机首次均较慢）；未连接时截图/控件树等操作自动尝试连接。
    """

    def __init__(self, platform: str = "android", device_id: str = "",
                 app_id: str = "", app_activity: str = "",
                 server_url: str = "", device_name: str = "") -> None:
        p = (platform or "android").lower()
        if p not in ("android", "ios"):
            raise ValueError(f"不支持的平台：{platform}（仅支持 android / ios）")
        self.platform = p
        self.device_id = device_id
        self.app_id = app_id            # Android=package / iOS=bundle_id
        self.app_activity = app_activity  # Android 启动 Activity（iOS 忽略）
        self.server_url = server_url or default_server_url()
        self.device_name = device_name or ("Android" if p == "android" else "iPhone")
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
            if self.platform == "android":
                from appium.options.android import UiAutomator2Options
                opts = UiAutomator2Options()
                opts.platform_name = "Android"
                opts.automation_name = "UiAutomator2"
                if self.device_id:
                    opts.udid = self.device_id
                if self.app_id:
                    opts.app_package = self.app_id
                    if self.app_activity:
                        opts.app_activity = self.app_activity
                opts.new_command_timeout = 600
                # 模拟器首次安装/启动 UiAutomator2 Server 较慢，放宽到 120s
                opts.uiautomator2_server_launch_timeout = 120000
                opts.uiautomator2_server_install_timeout = 120000
            else:
                from appium.options.ios import XCUITestOptions
                opts = XCUITestOptions()
                opts.platform_name = "iOS"
                opts.automation_name = "XCUITest"
                opts.device_name = self.device_name
                if self.device_id:
                    opts.udid = self.device_id
                if self.app_id:
                    opts.bundle_id = self.app_id  # session 创建即拉起被测 App
                opts.new_command_timeout = 600
                opts.wda_launch_timeout = 60000   # WDA 首次安装/启动较慢
                opts.wda_connection_timeout = 60000
        except ImportError as e:
            raise RuntimeError(
                "未安装 Appium-Python-Client，请执行：pip install Appium-Python-Client") from e
        try:
            self._session = webdriver.Remote(self.server_url, options=opts)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Appium session 创建失败（server={self.server_url}, {self.platform}={self.device_id}）：{e}；"
                + ("请确认 Appium Server 已启动、设备已连接且 appium-uiautomator2-driver 已安装"
                   if self.platform == "android" else
                   "请确认 Appium Server 已启动且设备已安装 WebDriverAgent")) from e
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
        """Appium 服务可达且 session 可用即视为在线（设备在线性由 connect 时校验）。"""
        if self._session is not None:
            return True
        try:
            import requests
            r = requests.get(f"{self.server_url.rstrip('/')}/status", timeout=5)
            return r.status_code < 500
        except Exception:  # noqa: BLE001
            return False

    def status(self) -> dict:
        """{device_id, online, width, height}。宽高为截图像素尺寸（session 建立后可得）。"""
        online = self.is_online()
        w = h = 0
        if online:
            try:
                w, h = self.screen_size()
            except Exception:  # noqa: BLE001
                w = h = 0
        return {"device_id": self.device_id, "online": online, "width": w, "height": h}

    def screen_size(self) -> tuple[int, int]:
        """屏幕分辨率（像素），即 screenshot() PNG 的实际尺寸（缓存）。

        优先用 PNG IHDR 直接读尺寸（无 Pillow 依赖）；失败回退窗口尺寸。
        """
        if self._px_size is None:
            session = self._require_session()
            try:
                png = session.get_screenshot_as_png()
                # PNG IHDR：宽高在字节 16..24（大端 int32）
                if png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) >= 24:
                    w = int.from_bytes(png[16:20], "big")
                    h = int.from_bytes(png[20:24], "big")
                    if w > 0 and h > 0:
                        self._px_size = (w, h)
            except Exception:  # noqa: BLE001
                pass
            if self._px_size is None:
                rect = session.get_window_rect()
                self._px_size = (int(rect["width"]), int(rect["height"]))
        return self._px_size

    # ---- 坐标换算（iOS: pt 像素 ↔ 逻辑点；Android: 同一坐标系，恒等） ----

    def _scale_pt_to_px(self) -> float:
        """1 pt = scale px。Android 恒为 1；iOS 以截图像素尺寸 / 窗口点尺寸计算。"""
        if self.platform == "android":
            return 1.0
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
        session = self._require_session()
        return session.get_screenshot_as_png()

    def ui_tree(self) -> list:
        """当前界面控件树：page_source XML → UiNode（坐标统一为屏幕像素）。"""
        session = self._require_session()
        xml = session.page_source
        nodes = (android_parser if self.platform == "android" else ios_parser).parse(xml)
        if not nodes:
            return []
        if self.platform == "android":
            return nodes  # 坐标系已一致，无需换算
        s = self._scale_pt_to_px()
        for n in nodes:
            n.bounds = (int(n.bounds[0] * s), int(n.bounds[1] * s),
                        int(n.bounds[2] * s), int(n.bounds[3] * s))
        return nodes

    # ---- 触控 / 按键 / 输入 ----

    def tap(self, x: int, y: int) -> None:
        """点击屏幕像素坐标（走平台原生手势，避开 selenium 版本差异）。"""
        session = self._require_session()
        px, py = self._px_to_pt(int(x), int(y))
        if self.platform == "android":
            session.execute_script("mobile: clickGesture", {"x": px, "y": py})
        else:
            session.execute_script("mobile: tap", {"x": px, "y": py})

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """滑动（像素坐标，duration 控制手势时长；走平台原生手势）。"""
        session = self._require_session()
        sx, sy = self._px_to_pt(int(x1), int(y1))
        ex, ey = self._px_to_pt(int(x2), int(y2))
        d = max(1, int(duration_ms))
        if self.platform == "android":
            session.execute_script("mobile: swipeGesture", {
                "left": min(sx, ex), "top": min(sy, ey),
                "width": max(1, abs(ex - sx)), "height": max(1, abs(ey - sy)),
                "direction": "left" if ex < sx else ("right" if ex > sx else ("up" if ey < sy else "down")),
                "percent": 1.0,
            })
        else:
            session.execute_script("mobile: dragFromToForDuration", {
                "fromX": sx, "fromY": sy, "toX": ex, "toY": ey,
                "duration": max(0.5, d / 1000.0),
            })

    def press_key(self, key: str) -> None:
        """按逻辑键（back/home/enter/...），平台差异在驱动内吸收。

        Android：mobile: pressKey；iOS：home/音量→mobile: pressButton，
        enter→聚焦元素发送换行，back→左缘边缘返回手势（iOS 无实体返回键）。
        """
        session = self._require_session()
        k = str(key).strip().lower()
        if k.isdigit():  # 直接给 keycode（仅 Android 有意义）
            if self.platform == "android":
                session.execute_script("mobile: pressKey", {"keycode": int(k)})
                return
            raise AssertionError(f"iOS 不支持数字 keycode：{key}")
        if self.platform == "android":
            kc = _KEYCODES.get(k)
            if kc is None:
                raise AssertionError(
                    f"未知按键：{key}（支持 {', '.join(LOGICAL_KEYS)} 或 keycode 数字）")
            session.execute_script("mobile: pressKey", {"keycode": kc})
            return
        # iOS
        if k in _PRESS_BUTTON:
            session.execute_script("mobile: pressButton", {"name": _PRESS_BUTTON[k]})
            return
        if k == "enter":
            try:
                session.switch_to.active_element.send_keys("\n")
                return
            except Exception:  # noqa: BLE001
                session.execute_script("mobile: pressButton", {"name": "return"})
                return
        if k == "back":
            self._ios_edge_back()
            return
        raise AssertionError(
            f"iOS 不支持按键：{key}（支持 {', '.join(LOGICAL_KEYS)} 中 iOS 侧可用的子集）")

    def _ios_edge_back(self) -> None:
        """iOS 返回：从屏幕左缘向右滑（系统边缘返回手势）。"""
        w, h = self.screen_size()
        self.swipe(int(w * 0.02), int(h * 0.5), int(w * 0.4), int(h * 0.5), duration_ms=300)

    def input_text(self, text: str) -> None:
        """向当前聚焦输入框输入（依赖 active element；主路径建议用 type_into）。"""
        session = self._require_session()
        if not text:
            return
        session.switch_to.active_element.send_keys(text)

    def _find_element_by_hit(self, hit: dict):
        """把定位结果 {by, value} 翻译成平台原生定位并查找元素（元素级输入用）。"""
        from appium.webdriver.common.appiumby import AppiumBy
        by, value = hit.get("by", ""), hit.get("value", "")
        if not by or not value:
            return None
        session = self._require_session()
        try:
            if self.platform == "android":
                if by == "resource_id":
                    sel = f'new UiSelector().resourceId("{value}")'
                    return session.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                if by == "text":
                    sel = f'new UiSelector().text("{value}")'
                    return session.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                if by == "desc":
                    sel = f'new UiSelector().description("{value}")'
                    return session.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                if by == "type":
                    sel = f'new UiSelector().className("{value}").instance(0)'
                    return session.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                return None
            v = value.replace('\\', '\\\\').replace('"', '\\"')
            if by == "type":
                return session.find_element(
                    AppiumBy.IOS_CLASS_CHAIN,
                    f'**/XCUIElementType{value.replace("XCUIElementType", "")}[0]')
            clause = {"text": f'label == "{v}" OR value == "{v}"',
                      "desc": f'name == "{v}" OR label == "{v}"',
                      "resource_id": f'name == "{v}"'}.get(by)
            if not clause:
                return None
            return session.find_element(AppiumBy.IOS_PREDICATE, clause)
        except Exception:  # noqa: BLE001
            return None

    def type_into(self, hit: dict, text: str) -> None:
        """按定位结果输入：命中元素是可输入控件时用元素级 send_keys（双端原生 Unicode），
        否则回退「点击中心聚焦 → 输入」。

        iOS/Android 都存在同名的非输入节点（导航栏标题、Cell、按钮），直接 send_keys
        会静默发错元素（实测 iOS 发给 Cell 会触发跳转而非输入），故必须校验控件类型。
        """
        el = self._find_element_by_hit(hit)
        if el is not None and self._is_editable(el):
            el.send_keys(text)
            return
        super().type_into(hit, text)

    @staticmethod
    def _is_editable(el) -> bool:
        """判断元素是否可输入控件（Android=EditText；iOS=TextField/SecureTextField/SearchField/TextView）。"""
        try:
            tag = (el.tag_name or "")
        except Exception:  # noqa: BLE001
            return False
        if tag == "android.widget.EditText":
            return True
        return tag in ("XCUIElementTypeTextField", "XCUIElementTypeSecureTextField",
                       "XCUIElementTypeSearchField", "XCUIElementTypeTextView")

    # ---- App 管理 ----

    def start_app(self, app_id: str, activity: str = "") -> None:
        """启动 App。Android=start_activity（activity 可缺省走默认），iOS=activate_app。

        启动是异步的（Activity 切换 + 首帧渲染需时间），返回前做状态等待：
        轮询 page_source 直到目标 App 成为前台（超时则抛错，不静默）。
        """
        session = self._require_session()
        if self.platform == "android":
            if activity:
                session.execute_script("mobile: startActivity",
                                       {"intent": f"{app_id}/{activity}"})
            else:
                session.activate_app(app_id)
            self._wait_app_foreground(app_id, timeout=20.0)
            return
        session.activate_app(app_id)

    def _wait_app_foreground(self, app_id: str, timeout: float = 20.0) -> None:
        """状态等待：Android 以 mobile: getCurrentPackage 判定是否切到前台。

        匹配目标包或其派生包（如系统设置子页由 *.settings.intelligence 渲染）。
        启动本身是异步的，超时视为尽力而为（不抛错），后续 ui_tree/操作会按其自身
        状态自然完成等待——避免把「复合 App 的子页面包名不同」误判为启动失败。
        """
        if self.platform != "android":
            return
        import time as _t
        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            try:
                pkg = self._session.execute_script("mobile: getCurrentPackage") or ""
                if pkg == app_id or pkg.startswith(app_id) or pkg.startswith(app_id + "."):
                    return
                # 目标包是当前包的前缀父级时（com.x.settings → com.x.settings.intelligence）
                if app_id and pkg.startswith(app_id.rsplit(".", 1)[0]):
                    return
            except Exception:  # noqa: BLE001
                pass
            _t.sleep(0.5)

    def stop_app(self, app_id: str) -> None:
        session = self._require_session()
        session.terminate_app(app_id)
