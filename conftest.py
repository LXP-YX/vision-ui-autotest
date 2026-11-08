"""pytest 公共 fixtures：会话级登录复用 + 失败自动截图。

- 会话开始清空 screenshots/；
- 会话级 context 预登录一次，cookies 跨用例复用；
- 每个用例拿到共享登录态的全新 page；
- 用例失败时自动整页截图到 screenshots/。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import BrowserContext, Page

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import browser as browser_mod  # noqa: E402
from core import config  # noqa: E402
from core.login_page import LoginPage  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _clean_screenshots() -> Iterator[None]:
    """会话开始前清空截图目录。"""
    shots = ROOT / config.get("screenshot_dir", default="screenshots")
    if shots.exists():
        for child in shots.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    shots.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture(scope="session")
def logged_in_context() -> Iterator[BrowserContext]:
    """会话级登录态上下文，所有用例共享 cookies。"""
    with browser_mod.launch_context() as (_browser, context):
        page = context.new_page()
        login = LoginPage(page)
        login.open()
        creds = config.get("account")
        login.login(creds["username"], creds["password"])
        page.close()
        yield context


@pytest.fixture
def page(logged_in_context: BrowserContext) -> Iterator[Page]:
    """每个用例独立 page，复用会话登录态。"""
    p = logged_in_context.new_page()
    yield p
    if not p.is_closed():
        p.close()


@pytest.fixture(autouse=True)
def _screenshot_on_failure(request, page: Page) -> Iterator[None]:
    """用例失败时自动整页截图。"""
    yield
    rep = getattr(request.node, "rep_call", None)
    if rep and rep.failed and not page.is_closed():
        out = ROOT / config.get("screenshot_dir", default="screenshots")
        out.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(out / f"FAIL_{request.node.name}.png"), full_page=True)
        except Exception:
            pass


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """暴露用例阶段结果供 fixture 使用。"""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
