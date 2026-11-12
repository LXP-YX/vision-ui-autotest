# CLAUDE.md

This file provides guidance to AI coding agents (Claude Code / others) when working with code in this repository.

# 用户级全局约束

## 回复语言
所有回答必须使用中文（简体），代码和代码注释保持英文。
## 项目要求
- 提问优先，切勿假设。遇到任何不明确的需求、架构或意图时，必须给出选择先向用户确认，禁止在未澄清的情况下编写任何代码或输出内容。
- 始终采用最简单的可行方案。仅实现用户明确要求的功能，禁止添加未经请求的抽象、扩展或灵活性。
- 严格限定修改范围。仅处理与当前任务直接相关的文件或函数，禁止修改任何无关代码，即使你认为其有待优化。
- 主动暴露不确定性。若对技术方案或实现细节存在疑虑，必须在执行前明确声明，以避免因盲目自信导致严重错误。
- 以上不确定性问题人工确定好后可以直接执行操作，不用再询问
- 不得影响其他功能，如有必要可以对数据库进行修改，但是针对数据库现有字段只能增加不准修改现有字段，不能影响账户相关数据，需要执行sql脚本发给我手动执行，手动执行完再通知你
- 本项目业务逻辑改动后及时更新本文件

## 项目概述

**vision-ui-autotest**：自然语言驱动、视觉理解、脚本自愈的 UI 自动化测试引擎 + 可视化测试平台。纯测试客户端，仓库里没有被测应用源码；被测站点由平台「测试环境管理」页面维护的环境（DB，优先），DB 不可用时回退 `config.yaml`（单机模式）。

**配置加载优先级**：
1. 测试数据（base_url、account）：平台 DB 的默认测试环境 > `config.yaml` 的 `site`/`account` 段
   - `base_url` 是「打开即用」的完整站点地址（一般填登录页）；登录/录制/执行均直接打开它，
     独立的登录路径（`login_path` / `test_env.login_path`）已废弃，不再被代码读取
2. 大模型配置：平台 DB 的默认 LLMConfig > 环境变量
3. 浏览器与基础配置：`config.yaml`

## 代码结构

| 目录 | 角色 |
|------|------|
| `core/` | **Web 执行内核**（Playwright 封装）：浏览器生命周期、页面对象、状态等待、UI 健康检查、大模型客户端、VLM 视觉能力、脚本自愈。被 pytest 与平台 Worker 共同复用。 |
| `core_app/` | **App 执行内核**（可选，纯 ADB 路线）：`adb_bridge.py`、`driver.py`、`vision.py`、`self_healing.py`、`planner.py`、`app_runner.py`。不引入 Appium/Node；大模型复用 `core/llm_client.get_client()`。 |
| `conftest.py` / `pytest.ini` | pytest 基础设施：会话级登录复用（context 预登录、cookies 跨用例共享）+ 失败自动截图。`tests/` 用例目录已移除，用例主要在平台维护。 |
| `platform/` | **可视化测试平台**：`backend/`（FastAPI + DB）、`backend/app/`（App REST 路由 + 投屏 WS）、`frontend/`（Vue3 + Vite4 + Element Plus + vue-router）、`worker/`（执行 + CDP 投屏 + AI Planner + 页面探索/录制/脚本编译）。 |
| `start.py` | 一键启动器（开发/生产模式）。 |
| `config.yaml` | **唯一配置源**（已 gitignore，模板见 `config.example.yaml`）：浏览器、登录判据、`site`/`account`（单机回退）、`database` 段。 |
| `run_login.py` | 无头登录冒烟演示脚本。 |

`core/` 分层：`config.py`（读配置 + `${ENV}` 展开；测试数据优先读平台 DB 默认环境，DB 不可用回退 config.yaml 的 site/account 段）→ `browser.py` → `base_page.py` → `login_page.py` / `main_page.py`；`llm_client.py`；`vision.py`；`self_healing.py`；`crypto.py`。

**配置加载优先级**：
- 测试数据（base_url、account）：平台 DB 的默认测试环境 > `config.yaml` 的 `site`/`account` 段
  （`base_url` 即打开地址，登录路径已废弃；旧 config.yaml 里的 `site.login_path` 仅作为兼容，
  由 `config.site_open_url()` 一次性拼到 base_url 尾部）
