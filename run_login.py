#!/usr/bin/env python
"""登录冒烟脚本（无头）。

用途：跑通登录 —— 无头启动浏览器，填账号、点登录、等顶部导航出现，
校验登录结果。默认无头（不弹窗口），与云端形态一致；仅登录失败时截图。

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
    creds = config.get("account")
    base_url = config.get("base_url")
    print(f"[1/4] 启动浏览器（无头）-> 目标站点 {base_url}")

    with browser_mod.launch_context() as (_browser, context):
        page = context.new_page()

        print("[2/4] 打开登录页 ...")
        login = LoginPage(page)
        login.open()

        print(f"[3/4] 填写账号并登录：{creds['username']}")
        login.login(creds["username"], creds["password"])

        print("[4/4] 校验登录结果 ...")
        main_page = MainPage(page)
        if not main_page.is_ready():
            print("    ✗ 登录后未检测到顶部导航，登录可能失败")
            login.screenshot("LOGIN_FAILED")  # 仅失败截图
            return 1

        menus = main_page.visible_top_menus()
        health = main_page.health_check()
        print("    ✓ 登录成功！")
        print(f"    · 可见顶部菜单：{menus}")
        print(f"    · 页面健康：{health}")

    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
