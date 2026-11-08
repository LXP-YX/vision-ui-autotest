"""App 自动化核心引擎（对标 core/，纯 ADB 路线）。

分层：
adb_bridge.py  ADB 命令封装（设备/截图/触控/按键/输入/App 管理/UI dump）
driver.py      AppDriver：设备句柄 + 便捷操作（基于 adb_bridge）
vision.py      截图压缩 + VLM 视觉定位/断言（复用 core.llm_client.get_client）
self_healing.py 三层定位（Store→XML 语义→VLM）+ 知识库回写
planner.py     App 动作集 function call（NL 步骤解析）
app_runner.py  run_app_case 执行编排（投屏 + 步骤循环 + emit 事件）

定位引擎：纯 ADB（adb uiautomator dump 的 XML 做语义定位，adb input 触控），
不引入 Appium/Node 依赖，与投屏/App 管理统一技术栈。
大模型：完全复用 core/llm_client.get_client()，由平台「大模型配置」页面统一管理。
"""