- 大模型配置：平台 DB 的默认 LLMConfig > 环境变量
- 浏览器与基础配置：`config.yaml`

## 数据库

- 连接读自 `config.yaml` 的 `database` 段：`enabled: true` 用 **MySQL**（默认库名 `vision_ui_autotest`），否则/连接失败**回退本地 SQLite**（`platform/platform.db`）。
- 核心表：`test_case` / `test_run` / `llm_config` / `locator` / `test_env` 等（见 `platform/backend/schema.sql`；应用启动时 `create_all` 自动建）。
- `test_env.login_path` 已废弃：列保留不用（旧库该列 NOT NULL，删映射会让新建环境写库失败），页面/接口/模型输出均不再暴露。
- 约定：**现有字段只增不改**，不含账户敏感数据。DB 变更需人工执行 SQL 脚本（`platform/backend/migrations/`）。

## 大模型接入 & VLM

- 任意 **OpenAI 兼容**端点（需支持 function call 与多模态图像输入），在平台「大模型配置」页面维护；`get_client()` 读 DB 默认配置。
- **AI Planner**（`platform/worker/planner.py`）：function call 把中文步骤解析为 6 类动作（login / navigate / click_element / assert_visual / check_health / wait），另有 `api_scenario` 动作由规则式解析（`runner.py` 的 `_interpret_step`）产生，LLM 不可用时整体回退规则式。
- **接口测试引擎**（`worker/api_runner.py`）：纯 HTTP（requests）不起浏览器；「场景」= 前置 + 主步骤，每步引用接口库的 `api_id`，变量上下文 ctx 跨步骤共享（`$.data.token` 式提取器 + `${var}` 替换；内置 `${TODAY}` / `${YESTERDAY}` 等日期变量）。UI 用例里以 `api_scenario` 动作嵌入场景，登录后的 token 存入 `ctx['token']` 可被场景引用。
- **接口请求 + token 全局变量**：登录步骤后自动从 localStorage（键名 `config.yaml` 的 `api.token_storage_key`）提取 token 存入 `ctx['token']`，供接口场景 `${token}` 变量替换。
- **AI 生成用例**（`platform/backend/generator.py`）：需求描述 → function call 生成结构化草稿（`status=draft`），人工审核后 `active`。
- **页面探索生成**（`worker/explorer.py`）与**录制生成**（`worker/recorder.py`）：真实打开页面/录制操作后由 AI 翻译成中文用例草稿。
- **脚本编译**（`worker/script_compiler.py`）：把中文步骤编译为完整 Playwright Python 脚本；生成脚本中的登录判据选择器与 token 键名通过环境变量 `PW_LOGIN_READY_SEL` / `PW_TOKEN_KEY` 由 runner 注入（生成脚本内用 `LOGIN_READY_SEL` / `TOKEN_KEY` 变量，勿硬编码菜单文字）。
- **VLM 视觉能力**（`core/vision.py`）：`locate()` 截图→坐标→`elementFromPoint` 反查→稳定选择器；`assert_visual()` 视觉断言。截图缩放 + JPEG 压缩降 token。
- **脚本自愈**（`core/self_healing.py`）：`find_element()` 三级定位（Store 命中 → 语义 `:text-is(...):visible` → VLM），成功回写 `locator` 表；全失败返回 None——**自愈 ≠ 掩盖 Bug**。
- **加密**（`core/crypto.py`）：API Key 经 Fernet 加密落库；**主密钥来自环境变量 `AUTOTEST_SECRET_KEY`**，代码内无固定密钥；密文带 `enc:v1:` 前缀；decrypt 不兼容无前缀明文。

## APP 自动化（可选，纯 ADB 路线）

与 Web 自动化架构对称，复用同一平台与大模型配置入口；差异仅在引擎层与投屏来源（`adb exec-out screencap` 采集，帧率受 adb 传输限制 ≈1 FPS）。
- **三层定位自愈**：locator 表 → UI XML 语义匹配 → VLM 截图定位。
- **运行要求**：需 `adb` 在 PATH 且已连接设备。
- **适用场景**：Android 原生应用测试，无需 Appium/Node 环境依赖。

## 常用命令

依赖装在当前 Python 环境（`pip install -r requirements.txt`；PEP 668 管控环境加 `--break-system-packages`），无需虚拟环境。

