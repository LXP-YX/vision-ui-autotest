"""浏览器生命周期管理（Playwright，有头可视化优先）。

阶段0要求可视化启动，故 headless 默认取自 config.yaml 的 browser.headless=false；
可用环境变量 PW_HEADLESS=1 临时切回无头（CI 场景）。
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from core import config


_FULLSCREEN_MOCK_JS = """\
// 无头模式下 requestFullscreen() 会静默失败，fullscreenchange 不触发。
// mock 让调用方以为全屏成功（返回已 resolve 的 Promise），并立即派发事件，
// 使依赖全屏布局的页面（如远程座舱）正常渲染。
(function () {
  if (window.__fullscreen_mocked) return;
  window.__fullscreen_mocked = true;

  const _fakeFullscreen = function () {
    const el = this;
    Object.defineProperty(document, 'fullscreenElement', { get: () => el, configurable: true });
    el.dispatchEvent(new Event('fullscreenchange', { bubbles: true }));
    document.dispatchEvent(new Event('fullscreenchange', { bubbles: true }));
    return Promise.resolve();
  };
  Element.prototype.requestFullscreen = _fakeFullscreen;
  Element.prototype.webkitRequestFullscreen = _fakeFullscreen;
  Element.prototype.mozRequestFullScreen = _fakeFullscreen;
  Element.prototype.msRequestFullscreen = _fakeFullscreen;

  document.exitFullscreen = function () {
    Object.defineProperty(document, 'fullscreenElement', { get: () => null, configurable: true });
    document.dispatchEvent(new Event('fullscreenchange', { bubbles: true }));
    return Promise.resolve();
  };
})();
"""


def _resolve_headless() -> bool:
    """无头判定：环境变量 PW_HEADLESS 优先，否则取配置。

    PW_HEADLESS=1 -> 无头；PW_HEADLESS=0 -> 有头；未设置则用 config。
    """
    env = os.environ.get("PW_HEADLESS")
    if env is not None:
        return env.strip() == "1"
    return bool(config.get("browser", "headless", default=False))


@contextmanager
def launch_context(headless: bool | None = None) -> Iterator[tuple[Browser, BrowserContext]]:
    """启动浏览器并创建带视口的 context（上下文管理器，自动清理）。

    headless=None 时走 _resolve_headless() 读环境变量/配置；
    显式传 True/False 时直接使用，不读环境变量（录制场景强制有头时用此参数）。
    """
    if headless is None:
        headless = _resolve_headless()
    slow_mo = int(config.get("browser", "slow_mo_ms", default=0)) if not headless else 0
    vp = config.get("browser", "viewport", default={"width": 1440, "height": 900})

    with sync_playwright() as p:
        launch_args = ["--no-sandbox", "--start-maximized"]
        if headless:
            # 新版无头（--headless=new）行为与有头一致，支持 WebRTC/WebGL/扩展，
            # 老版无头会导致座舱这类依赖媒体流/GPU 的页面白屏。
            launch_args += [
                "--headless=new",
                "--use-gl=angle",
                "--use-fake-ui-for-media-stream",  # 自动放行摄像头/麦克风权限提示
                "--autoplay-policy=no-user-gesture-required",
                "--enable-features=WebRTC",
            ]
        browser = p.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
            args=launch_args,
        )
        context = browser.new_context(
            viewport={"width": int(vp["width"]), "height": int(vp["height"])},
            ignore_https_errors=True,
            permissions=["camera", "microphone"],  # 座舱推流通常需要
        )
        context.set_default_timeout(
            int(config.get("browser", "default_timeout_ms", default=15000))
        )
        context.set_default_navigation_timeout(
            int(config.get("browser", "nav_timeout_ms", default=30000))
        )
        # 注入 Fullscreen API mock：无头模式下 requestFullscreen() 静默失败，
        # 此 mock 让页面（如远程座舱）以为全屏成功并触发 fullscreenchange 事件，
        # 保证依赖全屏布局的页面正常渲染，对所有当前和未来页面生效。
        context.add_init_script(_FULLSCREEN_MOCK_JS)
        try:
            yield browser, context
        finally:
            context.close()
            browser.close()


def new_page(context: BrowserContext) -> Page:
    """在已有 context 上开新页（复用登录态）。"""
    return context.new_page()
