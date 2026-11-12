"""App 自动化核心引擎（对标 core/，DeviceDriver 抽象：android=ADB / ios=Appium）。

分层：
drivers/
  base.py        DeviceDriver 统一契约 + UiNode 统一节点模型 + 逻辑键名
  android/       ADB 实现（纯 stdlib：adb.py + ui_parser.py + driver.py）
  ios/           Appium(XCUITest) 实现（appium 客户端 + ui_parser.py + driver.py）
vision.py       截图压缩 + VLM 视觉定位/断言（复用 core.llm_client.get_client）
self_healing.py 三层定位（Store→语义→VLM）+ 知识库回写（app_locator 表，按平台/环境隔离）
planner.py      App 动作集 function call（NL 步骤解析，平台感知）
app_runner.py   run_app_case 执行编排（环境解析 + 驱动工厂 + 投屏 + 步骤循环 + emit 事件）

定位引擎：控件树统一归一化为 UiNode 后做语义定位，触控走各平台驱动；
Android 不引入 Appium/Node（纯 ADB），iOS 走 Appium Server + WebDriverAgent。
大模型：完全复用 core/llm_client.get_client()，由平台「大模型配置」页面统一管理。
与 WEB 引擎（core/）互不依赖：仅共享 llm_client / crypto 基础设施与 config.yaml 读取。
"""
