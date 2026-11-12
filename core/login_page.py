"""登录页面对象（Playwright）。

登录成功判据：顶部导航出现任一菜单文字（config.login_ready_menus），
而非 sleep 等待 —— 符合方案"语义优先、状态等待"原则。
"""

from __future__ import annotations

from playwright.sync_api import TimeoutError as PWTimeout

from core import config
from core.base_page import BasePage


class LoginPage(BasePage):
    """登录页。"""

    # 用户名 / 密码 / 登录按钮候选选择器（语义优先，多候选兜底）
    USERNAME_SELECTORS = (
        "input[placeholder*='用户名']",
        "input[placeholder*='账号']",
        "input[type='text']",
    )
    PASSWORD_SELECTORS = (
        "input[placeholder*='密码']",
        "input[type='password']",
    )
    LOGIN_BTN_SELECTORS = (
        "button:has-text('登录')",
        "button:has-text('登 录')",
        "button[type='submit']",
    )

    def open(self) -> None:
        """打开登录页（站点 URL 即打开地址）并等待用户名输入框出现。"""
        self.page.goto(config.get("base_url").rstrip("/"), wait_until="domcontentloaded")
        self.wait_visible(", ".join(self.USERNAME_SELECTORS), timeout=15_000)

    def login(self, username: str, password: str) -> None:
        """执行登录：填账号密码 -> 点登录 -> 等顶部导航可见。"""
        self.fill(", ".join(self.USERNAME_SELECTORS), username)
        self.fill(", ".join(self.PASSWORD_SELECTORS), password)

        for sel in self.LOGIN_BTN_SELECTORS:
            loc = self.page.locator(sel)
            if loc.count() and loc.first.is_visible():
                loc.first.click()
                break

        # 等 URL 离开登录页（失败不致命，以导航可见为最终判据）
        try:
            self.page.wait_for_url(lambda u: "/login" not in u, timeout=15_000)
        except PWTimeout:
            pass

        self._wait_logged_in()

    def _wait_logged_in(self, timeout: int = 15_000) -> None:
        """以顶部导航任一菜单文字可见作为登录成功判据。

        注意：站点顶部菜单文字存在"隐藏 span + 可见 span"两份，
        故用 :text-is(...):visible 精确匹配可见节点，
        用文本而非 hash 类名定位（符合方案"语义优先"原则）。
        """
        menus = config.get("login_ready_menus", default=["首页", "主页"])
        selector = ", ".join(f":text-is('{m}'):visible" for m in menus)
        self.page.locator(selector).first.wait_for(state="visible", timeout=timeout)

    def is_login_failed(self) -> bool:
        """是否出现登录失败提示。"""
        return self.has_error_toast()
