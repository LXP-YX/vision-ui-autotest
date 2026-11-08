"""AI 脚本编译器：把用例的中文步骤列表编译为完整的 Playwright Python 脚本。

设计原则：
- LLM 负责把中文步骤翻译为可执行的 Playwright 代码，含精确 expect() 断言；
- 脚本通过 stdout JSON 行回传执行事件（step/frame/done/log），与 runner 解耦；
- 配置从环境变量读取，不硬编码；
- LLM 不可用时返回 None，调用方回退自然语言执行。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm_client import LLMError, get_client

# 脚本文件开头的固定样板（不依赖 LLM，直接内嵌）
_SCRIPT_HEADER = '''\
import sys
import json
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

def emit(kind, **kw):
    print(json.dumps({"kind": kind, **kw}, ensure_ascii=False), flush=True)

# 模块级当前 page 引用，供 _run_api_scenario 等辅助函数回退截帧使用
_current_page = None

BASE_URL = os.environ.get("PW_BASE_URL", "").rstrip("/")
USERNAME  = os.environ.get("PW_USERNAME", "")
PASSWORD  = os.environ.get("PW_PASSWORD", "")
# 登录就绪判据选择器（由父进程按 config.login_ready_menus 生成后注入）
LOGIN_READY_SEL = os.environ.get("PW_LOGIN_READY_SEL", ":text-is('首页'):visible")
# localStorage 中 token 的键名（config.api.token_storage_key）
TOKEN_KEY = os.environ.get("PW_TOKEN_KEY", "token")

_FULLSCREEN_MOCK_JS = """
(function () {
  if (window.__fullscreen_mocked) return;
  window.__fullscreen_mocked = true;
  const _fake = function () {
    const el = this;
    Object.defineProperty(document, 'fullscreenElement', { get: () => el, configurable: true });
    el.dispatchEvent(new Event('fullscreenchange', { bubbles: true }));
    document.dispatchEvent(new Event('fullscreenchange', { bubbles: true }));
    return Promise.resolve();
  };
  Element.prototype.requestFullscreen = _fake;
  Element.prototype.webkitRequestFullscreen = _fake;
  Element.prototype.mozRequestFullScreen = _fake;
  Element.prototype.msRequestFullscreen = _fake;
  document.exitFullscreen = function () {
    Object.defineProperty(document, 'fullscreenElement', { get: () => null, configurable: true });
    document.dispatchEvent(new Event('fullscreenchange', { bubbles: true }));
    return Promise.resolve();
  };
})();
"""

# 让 worker / backend 目录可 import，供接口场景调用使用
# PROJECT_ROOT 由父进程（runner.py）通过环境变量注入，确保临时脚本也能找到项目目录
_PROJECT_ROOT = os.environ.get("PW_PROJECT_ROOT", "")
if _PROJECT_ROOT:
    for _p in [
        _PROJECT_ROOT,
        str(Path(_PROJECT_ROOT) / "platform" / "worker"),
        str(Path(_PROJECT_ROOT) / "platform" / "backend"),
    ]:
        if _p not in sys.path:
            sys.path.insert(0, _p)
try:
    import api_runner as _api_runner  # noqa: F401
except ImportError:
    pass


def _run_api_scenario(scenario_id: int, api_ctx: dict, page=None) -> None:
    """在脚本进程内直接调用 api_runner.run_scenario 执行接口场景。

    - 复用接口管理里设计的完整场景（接口调用顺序、断言、变量提取）；
    - api_ctx 双向共享：登录 token 等变量传入，场景提取的新变量写回供后续步骤使用；
    - 任一接口断言失败 → run_scenario 返回 status!=passed → 抛 AssertionError，
      脚本进程以非 0 退出码结束，runner 捕获 done(failed) 触发 NL 重跑自愈。
    - page：可选，传入时在每步结束和断言重试等待期间截帧推送，保证实时视图不断流。
      未传时自动回退读取模块级 _current_page（兼容旧编译脚本）。
    """
    from db import ApiScenario, SessionLocal  # type: ignore
    from api_runner import run_scenario  # type: ignore

    with SessionLocal() as s:
        sc = s.get(ApiScenario, scenario_id)
        if not sc:
            raise AssertionError(f"接口场景不存在：id={scenario_id}")
        scenario = sc.to_dict()

    scenario_name = scenario.get("name") or f"scenario#{scenario_id}"

    # page 未显式传入时回退读模块级引用（兼容旧编译脚本）
    _page = page if page is not None else _current_page

    def _frame_cb():
        if _page is None:
            return
        try:
            import base64 as _b64
            _jpg = _page.screenshot(full_page=False, type="jpeg", quality=60)
            emit("frame", data=_b64.b64encode(_jpg).decode())
        except Exception:
            pass

    def _inner_emit(kind, payload):
        if kind == "step":
            msg = f"[接口场景] {payload.get('name','')} → {payload.get('state','')}"
            if payload.get("detail"):
                msg += f" — {payload['detail']}"
            emit("log", msg=msg)
            # 每步结束（passed/failed）时补一帧，保证视图反馈
            if payload.get("state") in ("passed", "failed"):
                _frame_cb()
        elif kind == "api":
            emit("log", msg=f"[接口] {payload.get('method','')} {payload.get('url','')} → {payload.get('status','')}")
            _frame_cb()

    result = run_scenario(scenario, _inner_emit, ctx=api_ctx, frame_cb=_frame_cb)
    if result.get("status") != "passed":
        raise AssertionError(
            f"接口场景「{scenario_name}」未通过：{result.get('error') or '断言失败'}"
        )


def _setup_screencast(context, page):
    """开启 CDP 投屏，每帧以 base64 JPEG 通过 emit('frame') 上抛。"""
    global _current_page
    _current_page = page  # 让 _run_api_scenario 等辅助函数可回退截帧
    cdp = context.new_cdp_session(page)
    def _on(p):
        emit("frame", data=p["data"])
        try:
            cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
        except Exception:
            pass
    cdp.on("Page.screencastFrame", _on)
    context.add_init_script(_FULLSCREEN_MOCK_JS)
    cdp.send("Page.startScreencast", {
        "format": "jpeg", "quality": 60,
        "maxWidth": 1440, "maxHeight": 900, "everyNthFrame": 1,
    })
    return cdp

'''

_SCRIPT_FOOTER = '''\

try:
    run()
except Exception as _e:
    emit("done", status="failed", error=f"{type(_e).__name__}: {_e}")
    sys.exit(1)
'''

# System prompt：告诉 LLM 生成脚本的规范
_SYSTEM = """\
你是一个 Playwright UI 自动化测试脚本生成器。用户给你一条测试用例的中文步骤列表，你需要生成一个完整的 `def run():` 函数（包含 `def run():` 这一行本身）。

