"""页面对象基类（Playwright）。

封装通用能力：状态等待、交互（带 JS 兜底）、UI 健康检查、安全截图。
工程铁律：严禁 time.sleep 做同步，一律状态等待。
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError as PWTimeout

from core import config

ROOT = Path(__file__).resolve().parent.parent


class BasePage:
    """所有页面对象的基类。用例/工具经由页面对象操作，不直接碰裸 page。"""

    def __init__(self, page: Page):
        self.page = page

    # ------------------------- 等待 -------------------------

    def wait_visible(self, selector: str, timeout: int = 10_000) -> Locator:
        """等待首个匹配元素可见并返回其 locator。"""
        loc = self.page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        return loc

    def wait_hidden(self, selector: str, timeout: int = 10_000) -> None:
        """等待元素消失（不存在或不可见均算消失）。"""
        loc = self.page.locator(selector)
        if loc.count() == 0:
            return
        try:
            loc.first.wait_for(state="hidden", timeout=timeout)
        except PWTimeout:
            pass

    def wait_loading_disappear(self, timeout: int = 10_000) -> None:
        """等待 antd / element-ui 的 loading 遮罩消失（双框架兼容）。

        先给 spinner 短暂的「出现机会窗」（300ms）：点击触发请求后 spinner 需要
        几十毫秒才挂载到 DOM；若立即检查会误以为没有 loading 而直接放行。
        遮罩一旦出现，就等它消失（最长 timeout ms）；始终未出现也正常返回。
        """
        selectors = (
            ".ant-spin-spinning",
            ".el-loading-mask",
            ".loading-mask",
        )
        # 短暂等任一 loading 遮罩出现（最多 300ms），让 spinner 来得及挂载
        try:
            self.page.locator(", ".join(selectors)).first.wait_for(
                state="visible", timeout=300
            )
        except Exception:  # noqa: BLE001
            # 300ms 内没出现任何 loading —— 说明是同步操作或极快响应，直接返回
            return
        # loading 出现了，等它消失
        for sel in selectors:
            self.wait_hidden(sel, timeout=timeout)

    # ------------------------- 交互 -------------------------

    def safe_click(self, selector: str, timeout: int = 10_000) -> bool:
        """点击首个可见元素，多级回退保证弹框/动画场景下也能命中。

        等待顺序：visible → 等弹框入场动画完成 → click。
        antd/el-plus Modal/Dialog 有 300-500ms 过渡动画，在动画期间
        click() 坐标会落在蒙层或动画中途的位置，需等动画结束再点。
        """
        loc = self.page.locator(selector).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            # 等待 antd / element-plus 弹框入场动画完成：
            # 若当前有弹框（Modal/Dialog 正在展示），等到 ant-zoom-appear /
            # el-overlay-dialog 的 transition 结束（最长 600ms）。
            # 没有弹框时此方法瞬间返回（count() == 0）。
            for modal_sel in (
                ".ant-modal.ant-zoom-appear",
                ".ant-modal.ant-zoom-enter",
                ".el-overlay-dialog .el-dialog__body",
            ):
                try:
                    if self.page.locator(modal_sel).count() > 0:
                        self.page.locator(modal_sel).first.wait_for(
                            state="hidden", timeout=800
                        )
                        break
                except Exception:
                    pass
            # 额外等 200ms 确保动画结束、按钮坐标稳定
            self.page.wait_for_timeout(200)
            loc.click(timeout=timeout)
            return True
        except PWTimeout:
            try:
                loc.evaluate("el => el.click()")
                return True
            except Exception:
                return False
        except Exception:
            # 非超时异常（intercepted / overlay）：force=True 直接派发真实指针事件
            try:
                self.page.wait_for_timeout(200)
                loc.click(force=True, timeout=timeout)
                return True
            except Exception:
                try:
                    loc.evaluate("el => el.click()")
                    return True
                except Exception:
                    return False

    def fill(self, selector: str, value: str, timeout: int = 10_000) -> None:
        """在可见输入框填值。"""
        loc = self.wait_visible(selector, timeout=timeout)
        loc.fill(value)

    # ------------------------- UI 健康检查 -------------------------

    def has_error_toast(self) -> bool:
        """检测 antd / element-ui 错误提示是否可见（双框架兼容）。"""
        for sel in (
            ".ant-message-error",
            ".ant-notification-notice-error",
            ".el-message--error",
            ".el-notification.is-error",
        ):
            loc = self.page.locator(sel)
            if loc.count() and loc.first.is_visible():
                return True
        return False

    def count_broken_images(self) -> int:
        """返回 naturalWidth==0 的 <img> 数量（破图）。"""
        return int(self.page.evaluate(
            "() => Array.from(document.images)"
            ".filter(img => img.complete && img.naturalWidth === 0).length"
        ))

    def is_blank_body(self) -> bool:
        """白屏检测：body 可见文本近乎为空。"""
        length = int(self.page.evaluate(
            "() => document.body ? document.body.innerText.trim().length : 0"
        ))
        return length < 10

    def health_check(self) -> dict:
        """汇总当前页面健康状况。"""
        result = {
            "error_toast": self.has_error_toast(),
            "broken_images": self.count_broken_images(),
            "blank_body": self.is_blank_body(),
        }
        result["healthy"] = (
            not result["error_toast"]
            and result["broken_images"] == 0
            and not result["blank_body"]
        )
        return result

    # ------------------------- 截图 -------------------------

    def screenshot(self, name: str, subdir: str = "") -> Path:
        """整页截图到 screenshots/ 目录，返回路径。"""
        base = ROOT / config.get("screenshot_dir", default="screenshots")
        out_dir = base / subdir if subdir else base
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=True)
        return path