```bash
# --- 核心引擎 ---
python3 run_login.py                             # 无头登录冒烟
PW_HEADLESS=0 python3 run_login.py               # 临时有头调试
python3 -m pytest                                # 跑全部用例（tests/ 为空时跳过，用例主要在平台维护）
python3 -m pytest "tests/test_xxx.py::TestClass::test_name" -v   # 单用例（有用例时）

# --- 测试平台 ---
python3 start.py                                 # 开发模式：后端8077 + 前端5177(热更新)
sudo python3 start.py --prod                     # 生产模式：单进程单端口(80)
cd platform/frontend && npm install              # 首次装前端依赖
python3 -m playwright install chromium           # 首次装浏览器
cd platform/frontend && npm run build            # 前端单独构建（提交前自查项）

# --- 前端开发 ---
cd platform/frontend && npm run dev              # 启动 Vite 开发服务器（热更新，端口 5173）
```

- 无头模式由环境变量 **`PW_HEADLESS`** 控制（`1`=无头，`0`=有头），未设置时取 `config.yaml` 的 `browser.headless`。
- 平台点「执行」即无头运行 + CDP 实时投屏（无头也能投屏），本地不弹浏览器窗口。

## 平台架构（platform/）

- **五层**：Vue 前端 → FastAPI（REST + WebSocket）→ Worker（调 `core/` 引擎跑用例）→ Chromium → 被测站点。
- **同步 Playwright 跑后台线程**：同步 API 不能进 asyncio 事件循环，Worker 起后台线程，事件经线程安全 Queue 桥接给 WebSocket（`run_manager.py`）。
- **启动器进程组**：`start.py` 用 `start_new_session=True` 让子进程独立成组，Ctrl+C 时 `os.killpg` 整组杀；开发模式先探测后端端口就绪再起前端（避免 vite 代理 ECONNREFUSED）。
- **前端锁 Node 16 兼容版本线**：Vite 4.5 + Vue 3.4 + Element Plus 2（Vite 5 要 Node 18+）。
- **三个执行入口、三套 manager**：单用例（`run_manager.py`，UI 用例）、接口场景（`api_run_manager.py`）、测试计划（`execution_manager.py`，批量 + `run-sync` 同步接口供 CI 调用）。UI 执行与接口场景执行均后台线程，事件经线程安全 Queue 桥接给 WebSocket。
- **录制功能门控**：`start.py` 给后端注入 `RECORDING_ENABLED=1`（需真实有头浏览器）；云端部署不设该变量则录制接口 403 + 前端隐藏入口。
- **安全**：REST/WS 默认无鉴权，仅限内网/本机部署；CORS 全开（`allow_origins=["*"]`）；截图接口已防路径穿越。
- **执行模板**：`execution_template` 表编排批量用例执行，支持超时、失败策略；`execution_run` 表记录执行结果；API `POST /api/execution-templates/{id}/run-sync` 供 CI 同步触发并阻塞等待结果。

## 关键约定

- **严禁用 `time.sleep` 做同步。** 一律状态等待。
- **注释和 docstring 一律中文**；代码与标识符英文。
- **读配置，不硬编码。** URL、账号、菜单名、token 键名都来自 `config.yaml` 或平台 DB；敏感值可用 `${ENV_VAR}` 注入。
- **定位语义优先，框架无关。** 顶部导航用 `:text-is('菜单名'):visible` 命中可见节点，不依赖 hash 压缩类名。二级菜单在 `aside` 侧边栏内。
- **导航必须校验到达。** 点击后用面包屑（`.ant-breadcrumb` / `.el-breadcrumb`）校验，到不了抛错。
- **默认只读，避免破坏性操作。** 不提交表单、不新建/删除数据。
- **会话级登录。** 登录成功以顶部导航可见为判据，而非 sleep。
- **失败自动截图。**（`screenshots/` 已 gitignore）
- **用例执行顺序**：用 `@pytest.mark.order(n)` 控制执行顺序（marker 已在 `pytest.ini` 注册）。
- **前端热更新**：开发模式启动后，前端文件修改自动刷新；需重启 Vite 才能加载新依赖。
- **不得向仓库提交任何真实账号、内网地址、密钥**：`config.yaml` 已 gitignore；新增配置项模板同步维护 `config.example.yaml`。
