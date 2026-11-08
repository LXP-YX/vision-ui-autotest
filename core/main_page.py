"""主框架页面对象（Playwright）—— 阶段0仅提供登录后就绪判定与导航可见性。

后续阶段（自愈/自然语言驱动）会在此基础上扩展 nav_to、站点地图发现等能力。
"""

from __future__ import annotations

from core import config
from core.base_page import BasePage


def ready_menu_selector() -> str:
    """登录就绪判据选择器：任一顶部菜单文字的可见节点。

    站点顶部菜单存在隐藏 span + 可见 span 两份，故用 :text-is(...):visible
    精确命中可见节点，用文本而非 hash 类名定位（方案"语义优先"原则）。
    """
    menus = config.get("login_ready_menus", default=["首页", "主页"])
    return ", ".join(f":text-is('{m}'):visible" for m in menus)


class MainPage(BasePage):
    """登录后的主框架。"""

    def is_ready(self) -> bool:
        """顶部导航任一菜单可见即视为主框架就绪。"""
        loc = self.page.locator(ready_menu_selector())
        return loc.count() > 0 and loc.first.is_visible()

    def visible_top_menus(self) -> list[str]:
        """返回当前可见的顶部菜单文字列表。"""
        menus = config.get("login_ready_menus", default=[])
        found = []
        for m in menus:
            loc = self.page.locator(f":text-is('{m}'):visible")
            if loc.count() and loc.first.is_visible():
                found.append(m)
        return found

    def click_top_menu(self, name: str, timeout: int = 10_000) -> None:
        """点击顶部一级菜单（用可见文字节点，避开 hash 类名）。"""
        loc = self.page.locator(f":text-is('{name}'):visible").first
        loc.wait_for(state="visible", timeout=timeout)
        loc.click()

    def click_sub_menu(self, name: str, timeout: int = 10_000) -> None:
        """点击侧边二级菜单。站点侧边项为 aside 内的可见文字节点，
        优先在侧边栏内精确定位可见文本，再回退 antd/element 菜单项。

        点完一级菜单后侧边栏需要时间渲染，故轮询等待目标项出现再点。
        """
        candidates = (
            f"aside :text-is('{name}'):visible",
            f".ant-menu-item:has-text('{name}'), .el-menu-item:has-text('{name}')",
            f":text-is('{name}'):visible",
        )
        import time
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            for sel in candidates:
                loc = self.page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click()
                    return
            self.page.wait_for_timeout(300)
        raise AssertionError(f"未找到二级菜单「{name}」")

    def current_breadcrumb(self) -> str:
        """返回当前面包屑文本（如 '一级菜单 / 二级菜单'），无则空串。"""
        for sel in (".ant-breadcrumb", ".el-breadcrumb", "[class*='breadcrumb']"):
            loc = self.page.locator(sel).first
            if loc.count():
                try:
                    return loc.inner_text(timeout=2_000).replace("\n", " ").strip()
                except Exception:
                    continue
        return ""

    def nav_to(self, top: str, sub: str) -> None:
        """从主页导航到 top → sub 子页面，全程状态等待（禁 sleep）。

        导航后用面包屑校验是否真的到达目标页 —— 到不了则抛错，
        避免"点错菜单却误判通过"的假通过（自愈≠掩盖 Bug）。
        """
        self.click_top_menu(top)
        self.click_sub_menu(sub)
        self.wait_loading_disappear(timeout=8_000)
        # 等主内容容器可见（双框架兼容）
        for sel in (".ant-layout-content", "main", ".main-content", ".el-main"):
            loc = self.page.locator(sel).first
            if loc.count():
                try:
                    loc.wait_for(state="visible", timeout=4_000)
                    break
                except Exception:
                    continue
        # 关键校验：面包屑须含目标二级菜单名，否则说明没真正进入
        self._verify_arrived(sub)

    def _verify_arrived(self, sub: str, timeout: int = 6_000) -> None:
        """轮询面包屑，确认已到达 sub 页；超时抛错。"""
        import time
        deadline = time.time() + timeout / 1000
        last = ""
        while time.time() < deadline:
            last = self.current_breadcrumb()
            if sub in last:
                return
            self.page.wait_for_timeout(300)
        raise AssertionError(
            f"导航未到达目标页「{sub}」：当前面包屑为「{last or '(无)'}」"
        )


