"""录制内核（codegen 版）。

设计要点：
- 录制**只允许在本地运行**：启动 `playwright codegen` 子进程弹出有头浏览器。
  云端由 `RECORDING_ENABLED` 开关在 API 层直接禁用。
- 用 codegen 原生录制：100% 可靠捕获所有操作，含新标签页 / 跨域跳转，
  不需要 JS 钩子注入，彻底消除注入时机竞态问题。
- codegen 直接生成 Playwright Python 脚本，停止后用 LLM 把脚本翻译成中文步骤入库。
- 支持 segment 切分：用户点「标记分段」时在脚本末尾插入注释 # ==SEGMENT==，
  翻译时据此切成多条用例。

事件模型（向前端推送）：
- ready：录制进程已启动，浏览器弹出
- record_log：进度消息
- done：录制结束（进程退出或用户点停止）
- error：启动失败
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402

EventCb = Callable[[str, dict], None]


class RecordSession:
    """一次录制会话：起 playwright codegen 子进程，用户在真实浏览器里操作。"""

    def __init__(self, session_id: int, emit: EventCb):
        self.session_id = session_id
        self.emit = emit
        self.commands: "queue.Queue[dict]" = queue.Queue()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._script_file: Optional[Path] = None
        # segment 切分：用户点「标记分段」时往脚本追加注释标记
        self._segments: int = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def send_command(self, cmd: dict) -> None:
        """有效指令：{"type":"stop"} 结束；{"type":"mark"} 切分用例。"""
        self.commands.put(cmd)

    def get_script(self) -> Optional[str]:
        """返回 codegen 生成的 Python 脚本内容（停止后读取）。"""
        if self._script_file and self._script_file.exists():
            try:
                return self._script_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    def cleanup(self) -> None:
        if self._script_file:
            try:
                self._script_file.unlink(missing_ok=True)
            except Exception:
                pass

    def _run(self) -> None:
        try:
            tmp = tempfile.NamedTemporaryFile(
                suffix=".py", delete=False, prefix="codegen_record_"
            )
            tmp.close()
            self._script_file = Path(tmp.name)

            # 找到虚拟环境里的 playwright 可执行文件
            pw_bin = Path(sys.executable).parent / "playwright"
            if not pw_bin.exists():
                pw_bin = Path(sys.executable).parent / "playwright.exe"
            if not pw_bin.exists():
                raise FileNotFoundError(
                    f"找不到 playwright CLI，请确认已在虚拟环境中安装：{pw_bin}"
                )

            login_url = config.get("base_url").rstrip("/") + "/login"

            cmd = [
                str(pw_bin), "codegen",
                "--target", "python",
                "--output", str(self._script_file),
                login_url,
            ]

            # 子进程继承环境变量，stdout/stderr 不捕获（codegen 自己管 UI）
            self._proc = subprocess.Popen(cmd)
            self.ready.set()
            self.emit("record_log", {
                "msg": "录制就绪，请在弹出的 Playwright Inspector 浏览器里操作（从登录开始），"
                       "完成后点『停止并生成』或直接关闭浏览器"
            })

            # 主循环：等前端 stop 指令，或 codegen 进程自然退出（用户关闭浏览器）
            while not self.stopped.is_set():
                if self._proc.poll() is not None:
                    # codegen 进程已自然退出（用户关闭了 Inspector 浏览器窗口）
                    self.emit("record_log", {"msg": "录制浏览器已关闭，正在生成用例…"})
                    break
                try:
                    cmd_msg = self.commands.get(timeout=0.3)
                except queue.Empty:
                    continue
                if cmd_msg.get("type") == "stop":
                    break
                if cmd_msg.get("type") == "mark":
                    # 切分：向脚本文件追加一条注释标记（翻译时据此分段）
                    self._segments += 1
                    try:
                        with open(self._script_file, "a", encoding="utf-8") as f:
                            f.write(f"\n# ==SEGMENT==\n")
                        self.emit("record_log", {"msg": f"已插入分段标记（第 {self._segments} 段）"})
                    except Exception:
                        pass

        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {str(e)[:200]}"
            self.emit("error", {"msg": f"录制启动失败：{self.error}"})
        finally:
            # 杀掉残留进程
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self.stopped.set()


# -------------------- codegen 脚本 → 中文用例翻译 --------------------

_TRANSLATE_SYSTEM = """\
你是 UI 自动化测试用例设计师。下面是 Playwright codegen 录制生成的 Python 脚本。
脚本里可能包含注释 # ==SEGMENT==，代表用户手动切分用例的分隔点；
**有几个分段标记就拆成几+1 条用例，没有标记则整体为一条**。

