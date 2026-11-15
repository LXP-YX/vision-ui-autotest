# vision-ui-autotest

**自然语言驱动、视觉理解、脚本自愈**的 UI 自动化测试引擎与可视化测试平台。以 **Playwright** 为执行基座，融合多模态大模型（VLM，OpenAI 兼容接口）实现中文步骤 → 可执行动作，并在其上构建可视化平台：网页维护用例、一键执行、无头运行 + 实时投屏观看。

> 这是一个纯测试客户端，仓库里没有被测应用的源码；测试驱动的是你在配置/平台中指定的任意 Web 站点（建议使用你的测试环境）。

## 功能特性

- **自然语言用例**：中文步骤描述（如 `登录系统`、`进入 系统设置 → 用户管理`、`确认页面正常无报错`），AI Planner 解析为可执行动作；LLM 不可用自动回退规则式解析
- **视觉理解（VLM）**：截图交给多模态大模型做视觉定位与视觉断言；任意 OpenAI 兼容 VLM 端点均可接入
- **脚本自愈**：三级定位（知识库命中 → 语义定位 → VLM 视觉重定位），成功自动回写知识库；三级全失败判为功能缺陷，不伪造通过
- **可视化平台**：用例管理、执行记录、测试环境（多套可切换）、大模型配置、自愈知识库、测试计划
- **实时投屏**：无头 Chromium + CDP `Page.startScreencast`，云端无物理屏也能实时观看执行过程
- **AI 生成用例**：需求描述 / 页面探索 / 操作录制三种方式生成用例草稿（人工审核后生效）
- **App 自动化（纯 ADB）**：可选的 Android 测试路线，与 Web 端共用平台与大模型配置
- **外部接口**：`POST /api/execution-templates/{id}/run-sync` 供 CI 同步触发测试计划并返回结果

## 目录结构

```
vision-ui-autotest/
├── core/                 Web 执行内核（Playwright 封装，被 pytest 与平台共用）
│   ├── config.py         读 config.yaml + ${ENV} 展开（DB 不可用回退 config.yaml）
│   ├── browser.py        浏览器生命周期（有头/无头）
│   ├── base_page.py      等待 / 交互(JS兜底) / UI 健康检查 基类
│   ├── login_page.py     登录页对象
│   ├── main_page.py      主框架 / 导航对象
│   ├── llm_client.py     OpenAI 兼容大模型客户端（stdlib 实现）
│   ├── vision.py         VLM 视觉定位 / 视觉断言
│   ├── self_healing.py   三级定位 + 知识库回写
│   └── crypto.py         API Key 加密落库（Fernet）
├── core_app/             App 执行内核（可选，纯 ADB 路线）
├── platform/
│   ├── backend/          FastAPI + SQLAlchemy（REST + WebSocket，MySQL/SQLite）
│   ├── frontend/         Vue3 + Vite4 + Element Plus
│   └── worker/           执行 + CDP 投屏 + AI Planner + 探索/录制/脚本编译
├── main.py              一键启动器（开发 / 生产模式）
├── config.example.yaml   配置模板（复制为 config.yaml 使用）
├── run_login.py          无头登录冒烟演示脚本
└── pytest.ini / conftest.py
```

## 环境要求

- **Python**：3.10+（推荐 3.12/3.13）
- **Node.js**：16.20+（前端锁 Vite 4 / Vue 3.4 / Element Plus 2 版本线；Vite 5 需 Node 18+，暂未升级）
- **Chromium**：Playwright 管理，缺省时执行 `python -m playwright install chromium`

### 首次准备

```bash
# 1) Python 依赖
pip install -r requirements.txt
#   （Homebrew 等 PEP 668 管控环境：pip install --break-system-packages -r requirements.txt）

# 2) 前端依赖
cd platform/frontend && npm install && cd ../..

# 3) 浏览器
python3 -m playwright install chromium

# 4) 配置
cp config.example.yaml config.yaml   # 然后按你的被测站点填写
```