## 约束与规范

### 输出格式
- **只输出 `def run():` 函数的完整定义**（从 `def run():` 开始，到函数结束）
- 不要输出 import、emit 定义、BASE_URL 等——这些已在外部定义好
- 不要用 markdown 代码块（``` 等），直接输出 Python 代码
- 函数内最后一行必须是 `    emit("done", status="passed", error="")`（4 空格缩进）

### 可用变量（已在模块顶层定义）
- `BASE_URL`：被测站点根地址（已去除尾部 /）
- `USERNAME`、`PASSWORD`：登录账号
- `LOGIN_READY_SEL`：登录就绪判据选择器（登录步骤断言必须用它，不要自造菜单文字）
- `TOKEN_KEY`：localStorage 中 token 的键名
- `emit(kind, **kw)`：输出事件（step/log/done）
- `_setup_screencast(context, page)`：开启 CDP 投屏

### 每个步骤的代码结构（4 空格缩进为函数体一级）
```python
def run():
    emit("step", index=0, text="步骤文本", state="running")
    time.sleep(1)  # 等待页面/弹框加载完成
    # ... 执行代码（8 空格，在 with 块内）...
    # expect() 断言
    emit("step", index=0, text="步骤文本", state="passed", detail="描述")
```

### 断言规范（重要）
每个步骤执行后必须有至少一条 `expect()` 断言，根据步骤语义选择：
- 登录步骤：`expect(page.locator(LOGIN_READY_SEL).first).to_be_visible()`
- 导航步骤（进入 X → Y）：`expect(page.locator(".ant-breadcrumb, .el-breadcrumb")).to_contain_text("Y")`
- 点击按钮/操作后：根据上下文推断——若打开弹窗则断言弹窗可见，若触发数据加载则断言列表非空
- 确认页面显示/有数据：`expect(page.locator("table .ant-table-row, .el-table__row")).not_to_have_count(0)` 或 `expect(page.locator("...")).to_be_visible()`
- 健康检查：`expect(page.locator(".ant-result-error, .el-result--error")).not_to_be_attached()`

### 选择器优先级（从高到低）
1. `page.get_by_role("button", name="XXX")` / `page.get_by_text("XXX")`
2. `:text-is('XXX'):visible`（用于菜单/按钮文本）
3. `.ant-*` / `.el-*` CSS 类（框架级通用选择器）
4. 最后才考虑具体 CSS 属性选择器

**禁止使用** `input:not([type='password'])` — 该选择器会匹配到 radio/checkbox 等不可见输入框导致 timeout。
用户名输入框必须用 `input[placeholder*='用户名'], input[placeholder*='账号'], input[type='text']` 并配合 `.first` 和 `wait_for_selector` 确保可见。

### 导航规范
- 顶部一级菜单：`page.locator(":text-is('菜单名'):visible").first.click()`
- 点菜单后等待二级菜单：`page.wait_for_selector("aside :text-is('二级菜单名'):visible", timeout=5000)`
- 二级菜单：`page.locator("aside :text-is('二级菜单名'):visible").first.click()`
- 导航完成后等 loading 消失：`page.wait_for_selector(".ant-spin, .el-loading-mask", state="hidden", timeout=10000)`（不存在 loading 时忽略异常）

### 超时设置
- `page.goto()`：`wait_until="domcontentloaded"`
- 关键等待：timeout=10000（10s），登录页等导航出现：timeout=15000
- 不要使用 `page.pause()` 或无意义的轮询，一律状态等待；每步开头的 `time.sleep(1)` 除外

### 完整模板
```python
def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        _setup_screencast(ctx, page)
        token = ""

        emit("step", index=0, text="登录系统", state="running")
        time.sleep(1)
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.wait_for_selector("input[placeholder*='用户名'], input[placeholder*='账号'], input[type='text']", timeout=15000)
        page.locator("input[placeholder*='用户名'], input[placeholder*='账号'], input[type='text']").first.fill(USERNAME)
        page.locator("input[placeholder*='密码'], input[type='password']").first.fill(PASSWORD)
        page.locator("button:has-text('登录'), button:has-text('登 录'), button[type='submit']").first.click()
        page.wait_for_selector(LOGIN_READY_SEL, timeout=15000)
        expect(page.locator(LOGIN_READY_SEL).first).to_be_visible()
        try:
            raw = page.evaluate("() => localStorage.getItem(TOKEN_KEY)")
            if raw:
                token = raw.strip().strip('"')
        except Exception:
            pass
        emit("step", index=0, text="登录系统", state="passed", detail="登录成功，导航菜单可见")

        # === 后续步骤按此格式继续... ===

        browser.close()
    emit("done", status="passed", error="")