请把每段操作整理成一条中文测试用例，调用 emit_cases 返回结构化结果。

步骤翻译规则：
1) 若脚本开头有 goto('/login') + fill('账号') + fill('密码') + click('登录')，整理为『登录系统』；
2) 连续的菜单点击（get_by_text / get_by_role 点菜单项）+ URL/标题变化，合并为『进入 X → Y』；
3) fill() → 『在 X 输入 Y』；click() → 『点击 X』；select_option() → 『在 X 选择 Y』；
4) expect_popup() / context.pages[] 表示新标签页，继续记录新标签页里的操作，不单独成步；
5) **保留所有步骤，包括新增、删除、提交、保存等，忠实还原用户操作；**
6) 每步用简洁中文表达，动词+宾语，不超过 30 字。
"""

_TRANSLATE_TOOL = [{
    "type": "function",
    "function": {
        "name": "emit_cases",
        "description": "把录制的操作脚本整理成中文测试用例",
        "parameters": {
            "type": "object",
            "properties": {
                "cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "module": {"type": "string", "description": "所属模块/一级菜单名"},
                            "steps": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "steps"],
                    },
                },
            },
            "required": ["cases"],
        },
    },
}]


def translate_script(script: str) -> list[dict]:
    """把 codegen 生成的 Playwright Python 脚本翻译成中文用例草稿列表。

    按 # ==SEGMENT== 注释切段，每段一条用例；无分段标记时整体为一条。
    """
    from core.llm_client import LLMError, get_client
    import json

    if not script or not script.strip():
        return []

    # 按分段标记切割
    raw_parts = script.split("# ==SEGMENT==")
    parts = [p.strip() for p in raw_parts if p.strip()]
    if not parts:
        return []

    n = len(parts)
    if n == 1:
        user = f"以下是完整录制脚本，请整理为 1 条用例：\n\n```python\n{parts[0]}\n```"
    else:
        sections = "\n\n".join(
            f"=== 第 {i+1} 段（用例 {i+1}）===\n```python\n{p}\n```"
            for i, p in enumerate(parts)
        )
        user = (
            f"以下是按分段标记拆分后的 {n} 段录制脚本，请整理为严格 {n} 条用例，"
            f"每段对应一条，不要跨段合并：\n\n{sections}"
        )

    msg = get_client().chat(
        messages=[
            {"role": "system", "content": _TRANSLATE_SYSTEM},
            {"role": "user", "content": user},
        ],
        tools=_TRANSLATE_TOOL,
    )
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        raise LLMError("翻译失败：模型未返回结构化用例")
    try:
        args = json.loads(tool_calls[0]["function"]["arguments"] or "{}")
    except (json.JSONDecodeError, KeyError, TypeError):
        raise LLMError("翻译结果解析失败")

    out = []
    for c in args.get("cases") or []:
        steps = [s.strip() for s in (c.get("steps") or []) if s.strip()]
        if not steps:
            continue
        out.append({
            "name": c.get("name") or "录制用例",
            "module": c.get("module") or "",
            "tags": ["AI生成", "录制"],
            "steps": steps,
        })
    return out


# -------------------- codegen 脚本 → 平台兼容脚本改造 --------------------

# 需要从 script_compiler 导入的样板，避免循环 import —— 直接复制常量
_ADAPT_HEADER = '''\
import sys
import json
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

def emit(kind, **kw):
    print(json.dumps({"kind": kind, **kw}, ensure_ascii=False), flush=True)

BASE_URL = os.environ.get("PW_BASE_URL", "").rstrip("/")
USERNAME  = os.environ.get("PW_USERNAME", "")
PASSWORD  = os.environ.get("PW_PASSWORD", "")
# 登录就绪判据选择器 + localStorage token 键名（由父进程按 config 注入）
LOGIN_READY_SEL = os.environ.get("PW_LOGIN_READY_SEL", ":text-is('首页'):visible")
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


def _run_api_scenario(scenario_id: int, api_ctx: dict) -> None:
    from db import ApiScenario, SessionLocal  # type: ignore
    from api_runner import run_scenario  # type: ignore
    with SessionLocal() as s:
        sc = s.get(ApiScenario, scenario_id)
        if not sc:
            raise AssertionError(f"接口场景不存在：id={scenario_id}")
        scenario = sc.to_dict()
    scenario_name = scenario.get("name") or f"scenario#{scenario_id}"
    def _inner_emit(kind, payload):
        if kind == "step":
            msg = f"[接口场景] {payload.get('name','')} → {payload.get('state','')}"
            if payload.get("detail"):
                msg += f" — {payload['detail']}"
            emit("log", msg=msg)
        elif kind == "api":
            emit("log", msg=f"[接口] {payload.get('method','')} {payload.get('url','')} → {payload.get('status','')}")
    result = run_scenario(scenario, _inner_emit, ctx=api_ctx)
    if result.get("status") != "passed":
        raise AssertionError(f"接口场景「{scenario_name}」未通过：{result.get('error') or '断言失败'}")


def _setup_screencast(context, page):
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

_ADAPT_FOOTER = '''\

try:
    run()
except Exception as _e:
    emit("done", status="failed", error=f"{type(_e).__name__}: {_e}")
    sys.exit(1)
'''

# LLM 只负责返回步骤边界行号，不修改任何代码行
_ADAPT_STEP_TOOL = [{
    "type": "function",
    "function": {
        "name": "mark_steps",
        "description": (
            "根据 Playwright 操作脚本，标注每个测试步骤的边界行号。"
            "行号从 0 开始，对应传入的操作行列表索引。"
            "只返回行号，绝对不要修改或重写任何操作代码。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "步骤中文描述（简洁动词+宾语）"},
                            "start": {"type": "integer", "description": "该步骤第一条操作行的索引（含）"},
                            "end": {"type": "integer", "description": "该步骤最后一条操作行的索引（含）"},
                        },
                        "required": ["text", "start", "end"],
                    },
                },
            },
            "required": ["steps"],
        },
    },
}]


def adapt_codegen_script(raw_script: str, case_steps: list[str]) -> Optional[str]:
    """把 codegen 原始脚本改造为平台兼容的可执行脚本。

    改造规则（全部纯代码变换，不让 LLM 重写任何操作行）：
    1. 提取 run() 函数体内的操作行（去掉 def / with sync_playwright / browser.close 等样板）
    2. 把硬编码的 BASE_URL / 账号密码替换为环境变量引用
    3. 把 headless=False 改为 headless=True
    4. 给 browser.new_context() 加 viewport 参数，补 _setup_screencast 调用
    5. 初始化 api_ctx = {}，登录后同步 token
    6. 用 LLM（function call）仅返回步骤边界行号，代码按行号插入 emit("step") 包裹
    7. 拼接 _ADAPT_HEADER + 改造后 run() + _ADAPT_FOOTER

    LLM 只看操作行索引 → 返回 {text, start, end}，不修改任何操作代码。
    失败时返回 None，调用方存原始 codegen 脚本（runner 已做 headless 替换兜底）。
    """
    from core.llm_client import get_client
    import json as _json

    if not raw_script or not raw_script.strip():
        return None

    lines = raw_script.splitlines()

    # ── 1. 提取 run() 函数体内的纯操作行 ──────────────────────────────────
    # codegen 脚本结构：
    #   def run(playwright) -> None:
    #       browser = playwright.chromium.launch(...)
    #       context = browser.new_context()
    #       page = context.new_page()
    #       <操作行...>
    #       context.close()
    #       browser.close()
    #   with sync_playwright() as playwright:
    #       run(playwright)
    op_lines: list[str] = []  # 操作行（保留原始内容，含4空格缩进）
    in_run = False
    for raw_line in lines:
        stripped = raw_line.strip()
        # 进入 run 函数
        if re.match(r"^def run\s*\(", stripped):
            in_run = True
            continue
        if not in_run:
            continue
        # 退出：遇到顶层 with sync_playwright 或非缩进行（函数结束）
        if stripped and not raw_line.startswith(" ") and not raw_line.startswith("\t"):
            break
        # 跳过样板行
        _skip_patterns = (
            r"^browser\s*=\s*playwright\.chromium\.launch",
            r"^context\s*=\s*browser\.new_context",
            r"^page\s*=\s*context\.new_page",
            r"^context\.close\(\)",
            r"^browser\.close\(\)",
            r"^page\.close\(\)",
            r"^#\s*-{3,}",  # codegen 分隔注释
        )
        if any(re.match(p, stripped) for p in _skip_patterns):
            continue
        # 空行保留（保持可读性），注释行保留
        op_lines.append(raw_line)

    if not op_lines:
        return None

    # ── 2. 替换硬编码 URL / 账号密码 ───────────────────────────────────────
    # 被测站点 base_url（从 config 读，兜底空串）
    try:
        from core import config as _config
        _base = (_config.get("base_url") or "").rstrip("/")
    except Exception:
        _base = ""

    def _replace_url(line: str) -> str:
        if not _base:
            return line
        # page.goto("http://...") → page.goto(f"{BASE_URL}/path")
        def _sub(m):
            url = m.group(1)
            if url.startswith(_base):
                path = url[len(_base):]
                if not path:
                    path = "/"
                return f'f"{{BASE_URL}}{path}"'
            return m.group(0)
        return re.sub(r'"(https?://[^"]+)"', _sub, line)

    # 账号密码：替换 fill("账号值") 里的硬编码值
    # 先收集原始凭据（从第一条 fill 行判断）
    _raw_user, _raw_pass = "", ""
    try:
        from core import config as _config2
        creds = _config2.get("account") or {}
        _raw_user = creds.get("username") or ""
        _raw_pass = creds.get("password") or ""
    except Exception:
        pass
    # 也从 op_lines 里尝试反推（codegen 把真实密码写进去了）
    for _ol in op_lines:
        _s = _ol.strip()
        if 'fill("' in _s and ('账户' in _s or '用户名' in _s or 'username' in _s.lower()):
            _m = re.search(r'fill\("([^"]+)"\)', _s)
            if _m:
                _raw_user = _m.group(1)
        if 'fill("' in _s and ('密码' in _s or 'password' in _s.lower()):
            _m = re.search(r'fill\("([^"]+)"\)', _s)
            if _m:
                _raw_pass = _m.group(1)

    def _replace_creds(line: str) -> str:
        # 仅替换 fill("硬编码值") 中与已知账号密码完全匹配的部分
        if _raw_user and f'fill("{_raw_user}")' in line:
            line = line.replace(f'fill("{_raw_user}")', 'fill(USERNAME)')
        if _raw_pass and f'fill("{_raw_pass}")' in line:
            line = line.replace(f'fill("{_raw_pass}")', 'fill(PASSWORD)')
        return line

    op_lines = [_replace_creds(_replace_url(l)) for l in op_lines]

    # ── 3. 在操作行列表前插入 browser/context/page 初始化（含 viewport + 投屏）──
    init_lines = [
        "    with sync_playwright() as pw:",
        "        browser = pw.chromium.launch(headless=True)",
        "        ctx = browser.new_context(viewport={'width': 1440, 'height': 900})",
        "        page = ctx.new_page()",
        "        _setup_screencast(ctx, page)",
        "        api_ctx = {}  # 接口场景变量上下文",
        "        token = \"\"",
        "",
    ]
    # 操作行整体右移4空格（从 def run() 的4空格层进入 with sync_playwright() 的8空格层）
    # 必须保留原始相对缩进（with 块内的子行比外层多4空格），不能 lstrip 后统一加缩进
    normalized: list[str] = []
    for line in op_lines:
        if line.strip() == "":
            normalized.append("")
            continue
        normalized.append("    " + line)  # 整体右移4格，相对缩进不变

    # 登录后同步 token 到 api_ctx（找到最后一个 fill(PASSWORD) 行后插入）
    _token_inject = [
        "        try:",
        "            _tok = page.evaluate(\"() => localStorage.getItem(TOKEN_KEY)\")",
        "            if _tok:",
        "                token = _tok.strip().strip('\"')",
        "                api_ctx['token'] = token",
        "        except Exception:",
        "            pass",
    ]
    last_pass_idx = -1
    for _i, _l in enumerate(normalized):
        if "fill(PASSWORD)" in _l:
            last_pass_idx = _i
    if last_pass_idx >= 0:
        # 找到 fill(PASSWORD) 之后的第一个 click（登录按钮）再往后插
        insert_after = last_pass_idx
        for _i in range(last_pass_idx + 1, len(normalized)):
            if ".click()" in normalized[_i]:
                insert_after = _i
                break
        normalized = normalized[:insert_after + 1] + _token_inject + normalized[insert_after + 1:]

    close_lines = [
        "",
        "        ctx.close()",
        "        browser.close()",
        "    emit(\"done\", status=\"passed\", error=\"\")",
    ]

    # ── 4. 用 LLM（function call）标注步骤边界 ────────────────────────────
    # 只把操作行的纯文本（去除缩进）发给 LLM，LLM 返回 {text, start, end} 列表
    # 传入 case_steps 供 LLM 参考，但 LLM 不能用它来重写代码
    indexed_ops = "\n".join(f"{i}: {l.strip()}" for i, l in enumerate(normalized) if l.strip())

    steps_hint = ""
    if case_steps:
        steps_hint = "\n\n参考步骤（仅供理解语义，不要重写代码行）：\n" + "\n".join(
            f"- {s}" for s in case_steps
        )

    llm_user = (
        f"以下是 Playwright 录制操作行（格式：行索引: 代码）：\n\n{indexed_ops}"
        f"{steps_hint}\n\n"
        "请把这些操作行按测试步骤分组，调用 mark_steps 返回每个步骤的 text/start/end。\n"
        "要求：\n"
        "1. start/end 必须是上面列出的有效行索引；\n"
        "2. 所有行索引必须被覆盖，不能有遗漏；\n"
        "3. 只返回行索引范围，绝对不要重写、修改任何代码行。"
    )

    step_boundaries: list[dict] = []
    try:
        msg = get_client().chat(
            messages=[
                {"role": "system", "content": "你是测试用例步骤分析器，只标注行号范围，不修改代码。"},
                {"role": "user", "content": llm_user},
            ],
            tools=_ADAPT_STEP_TOOL,
            temperature=0,
            timeout=60,
        )
        tc = (msg.get("tool_calls") or [])
        if tc:
            step_boundaries = _json.loads(tc[0]["function"]["arguments"]).get("steps") or []
    except Exception:
        pass  # LLM 失败时退化：把所有行作为一个步骤

    if not step_boundaries:
        # 退化：整体作为一个步骤
        step_boundaries = [{"text": case_steps[0] if case_steps else "执行录制操作", "start": 0, "end": len(normalized) - 1}]

    # ── 5. 按边界在 normalized 行间插入 emit("step") ─────────────────────
    # 收集有内容的行索引集合，用于边界校正
    content_indices = [i for i, l in enumerate(normalized) if l.strip()]
    if not content_indices:
        return None

    result_body: list[str] = []
    covered_up_to = -1

    for step_idx, sb in enumerate(step_boundaries):
        s_text = sb.get("text") or f"步骤{step_idx + 1}"
        s_start = int(sb.get("start") or 0)
        s_end = int(sb.get("end") or s_start)
        # 边界钳位
        s_start = max(0, min(s_start, len(normalized) - 1))
        s_end = max(s_start, min(s_end, len(normalized) - 1))

        # emit running
        result_body.append(
            f"        emit(\"step\", index={step_idx}, "
            f"text={_json.dumps(s_text, ensure_ascii=False)}, state=\"running\")"
        )
        result_body.append("        time.sleep(1)")
        # 原始操作行（原封不动）
        for i in range(s_start, s_end + 1):
            result_body.append(normalized[i])
        # emit passed
        result_body.append(
            f"        emit(\"step\", index={step_idx}, "
            f"text={_json.dumps(s_text, ensure_ascii=False)}, state=\"passed\", detail=\"完成\")"
        )
        result_body.append("")
        covered_up_to = s_end

    # 未被覆盖的尾部行（LLM 遗漏时补上），跳过纯空行
    tail_lines = [normalized[i] for i in range(covered_up_to + 1, len(normalized)) if normalized[i].strip()]
    if tail_lines:
        result_body.append(
            f"        emit(\"step\", index={len(step_boundaries)}, "
            f"text=\"收尾操作\", state=\"running\")"
        )
        result_body.extend(tail_lines)
        result_body.append(
            f"        emit(\"step\", index={len(step_boundaries)}, "
            f"text=\"收尾操作\", state=\"passed\", detail=\"完成\")"
        )

    # ── 6. 拼接完整脚本 ──────────────────────────────────────────────────
    run_func_lines = ["def run():"] + init_lines + result_body + close_lines
    run_func = "\n".join(run_func_lines)
    full_script = _ADAPT_HEADER + run_func + _ADAPT_FOOTER

    # 语法检查
    try:
        compile(full_script, "<adapted>", "exec")
    except SyntaxError:
        return None

    return full_script