## 快速开始

### A. 只验证核心引擎（登录冒烟）

```bash
python3 run_login.py                  # 无头登录（默认，不弹窗口）
PW_HEADLESS=0 python3 run_login.py    # 临时有头调试
python3 -m pytest                     # pytest 登录冒烟
```

引擎单机模式无需启动平台：`config.yaml` 的 `site` / `account` 段提供站点与账号（平台 DB 可用时优先用平台「测试环境管理」维护的环境）。

### B. 启动可视化测试平台

```bash
# 开发模式：后端 127.0.0.1:8077 + 前端 127.0.0.1:5177（热更新，浏览器打开 5177）
python3 main.py

# 生产模式：构建前端后单进程单端口托管（默认 80，特权端口需 sudo）
sudo python3 main.py --prod
```

打开平台页面后：左侧维护用例 → 点「执行」→ 右侧 `<canvas>` 实时观看浏览器操作过程与逐步骤结果；执行失败自动截图。

## 配置说明

所有可变项集中在 `config.yaml`（模板见 `config.example.yaml`，**该文件已 gitignore，含你的站点地址与账号，请勿提交**）：

```yaml
browser:
  headless: true                  # 全局无头；PW_HEADLESS 环境变量可覆盖
login_ready_menus: ["首页", "系统设置"]   # 登录成功判据（按被测站点实际菜单改）
api:
  token_storage_key: "token"      # 被测站点登录后 token 在 localStorage 的键名
site:                             # 单机模式回退用；平台模式下由 DB 提供
  base_url: "https://your-site.example.com/login"   # 打开即用的地址（一般填登录页）
account:
  username: "${SITE_USERNAME}"   # 敏感值走环境变量注入
  password: "${SITE_PASSWORD}"
database:
  enabled: false                  # true 用 MySQL，失败/关闭回退本地 SQLite
  host: "${DB_HOST}"
  name: "vision_ui_autotest"
  password: "${DB_PASSWORD}"
```

- 大模型配置优先级：**平台「大模型配置」页面的默认配置（DB，API Key 加密落库）> 环境变量**；在页面填 base_url / api_key / 模型名（任意 OpenAI 兼容 VLM，需支持 function call 与图像输入）。

## 密钥与加密

### 工作原理

- **API Key 加密落库**：Fernet（AES-128-CBC + HMAC）对称加密，密文带 `enc:v1:` 前缀。
- **加密只用一个主密钥**（不是公钥/私钥对）：同一把主密钥既加密也解密，因为平台必须还原明文才能去调用大模型接口。
- **主密钥与数据库分离存放**：数据库里只有密文，只拿到数据库无法还原 API Key；主密钥不入库、不进代码、不上 Git。

### 主密钥文件

- 路径：**`secrets/master.key`**（单个文件，内容就是一行主密钥字符串，无换行也可）。
- 该目录已在 `.gitignore`：`secrets/`、`*.key`、`.env` 均被忽略，`git status` 看不到，不会被提交。
- 权限建议 `600`（仅本人可读），目录建议 `700`。
- 已为本仓库生成本地主密钥，内容：
  `cEfiTL4ynya9TIixI0aZskDAmp4mCaYCvzN4l52PUGkxCgi5TqPRBndGx0D2aRPl`
  **请把它备份到密码管理器**；一旦丢失，已落库的大模型 API Key 将无法解密，需要在页面重新录入。

### 生成或更换主密钥

```bash
mkdir -p secrets && chmod 700 secrets
python3 -c "import secrets; open('secrets/master.key','w').write(secrets.token_urlsafe(48))"
chmod 600 secrets/master.key
```

> 更换主密钥会让旧密文全部失效（表现为页面「未录入 API Key」、连接测试失败），需在「大模型配置」页面重新保存一次 API Key。

### 启动时如何加载

`python3 main.py` 启动时按以下顺序获取主密钥，**不会自动生成**（避免换机后静默生成新密钥导致旧密文解不开）：

