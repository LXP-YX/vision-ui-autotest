"""App 后端模块：REST 路由 + 实时投屏 WebSocket。

- router.py    /api/app/* 设备/截图/触控/App 管理
- screen_ws.py /ws/app/screen/{serial} ADB 截图采集循环 → 推订阅 WS

由 platform/backend/main.py include_router 挂载；底层 ADB 能力由 core_app 提供。
"""
