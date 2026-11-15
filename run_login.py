#!/usr/bin/env python
"""站点就绪冒烟脚本（无头）。

用途：跑通「打开环境 URL → 顶部导航出现 → 页面健康」，校验被测站点可访问。
web 环境不含账号密码，登录由站点会话/免登状态承担，本脚本不再填表登录。
默认无头（不弹窗口），与云端形态一致；仅失败时截图。

运行：
    python3 run_login.py
如需临时有头调试：PW_HEADLESS=0 python3 run_login.py
依赖：pip install -r requirements.txt（PEP 668 管控的环境加 --break-system-packages）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import browser as browser_mod
from core import config
from core.login_page import LoginPage
from core.main_page import MainPage


def main() -> int:
    base_url = config.get("base_url")
    print(f"[1/3] 启动浏览器（无头）-> 目标环境 {base_url}")

    with browser_mod.launch_context() as (_browser, context):
        page = context.new_page()

        print("[2/3] 打开环境 URL ...")
        page.goto(base_url.rstrip("/"), wait_until="domcontentloaded")

        print("[3/3] 校验站点就绪 ...")
        main_page = MainPage(page)
        if not main_page.is_ready():
            print("    ✗ 未检测到顶部导航，站点可能未就绪或需要登录")
            LoginPage(page).screenshot("LOGIN_FAILED")  # 仅失败截图
            return 1

        menus = main_page.visible_top_menus()
        health = main_page.health_check()
        print("    ✓ 站点就绪！")
        print(f"    · 可见顶部菜单：{menus}")
        print(f"    · 页面健康：{health}")

    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