1. 环境变量 `AUTOTEST_SECRET_KEY`（如容器/CI 注入）；
2. 项目根 `secrets/master.key` 文件。

两者都找不到时，启动器打印生成指引并中止启动。

也可以手动导出（优先级最高）：

```bash
export AUTOTEST_SECRET_KEY="$(cat secrets/master.key)"
python3 main.py
```

### 多机 / 团队共享

- 共用同一个数据库的多台机器**必须使用同一个主密钥**：把 `secrets/master.key` 通过安全渠道（密码管理器、密钥管理服务）分发，或各自注入相同的 `AUTOTEST_SECRET_KEY` 环境变量。
- **绝不通过 Git、聊天工具、邮件传输主密钥**；`.gitignore` 已兜底，但仍建议用密码管理器分发。
- 容器部署时用 Secret（k8s Secret / Docker secret）挂载文件或注入环境变量，不要 `COPY` 进镜像。

### 页面展示

- 列表默认掩码显示（如 `sk-sec****6789`），点小眼睛按需向后端请求该条明文；编辑框留空表示不修改 Key。
- 主密钥缺失或与密文不匹配时，保存接口返回明确错误，列表降级显示「未录入」而不是接口 500。
- 被测站点账号密码仅存在平台 `test_env` 表（本地 SQLite / 你的 MySQL），`config.yaml` 已 gitignore。

## 安全须知（部署前必读）

本平台面向**内网/本机**使用，默认**未开启访问鉴权**：

- REST API 与 WebSocket 均无认证，任何能访问端口的人均可查看/执行/删除数据；
- 请勿将平台端口直接暴露到公网；确需远程访问请套反向代理 + 认证（如 nginx + basic auth）；
- 截图接口已做目录穿越防护；CORS 默认放开（`allow_origins=["*"]`），公网部署请收敛。

## 外部接口：同步触发测试计划

供 CI / 流水线调用，同步阻塞到所有用例跑完再返回：

```
POST /api/execution-templates/{template_id}/run-sync?timeout=1800
```

返回体：`success`（全部通过才为 true）、`run_id`、`status`（passed/failed/error/timeout）、`summary`、`failed_cases` 等。CI 示例：

```bash
RESP=$(curl -s -X POST http://127.0.0.1:8077/api/execution-templates/1/run-sync)
echo "$RESP" | jq -e '.success' >/dev/null || { echo "$RESP" | jq '.failed_cases'; exit 1; }
```

## 工程约定

- **严禁 `time.sleep` 做同步**，一律用 Playwright 状态等待（`wait_for(state=...)`、`get_by_*` 自动等待、loading 消失 + DOM 稳定）
- **读配置，不硬编码**：URL / 账号 / 浏览器选项均来自 `config.yaml` 或平台 DB；敏感值用 `${ENV_VAR}` 注入
- **定位语义优先、框架无关**：首选 role/文本/label 定位，不依赖 hash 压缩类名；CSS 兜底同时兼容 Ant Design（`.ant-*`）与 Element UI（`.el-*`）
- **导航必须校验到达**：点击菜单后用面包屑校验真到目标页，避免假通过
- **默认只读**：用例只查看/搜索/导出，不提交表单、不新建/删除数据
- **会话级登录**：每会话登录一次、cookies 复用；登录成功以顶部导航可见为判据
- **失败自动截图**；数据库现有字段只增不改；用 `@pytest.mark.order(n)` 控制用例顺序

## 用例步骤写法（6 类可执行动作）

| 动作 | 写法示例 |
|---|---|
| login | `登录系统` |
| navigate | `进入 一级菜单 → 二级菜单` |
| click_element | `点击 搜索按钮` |
| assert_visual | `确认列表中展示了数据`（VLM 视觉断言） |
| check_health | `确认页面正常无报错`（纯 DOM 健康检查） |
| api_request | `调用接口 GET /api/xxx`（自动带登录 token） |

## License

[MIT](LICENSE)