```

注意：`browser.close()` 在 `with sync_playwright()` 块内（8 空格），`emit("done", ...)` 在 `with` 块外（4 空格）。

### 接口步骤（[接口场景 #N]）
若步骤文本形如 `[接口场景 #N]`，且在 scenarios 变量（dict，key 为 id 字符串）中有数据，
则用 `requests` 库串行执行该场景的接口调用，并用变量 `token` 设置 `Authorization` 头。

输出纯 Python 代码，不要包含任何 markdown 代码块标记（```python 等）。
"""


def _steps_to_text(steps: list) -> str:
    """把步骤列表规范化为带编号的纯文本，供 LLM 阅读。"""
    lines = []
    for i, s in enumerate(steps):
        if isinstance(s, dict):
            text = s.get("text", "")
            pars = s.get("parallel") or []
            line = f"{i}. {text}"
            for p in pars:
                line += f"\n   [并行接口场景 #{p.get('id')} {p.get('name','')}]"
        else:
            line = f"{i}. {s}"
        lines.append(line)
    return "\n".join(lines)


def _load_scenario_summaries(steps: list) -> dict:
    """从步骤列表里提取接口场景 ID，查 DB 返回 {id_str: 场景摘要}。

    DB 不可用时返回空 dict，LLM 会跳过场景内联（只生成注释占位）。
    """
    import re
    ids = set()
    for s in steps:
        text = s.get("text", s) if isinstance(s, dict) else s
        m = re.match(r"\s*\[\s*接口场景\s*#\s*(\d+)\s*\]", str(text))
        if m:
            ids.add(int(m.group(1)))
        if isinstance(s, dict):
            for p in (s.get("parallel") or []):
                if p.get("id"):
                    ids.add(int(p["id"]))
    if not ids:
        return {}
    try:
        from db import ApiScenario, SessionLocal  # type: ignore
        result = {}
        with SessionLocal() as sess:
            for sid in ids:
                sc = sess.get(ApiScenario, sid)
                if sc:
                    result[str(sid)] = sc.to_dict()
        return result
    except Exception:
        return {}


def compile_to_playwright(case: dict) -> Optional[str]:
    """把用例的中文步骤列表编译为完整的 Playwright Python 脚本。

    Args:
        case: {id, name, module, steps, ...}

    Returns:
        完整可执行的 Python 脚本字符串；LLM 不可用或编译失败时返回 None。
    """
    steps = case.get("steps") or []
    if not steps:
        return None

    name = case.get("name", "未命名用例")
    module = case.get("module", "")
    steps_text = _steps_to_text(steps)
    scenarios = _load_scenario_summaries(steps)

    user_msg_parts = [
        f"用例名：{name}",
        f"模块：{module}" if module else "",
        "",
        "步骤列表：",
        steps_text,
    ]
    if scenarios:
        user_msg_parts += [
            "",
            "涉及的接口场景数据（JSON）：",
            json.dumps(scenarios, ensure_ascii=False, indent=2),
        ]
    user_msg = "\n".join(p for p in user_msg_parts if p is not None)

    try:
        client = get_client()
        msg = client.chat(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            timeout=120,
        )
    except LLMError:
        return None

    body = ""
    if isinstance(msg, dict):
        # function call 响应或普通文本响应
        choices = msg.get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content") or ""
        else:
            content = msg.get("content") or ""
    else:
        content = str(msg)

    body = content.strip()
    # 去除 markdown 代码块标记
    if body.startswith("```python"):
        body = body[9:]
    elif body.startswith("```"):
        body = body[3:]
    if body.endswith("```"):
        body = body[:-3]
    body = body.strip()

    if not body:
        return None

    header_comment = f"# 用例: {name}"
    if module:
        header_comment += f" / 模块: {module}"

    # LLM 直接输出完整 def run(): 函数，不做缩进操纵，直接拼接
    script = f"{header_comment}\n{_SCRIPT_HEADER}\n{body}\n{_SCRIPT_FOOTER}"

    # 语法校验：有语法错误直接丢弃，不落库
    try:
        compile(script, "<generated>", "exec")
    except SyntaxError as e:
        import logging
        logging.warning("AI 编译脚本语法错误，丢弃：%s", e)
        return None

    return script


# ─────────────────────────────────────────────────────────────────────────────
# 从执行轨迹编译（主路径）
# ─────────────────────────────────────────────────────────────────────────────

_ASSERT_SYSTEM = """\
你是 Playwright 断言生成器。给你一条测试步骤的中文描述和该步骤完成后的页面截图（base64 JPEG），
生成 1-3 行 Playwright Python expect() 断言，直接可嵌入脚本。

规则：
- 只输出 Python 代码行，无注释、无 markdown 标记
- 每行以 expect( 开头
- 可用变量：page（Playwright Page 对象）
- 断言要覆盖步骤最核心的可见结果（优先断言业务内容而非通用元素）
- 选择器只能用 .ant-* / .el-* 类名、role、:text-is、[aria-label] 这类稳定选择器
"""


def _gen_assert_for_step(step_text: str, page_b64: str | None) -> str:
    """调 VLM 为单步生成 expect() 断言，失败时返回健康检查兜底断言。"""
    fallback = "    expect(page.locator(\".ant-result-error, .el-result--error\")).not_to_be_attached()"
    try:
        client = get_client()
        if page_b64:
            raw = client.ask_with_image(
                f"步骤描述：{step_text}\n请生成断言代码（只输出代码行，无注释）",
                page_b64,
            )
        else:
            msg = client.chat(
                messages=[
                    {"role": "system", "content": _ASSERT_SYSTEM},
                    {"role": "user", "content": f"步骤描述：{step_text}"},
                ],
                temperature=0.1,
                timeout=30,
            )
            choices = (msg or {}).get("choices") or []
            raw = (choices[0].get("message") or {}).get("content") or "" if choices else ""
    except Exception:
        return fallback

    lines = [ln for ln in (raw or "").splitlines() if ln.strip().startswith("expect(")]
    if not lines:
        return fallback
    # 保证每行有 4 空格缩进
    return "\n".join("    " + ln.lstrip() for ln in lines)


def _trace_entry_to_code(entry: dict, idx: int) -> str:
    """把一条执行轨迹条目翻译为 Playwright Python 代码片段（不含 emit 包裹）。

    selector 直接取轨迹里三级定位命中的值，不猜。
    """
    action = entry.get("action") or "skip"
    args = entry.get("args") or {}
    selector = entry.get("selector") or ""
    step_text = entry.get("step", "")

    if action == "login":
        return (
            "        page.goto(f\"{BASE_URL}/login\", wait_until=\"domcontentloaded\")\n"
            "        page.wait_for_selector(\"input[placeholder*='用户名'], input[placeholder*='账号'], input[type='text']\", timeout=15000)\n"
            "        page.locator(\"input[placeholder*='用户名'], input[placeholder*='账号'], input[type='text']\").first.fill(USERNAME)\n"
            "        page.locator(\"input[placeholder*='密码'], input[type='password']\").first.fill(PASSWORD)\n"
            "        page.locator(\"button:has-text('登录'), button:has-text('登 录'), button[type='submit']\").first.click()\n"
            "        page.wait_for_selector(LOGIN_READY_SEL, timeout=15000)\n"
            "        expect(page.locator(LOGIN_READY_SEL).first).to_be_visible()\n"
            "        try:\n"
            "            raw = page.evaluate(\"() => localStorage.getItem(TOKEN_KEY)\")\n"
            "            if raw:\n"
            "                token = raw.strip().strip('\"')\n"
            "        except Exception:\n"
            "            pass"
        )

    if action == "navigate":
        top = args.get("top", "")
        sub = args.get("sub", "")
        return (
            f"        page.locator(\":text-is('{top}'):visible\").first.click()\n"
            f"        page.wait_for_selector(\"aside :text-is('{sub}'):visible\", timeout=5000)\n"
            f"        page.locator(\"aside :text-is('{sub}'):visible\").first.click()\n"
            "        try:\n"
            "            page.wait_for_selector(\".ant-spin, .el-loading-mask\", state=\"hidden\", timeout=10000)\n"
            "        except Exception:\n"
            "            pass\n"
            f"        expect(page.locator(\".ant-breadcrumb, .el-breadcrumb\")).to_contain_text(\"{sub}\")"
        )

    if action == "check_health":
        return (
            "        expect(page.locator(\".ant-result-error, .el-result--error\")).not_to_be_attached()\n"
            "        expect(page.locator(\"main, .ant-layout-content, .el-main, .ant-card, .el-card\").first).to_be_visible()"
        )

    if action == "click_element":
        target = args.get("target", step_text)
        new_tab_url = entry.get("new_tab_url", "")

        # 优先使用步骤级手动定位提示（locator_hint），比运行时碰巧命中的 selector 更可靠
        hint = entry.get("locator_hint")
        if hint and hint.get("type") and hint.get("value"):
            t = hint["type"].strip().lower()
            v = hint["value"].strip()
            _hint_map = {
                "xpath":       f"xpath={v}",
                "css":         v,
                "text":        f':text-is("{v}")',
                "hastext":     f':has-text("{v}")',
                "role":        f"role={v}",
                "label":       f"label={v}",
                "placeholder": f'[placeholder="{v}"]',
                "testid":      f'[data-testid="{v}"]',
                "id":          f"#{v}" if not v.startswith("#") else v,
                "name":        f'[name="{v}"]',
            }
            selector = _hint_map.get(t, v)

        if selector:
            sel_escaped = selector.replace("\\", "\\\\").replace('"', '\\"')
            click_line = (
                f"        page.locator(\"{sel_escaped}\").first.wait_for(state=\"visible\", timeout=8000)\n"
            )
            if new_tab_url:
                # 此点击会打开新标签页：用 expect_popup() 捕获，之后把 page 变量指向新页
                return (
                    click_line +
                    "        with page.expect_popup() as _popup_info:\n"
                    f"            page.locator(\"{sel_escaped}\").first.click()\n"
                    "        page = _popup_info.value\n"
                    "        page.wait_for_load_state(\"domcontentloaded\", timeout=15000)\n"
                    "        _setup_screencast(ctx, page)\n"
                    "        try:\n"
                    "            page.wait_for_load_state(\"networkidle\", timeout=8000)\n"
                    "        except Exception:\n"
                    "            pass"
                )
            return (
                click_line +
                f"        page.locator(\"{sel_escaped}\").first.click()\n"
                "        try:\n"
                "            page.wait_for_load_state(\"networkidle\", timeout=5000)\n"
                "        except Exception:\n"
                "            pass"
            )
        # 没有 selector（跳过步骤）
        return f"        # [跳过] {target}（自然语言执行时未命中选择器）"

    if action == "assert_visual":
        claim = args.get("claim", step_text)
        # assert_visual 无 selector，返回占位注释——调用方会用 VLM 生成断言替换
        return f"        # [assert_visual] {claim}"

    if action == "api_scenario":
        sid = args.get("id")
        return (
            f"        _run_api_scenario({sid}, api_ctx, page=page)\n"
            f"        # 接口场景 #{sid} 已按接口管理定义执行（含断言），变量写回 api_ctx"
        )

    if action == "wait":
        seconds = float(args.get("seconds", 1))
        interval = 0.3
        return (
            f"        # 等待 {seconds:.0f} 秒，每 {interval}s 截帧保持实时视图不断流\n"
            f"        _wait_elapsed = 0.0\n"
            f"        while _wait_elapsed < {seconds}:\n"
            f"            time.sleep(min({interval}, {seconds} - _wait_elapsed))\n"
            f"            _wait_elapsed += {interval}\n"
            f"            try:\n"
            f"                import base64 as _b64\n"
            f"                _jpg = page.screenshot(full_page=False, type='jpeg', quality=60)\n"
            f"                emit('frame', data=_b64.b64encode(_jpg).decode())\n"
            f"            except Exception:\n"
            f"                pass"
        )

    # skip / unknown
    return f"        # [跳过] {step_text}"


def compile_from_trace(case: dict, trace: list[dict]) -> Optional[str]:
    """从自然语言执行轨迹编译 Playwright Python 脚本。

    trace 是 runner._execute_steps(record=True) 产出的 compiled 列表，每条包含：
      {step, action, args, selector?, strategy?, parallel?}

    编译策略：
    - login/navigate/check_health/click_element：模板化生成，selector 直接取轨迹值，不调 LLM；
    - assert_visual：调 VLM（ask_with_image / chat）生成精确 expect() 断言；
    - api_scenario：注释占位；
    - 每步用 emit("step") 包裹。

    Returns:
        完整可执行的 Python 脚本字符串；失败返回 None。
    """
    if not trace:
        return None

    name = case.get("name", "未命名用例")
    module = case.get("module", "")
    header_comment = f"# 用例: {name}"
    if module:
        header_comment += f" / 模块: {module}"
    header_comment += "\n# 编译来源: 执行轨迹（selector 来自三级定位真实命中）"

    body_lines = [
        "def run():",
        "    with sync_playwright() as pw:",
        "        browser = pw.chromium.launch(headless=True)",
        "        ctx = browser.new_context(viewport={\"width\": 1440, \"height\": 900})",
        "        page = ctx.new_page()",
        "        _setup_screencast(ctx, page)",
        "        token = \"\"",
        "        api_ctx = {}  # 接口场景变量上下文，跨步骤共享",
        "",
    ]

    for idx, entry in enumerate(trace):
        step_text = entry.get("step", f"步骤{idx}")
        action = entry.get("action") or "skip"
        parallels = entry.get("parallel") or []

        body_lines.append(f"        emit(\"step\", index={idx}, text={json.dumps(step_text, ensure_ascii=False)}, state=\"running\")")
        body_lines.append("        time.sleep(1)")

        code = _trace_entry_to_code(entry, idx)

        # assert_visual 特殊处理：调 LLM/VLM 生成断言
        if action == "assert_visual":
            assert_code = _gen_assert_for_step(step_text, None)
            body_lines.append(assert_code)
        else:
            body_lines.append(code)
            # click_element 后加无报错断言（如果代码里还没有 expect）
            if action == "click_element" and "expect(" not in code:
                body_lines.append("        expect(page.locator(\".ant-result-error, .el-result--error\")).not_to_be_attached()")

        # 登录步骤后把 token 同步到接口上下文
        if action == "login":
            body_lines.append("        if token:")
            body_lines.append("            api_ctx[\"token\"] = token")

        # parallel 并行接口场景：在 UI 步骤之后串行执行（脚本进程单线程，无并发）
        for p in parallels:
            sid = p.get("id")
            pname = p.get("name") or f"场景#{sid}"
            body_lines.append(f"        # 并行接口场景：{pname}")
            body_lines.append(f"        _run_api_scenario({sid}, api_ctx, page=page)")

        body_lines.append(f"        emit(\"step\", index={idx}, text={json.dumps(step_text, ensure_ascii=False)}, state=\"passed\", detail=\"完成\")")
        body_lines.append("")

    body_lines += [
        "        browser.close()",
        "    emit(\"done\", status=\"passed\", error=\"\")",
    ]

    body = "\n".join(body_lines)
    script = f"{header_comment}\n{_SCRIPT_HEADER}\n{body}\n{_SCRIPT_FOOTER}"

    try:
        compile(script, "<generated-from-trace>", "exec")
    except SyntaxError as e:
        import logging
        logging.warning("轨迹编译脚本语法错误，丢弃：%s", e)
        return None

    return script


# ─────────────────────────────────────────────────────────────────────────────
# 接口场景 patch（用例仅修改/删除/添加接口场景时，就地更新脚本，不重新编译）
# ─────────────────────────────────────────────────────────────────────────────

def patch_script_api_scenarios(
    script_py: str,
    old_steps: list,
    new_steps: list,
) -> Optional[str]:
    """在不重新编译脚本的前提下，就地修改脚本中各步骤的接口场景调用。

    调用时机：用例编辑时，自然语言文本完全未变，只有 parallel 接口场景列表发生了
    新增 / 删除 / 修改。此时逐步骤对比 parallel 差异，对脚本中对应步骤块做文本替换，
    保留 UI 操作代码和选择器不变。

    实现策略：
    - 脚本里每个步骤块的结构固定为：
        emit("step", index=N, text="...", state="running")
        ... UI 操作代码 ...
        [可能有若干 _run_api_scenario / 并行接口场景注释行]
        emit("step", index=N, text="...", state="passed", ...)
    - 找到该步骤的 running/passed emit 行范围，替换这段范围内的
      `_run_api_scenario(...)` 和 `# 并行接口场景` 行。

    Returns:
        修改后的脚本字符串；任何 patch 失败返回 None（调用方回退清空脚本重编译）。
    """
    if not script_py:
        return None

    # 把 steps 规范化：取出 text 和 parallel
    def _norm(steps):
        result = []
        for s in steps:
            if isinstance(s, dict):
                result.append({"text": s.get("text", ""), "parallel": s.get("parallel") or []})
            else:
                result.append({"text": str(s), "parallel": []})
        return result

    old = _norm(old_steps)
    new = _norm(new_steps)

    # 只处理文本相同、parallel 有变化的步骤（文本变化的不在此处理）
    if len(old) != len(new):
        return None
    if any(o["text"] != n["text"] for o, n in zip(old, new)):
        return None

    lines = script_py.splitlines(keepends=True)
    result_lines = list(lines)
    offset = 0  # 因插入/删除行导致的行号偏移

    for idx, (o, n) in enumerate(zip(old, new)):
        if o["parallel"] == n["parallel"]:
            continue  # 此步骤无变化，跳过

        step_text_json = json.dumps(o["text"], ensure_ascii=False)

        # 找 running emit 行
        running_line = None
        passed_line = None
        for i, ln in enumerate(result_lines):
            stripped = ln.strip()
            if (f'emit("step", index={idx},' in stripped
                    and f'text={step_text_json}' in stripped
                    and 'state="running"' in stripped):
                running_line = i
            if running_line is not None and (
                f'emit("step", index={idx},' in stripped
                and f'text={step_text_json}' in stripped
                and 'state="passed"' in stripped
            ):
                passed_line = i
                break

        if running_line is None or passed_line is None:
            return None  # 找不到步骤边界，放弃 patch

        # 删除 running_line+1 .. passed_line-1 中属于接口场景的行
        # （_run_api_scenario / # 并行接口场景）
        keep = []
        for ln in result_lines[running_line + 1: passed_line]:
            stripped = ln.strip()
            if (stripped.startswith("_run_api_scenario(")
                    or stripped.startswith("# 并行接口场景")):
                continue
            keep.append(ln)

        # 在 passed emit 前插入新的接口场景调用行
        indent = "        "  # 8 空格（脚本体在 with sync_playwright 内）
        new_api_lines = []
        for p in (n["parallel"] or []):
            sid = p.get("id")
            pname = p.get("name") or f"场景#{sid}"
            new_api_lines.append(f"{indent}# 并行接口场景：{pname}\n")
            new_api_lines.append(f"{indent}_run_api_scenario({sid}, api_ctx, page=page)\n")

        # 重组：running行 + UI代码 + 新接口行 + passed行
        replacement = (
            [result_lines[running_line]]
            + keep
            + new_api_lines
            + [result_lines[passed_line]]
        )

        result_lines[running_line: passed_line + 1] = replacement

    patched = "".join(result_lines)

    # 语法校验
    try:
        compile(patched, "<patched>", "exec")
    except SyntaxError:
        return None

    return patched
