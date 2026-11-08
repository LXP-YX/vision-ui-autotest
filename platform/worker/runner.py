"""Worker 执行内核：把一条用例的中文步骤跑起来。

阶段策略（P1+P2 本地先跑通）：
- 复用 core/ 引擎（登录、导航、健康检查）；
- 步骤解释器：把中文步骤映射到 core/ 动作（阶段0规则式，P4 换 AI Planner）；
- 可视化模式：无头 Chromium + CDP Page.startScreencast，逐帧回调给上层
  （→ WebSocket → 前端 canvas）。无头浏览器同样支持投屏，故无需有头、无需显示器，
  本地与云端形态一致；
- 全程发结构化事件（step/frame/done）给回调，禁 time.sleep。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core import browser as browser_mod  # noqa: E402
from core import config  # noqa: E402
from core.login_page import LoginPage  # noqa: E402
from core.main_page import MainPage  # noqa: E402

# 事件回调签名：event(kind, payload) —— kind ∈ {step, frame, log, done}
EventCb = Callable[[str, dict], None]


def _noop(kind: str, payload: dict) -> None:
    pass


def _do_login(main: MainPage, ctx: dict) -> str:
    """打开已登录首页并确认就绪，同时提取 token 存入全局变量 ctx['token']。"""
    from core.main_page import ready_menu_selector
    main.page.goto(config.get("base_url").rstrip("/"), wait_until="domcontentloaded")
    main.wait_visible(ready_menu_selector(), timeout=15_000)
    # 登录后从 localStorage 提取 token（键名可配），存入全局变量供接口请求使用
    key = config.get("api", "token_storage_key", default="token")
    try:
        raw = main.page.evaluate(f"() => localStorage.getItem('{key}')")
        if raw:
            ctx["token"] = raw.strip().strip('"')  # 去掉 JSON 字符串的引号
    except Exception:
        pass
    tok = ctx.get("token")
    tail = f"，已获取全局 token（末4位 …{tok[-4:]}）" if tok else ""
    return f"已进入已登录首页，可见菜单：{main.visible_top_menus()}{tail}"


def _do_navigate(main: MainPage, top: str, sub: str) -> str:
    main.nav_to(top, sub)
    return f"已导航到 {top} → {sub}"


def _do_check_health(main: MainPage) -> str:
    health = main.health_check()
    if not health["healthy"]:
        raise AssertionError(f"页面健康检查未通过：{health}")
    return f"健康检查通过：{health}"


def _do_click(main: MainPage, target: str, emit: EventCb,
              ctx: dict | None = None, locator_hint: dict | None = None) -> str:
    """点击一个元素：三级定位（Store→语义→VLM 自愈）找到后点击。

    locator_hint: 用户手动指定的精确定位 {type, value}，优先于三级定位。
    命中的 selector/strategy 写入 ctx['_rec']（供脚本编译记录本步实际用的选择器）。
    """
    from core import self_healing
    hit = self_healing.find_element(main.page, target, locator_hint=locator_hint)
    if not hit:
        # 三级都找不到 —— 判为功能缺陷，不伪造
        raise AssertionError(f"未找到元素「{target}」（Store/语义/VLM 均失败，疑为功能缺陷）")
    strat = hit["strategy"]
    if ctx is not None and isinstance(ctx.get("_rec"), dict):
        ctx["_rec"]["selector"] = hit["selector"]
        ctx["_rec"]["strategy"] = strat
    # 结构化自愈事件（前端高亮展示）
    emit("heal", {
        "kind": "vlm" if strat == "vlm" else "locate",
        "target": target,
        "strategy": strat,
        "selector": hit["selector"],
        "healed": strat == "vlm",
    })
    if not main.safe_click(hit["selector"]):
        raise AssertionError(f"元素「{target}」定位成功但点击失败：{hit['selector']}")
    main.wait_loading_disappear(timeout=8_000)
    return f"已点击「{target}」（策略 {strat}）"


def _do_assert_visual(main: MainPage, claim: str, emit: EventCb) -> str:
    """VLM 视觉断言：判定当前页面是否满足描述。"""
    from core import vision
    r = vision.assert_visual(main.page, claim)
    emit("heal", {
        "kind": "assert",
        "claim": claim,
        "passed": r["passed"],
        "reason": r["reason"],
    })
    if not r["passed"]:
        raise AssertionError(f"视觉断言未通过：{claim} —— {r['reason']}")
    return f"视觉断言通过：{claim} —— {r['reason']}"


def _do_api_scenario(scenario_id: int, ctx: dict, emit: EventCb,
                     main: "MainPage | None" = None,
                     cancel_event=None) -> str:
    """执行接口场景：从 db 载入 → 复用 api_runner.run_scenario → 变量双向共享。

    - UI 用例上下文 ctx 传入场景作为初始变量（含登录 token）；
    - 场景内部提取的新变量就地写回 ctx，供后续 UI 步骤/场景引用；
    - 场景任一步骤断言未通过 → 抛 AssertionError，run_case 立即中断后续步骤；
    - 场景内部的 step/done 事件不外抛，改以 log 前缀展示，避免前端误判外层用例状态；
    - main 不为 None 时，断言重试等待期间每 300ms 截图推帧，保持实时视图不卡顿。
    """
    from db import ApiScenario, SessionLocal  # type: ignore
    from api_runner import run_scenario

    with SessionLocal() as s:
        sc = s.get(ApiScenario, scenario_id)
        if not sc:
            raise AssertionError(f"接口场景不存在：id={scenario_id}")
        scenario = sc.to_dict()

    scenario_name = scenario.get("name") or f"scenario#{scenario_id}"

    def _inner_emit(kind: str, payload: dict) -> None:
        if kind == "done":
            return  # 场景 done 不外抛，避免前端把整个用例判成结束
        if kind == "step":
            phase = payload.get("phase", "")
            name = payload.get("name", "")
            state = payload.get("state", "")
            detail = payload.get("detail", "")
            msg = f"[接口场景] {phase}#{payload.get('index', '')} {name} → {state}"
            if detail:
                msg += f" — {detail}"
            emit("log", {"msg": msg})
        elif kind == "api":
            method = payload.get("method", "")
            url = payload.get("url", "")
            status = payload.get("status", "")
            emit("log", {"msg": f"[接口] {method} {url} → {status}"})
        else:
            emit(kind, payload)

    # 构造投屏帧回调：重试等待期间每次调用截一帧推给前端
    def _frame_cb() -> None:
        if main is None:
            return
        try:
            import base64 as _b64
            jpg = main.page.screenshot(full_page=False, type="jpeg", quality=60)
            emit("frame", {"data": _b64.b64encode(jpg).decode()})
        except Exception:  # noqa: BLE001
            pass

    emit("log", {"msg": f"▶ 进入接口场景「{scenario_name}」（携带上下文变量 {len(ctx)} 个）"})
    result = run_scenario(scenario, _inner_emit, ctx=ctx,
                          frame_cb=_frame_cb if main is not None else None,
                          cancel_event=cancel_event)

    result_status = result.get("status")
    if result_status == "cancelled":
        # 取消信号透传：上层步骤循环同样检查 cancel_event，会正确中止
        raise AssertionError("手动停止")
    if result_status != "passed":
        raise AssertionError(
            f"接口场景「{scenario_name}」未通过：{result.get('error') or '断言失败'}"
        )
    passed_cnt = sum(1 for r in result.get("step_results", []) if r.get("ok"))
    return f"接口场景「{scenario_name}」通过（{passed_cnt} 步）"


def _do_wait(seconds: float, main: MainPage, emit: EventCb) -> str:
    """等待指定秒数，期间每 300ms 截一帧推给前端，保证实时视图不中断。

    用 page.screenshot() 而非 CDP（此处没有 cdp 引用），质量与保活帧一致。
    """
    import time as _time
    seconds = max(0.0, float(seconds))
    elapsed = 0.0
    interval = 0.3
    page = main.page
    while elapsed < seconds:
        _time.sleep(min(interval, seconds - elapsed))
        elapsed += interval
        try:
            import base64 as _b64
            jpg = page.screenshot(full_page=False, type="jpeg", quality=60)
            emit("frame", {"data": _b64.b64encode(jpg).decode()})
        except Exception:
            pass
    return f"已等待 {seconds:.0f} 秒"


def _dispatch(action: str, args: dict, main: MainPage, ctx: dict, emit: EventCb,
              cancel_event=None) -> str | None:
    """把结构化动作分发到 core/ 执行器；未知动作返回 None。"""
    if action == "login":
        return _do_login(main, ctx)
    if action == "navigate":
        top, sub = args.get("top", ""), args.get("sub", "")
        if top and sub:
            return _do_navigate(main, top, sub)
    if action == "check_health":
        return _do_check_health(main)
    if action == "click_element":
        target = args.get("target", "")
        if target:
            locator_hint = args.get("locator_hint") or None
            return _do_click(main, target, emit, ctx, locator_hint=locator_hint)
    if action == "assert_visual":
        claim = args.get("claim", "")
        if claim:
            return _do_assert_visual(main, claim, emit)
    if action == "api_scenario":
        sid = args.get("id")
        if sid is not None:
            return _do_api_scenario(int(sid), ctx, emit, main=main, cancel_event=cancel_event)
    if action == "wait":
        return _do_wait(args.get("seconds", 1), main, emit)
    return None


def _interpret_step_rule(step: str) -> dict | None:
    """规则式解析器（AI 不可用时的回退）：把高频确定步骤解析为 plan。

    只解析、不执行——返回 {action, args} 或 None（未识别）。
    这三类语义明确、规则命中即正确，零 LLM 调用省 token。
    """
    import re as _re
    s = step.strip()
    if "登录" in s:
        return {"action": "login", "args": {}}
    if "进入" in s and ("→" in s or "->" in s):
        parts = [p.strip() for p in s.split("进入", 1)[1].replace("->", "→").split("→")]
        if len(parts) >= 2:
            return {"action": "navigate", "args": {"top": parts[0], "sub": parts[1]}}
    if "无报错" in s or "健康" in s or "正常加载" in s or "导航可见" in s:
        return {"action": "check_health", "args": {}}
    # 等待 N 秒 / 等待N秒 / 等待 Ns / 暂停Ns
    m = _re.search(r"(?:等待|暂停|停留|pause|wait)[^\d]*(\d+(?:\.\d+)?)\s*[秒sSm分]?", s, _re.IGNORECASE)
    if m:
        seconds = float(m.group(1))
        # "等待N分"时单位是分钟
        unit_char = s[m.end(1):m.end(1) + 1].strip()
        if unit_char in ("m", "分"):
            seconds *= 60
        return {"action": "wait", "args": {"seconds": seconds}}
    return None


def _plan_step(step: str) -> dict | None:
    """把一条中文步骤解析为 plan {action, args}（不执行）。

    解析优先级：接口场景正则 → 规则式（零 LLM）→ AI Planner。
    三者都无法识别返回 None（调用方标记跳过）。
    """
    import re
    m = re.match(r"\s*\[\s*接口场景\s*#\s*(\d+)\s*\]", step)
    if m:
        return {"action": "api_scenario", "args": {"id": int(m.group(1))}}

    plan = _interpret_step_rule(step)
    if plan is not None:
        return plan

    from planner import plan_step
    return plan_step(step)


def _interpret_step(step: str, ctx: dict, main: MainPage, emit: EventCb,
                    locator_hint: dict | None = None, cancel_event=None) -> str:
    """解析并执行一条中文步骤：解析出结构化 plan → 统一分发执行。

    locator_hint: 用户手动指定的精确定位，注入到 click_element 的 args 里优先使用。
    """
    plan = _plan_step(step)
    if plan:
        action, args = plan["action"], plan.get("args") or {}
        # 把 locator_hint 注入 click_element 的 args，供 _dispatch → _do_click 使用
        if action == "click_element" and locator_hint:
            args = {**args, "locator_hint": locator_hint}
        if isinstance(ctx.get("_rec"), dict):
            ctx["_rec"]["action"] = action
            ctx["_rec"]["args"] = args
        if action not in ("login", "navigate", "check_health", "api_scenario"):
            emit("log", {"msg": f"[AI] 解析为 {action}{args or ''}"})
        result = _dispatch(action, args, main, ctx, emit, cancel_event=cancel_event)
        if result is not None:
            return result

    emit("log", {"msg": f"[跳过] 未识别步骤：{step.strip()}"})
    if isinstance(ctx.get("_rec"), dict):
        ctx["_rec"]["action"] = "skip"
        ctx["_rec"]["args"] = {}
    return f"[跳过] {step.strip()}"


def _step_text(raw) -> str:
    """规范化步骤为文本：字符串直返，对象取其 text 字段。"""
    if isinstance(raw, dict):
        return str(raw.get("text", "")).strip()
    return str(raw).strip()


def _step_locator_hint(raw) -> dict | None:
    """提取步骤对象里的手动定位提示 {type, value}；无则返回 None。"""
    if isinstance(raw, dict):
        h = raw.get("locator_hint")
        if isinstance(h, dict) and h.get("type") and h.get("value"):
            return h
    return None


def _step_parallels(raw) -> list:
    """取步骤挂载的并行接口场景列表（元素含 id/name）；无则返回空列表。"""
    if isinstance(raw, dict):
        return raw.get("parallel") or []
    return []


def _run_step_with_parallels(text, parallels, ctx, main, emit: EventCb, ui_fn=None) -> str:
    """执行「UI 步骤 + 并行接口场景」：UI 步骤主线程跑，接口场景多线程真并发。

    - UI 步骤（Playwright 同步 API）必须留主线程执行，不能跨线程；
    - 每个并行接口场景在独立线程用 ctx 副本执行，避免并发写同一 dict 竞态；
    - 门禁：UI 步骤与所有场景全部通过才算通过；任一失败汇总错误抛 AssertionError
      （触发主循环 break，跳过后续步骤）；
    - 全通过后把各场景副本里新增/变更的变量合并回主 ctx，供后续步骤引用。

    Args:
        ui_fn: UI 步骤执行函数 (text, ctx, main, emit) -> detail；
               默认自然语言解析；回放时传回放版。回放中 UI 侧的 ScriptDriftError
               必须穿透并发汇总（不被吞成 AssertionError），才能触发 AI 兜底——
               故先 join 所有场景线程避免悬挂，再把漂移异常重新抛出。
    """
    import concurrent.futures

    if ui_fn is None:
        ui_fn = lambda t, c, m, e: _interpret_step(t, c, m, e)

    # 每个场景一份 ctx 快照副本（携带 ${token} 等），彼此隔离
    scenario_ctxs = [dict(ctx) for _ in parallels]
    emit("log", {"msg": f"⇉ UI 步骤与 {len(parallels)} 个接口场景并行执行"})

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(parallels)) as pool:
        futures = []
        for p, sctx in zip(parallels, scenario_ctxs):
            sid = int(p.get("id"))
            futures.append((p, sctx, pool.submit(_do_api_scenario, sid, sctx, emit, None)))

        # 主线程同时执行 UI 步骤（Playwright 留主线程）
        ui_error = None
        ui_result = None
        ui_drift = None
        try:
            ui_result = ui_fn(text, ctx, main, emit)
        except ScriptDriftError as e:
            ui_drift = e  # 漂移须穿透，先记下、join 后重抛
        except Exception as e:  # noqa: BLE001
            ui_error = str(e)

        # 收集并行结果：无论 UI 成败都要 join，避免悬挂线程
        errors = []
        if ui_error:
            errors.append(f"UI 步骤失败：{ui_error}")
        for p, sctx, fut in futures:
            name = p.get("name") or f"场景#{p.get('id')}"
            try:
                fut.result()
                # 场景通过：把副本里的变量合并回主 ctx（供后续步骤引用）
                ctx.update(sctx)
            except Exception as e:  # noqa: BLE001
                errors.append(f"接口场景「{name}」失败：{e}")

    # 线程已全部 join：回放 UI 侧漂移穿透，触发 AI 兜底（不与场景错误合并）
    if ui_drift is not None:
        raise ui_drift

    if errors:
        raise AssertionError("；".join(errors))

    ok_names = "、".join(p.get("name") or f"#{p.get('id')}" for p in parallels)
    return f"{ui_result}（并行接口场景全部通过：{ok_names}）"


# ============================================================
# 脚本化：编译产物 + 回放 + 定位漂移自愈
# 脚本 = 自然语言步骤的编译产物/加速缓存；自然语言 steps 仍是唯一真源。
# ============================================================


class ScriptDriftError(Exception):
    """脚本回放时固化的 selector 失效（元素仍应在、选择器漂移）。

    仅从 _do_click_fixed 抛出——click_element 是唯一固化了易变数据（selector）
    的动作，只有它有「漂移」语义。捕获后触发 AI 兜底重编译（自愈脚本）；
    其余动作（health/visual/navigate）失败是页面真有问题，不属漂移，直接判不通过。
    """


def _compile_entry(step_obj, rec: dict, parallels: list) -> dict:
    """把一步的「原始步骤 + 本次实际动作记录」编译成一条脚本条目。

    rec 为 ctx['_rec']：{action, args, selector?, strategy?, new_tab_url?}。
    """
    entry = {
        "step": _step_text(step_obj),
        "action": rec.get("action") or "skip",
        "args": rec.get("args") or {},
    }
    # click_element 固化命中的稳定选择器（回放时跳过三级定位直接用）
    if rec.get("selector"):
        entry["selector"] = rec["selector"]
        entry["strategy"] = rec.get("strategy") or ""
    # 保留步骤级手动定位提示，脚本编译时优先使用（比运行时命中的 selector 更可靠）
    hint = _step_locator_hint(step_obj)
    if hint:
        entry["locator_hint"] = hint
    # 点击触发了新标签页：记录 URL 供脚本生成 expect_popup() 包裹
    if rec.get("new_tab_url"):
        entry["new_tab_url"] = rec["new_tab_url"]
    # 并行接口场景元数据（回放照走并发）
    if parallels:
        entry["parallel"] = [
            {"id": p.get("id"), "name": p.get("name")} for p in parallels
        ]
    return entry


def _save_script(case_id, compiled: list) -> None:
    """把编译好的脚本写回 test_case.script（懒 import db，DB 不可用静默降级）。"""
    if not case_id:
        return
    try:
        from datetime import datetime
        from db import SessionLocal, TestCase  # type: ignore
        with SessionLocal() as s:
            c = s.get(TestCase, int(case_id))
            if not c:
                return  # 用例已被删（批量快照场景），no-op
            c.script = compiled
            c.script_updated_at = datetime.now()
            s.commit()
    except Exception:  # noqa: BLE001
        pass


def _do_click_fixed(main: MainPage, entry: dict, emit: EventCb) -> str:
    """回放 click_element：直接用脚本固化的 selector，跳过三级定位。

    与 _do_click（三级自愈）不同——回放要严：selector 等不到可见元素 / 点击失败
    即判「定位漂移」抛 ScriptDriftError（触发 AI 兜底），不复用 safe_click 的
    JS-click 兜底（那会去点可能已漂移的错元素，掩盖真实漂移）。
    """
    from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout
    selector = entry.get("selector") or ""
    target = (entry.get("args") or {}).get("target", "") or entry.get("step", "")
    if not selector:
        raise ScriptDriftError(f"脚本条目缺 selector：{target}")
    loc = main.page.locator(selector).first
    try:
        loc.wait_for(state="visible", timeout=6_000)
        loc.click(timeout=6_000)
    except (PWTimeout, PWError) as e:
        raise ScriptDriftError(f"固化选择器失效「{target}」：{selector}（{type(e).__name__}）")
    emit("heal", {
        "kind": "replay",
        "target": target,
        "strategy": entry.get("strategy") or "store",
        "selector": selector,
        "healed": False,
    })
    # 回放跳过了三级定位的隐式延迟，点击后要等页面网络请求静止再检查 loading，
    # 否则 spinner 还未挂载 DOM，wait_loading_disappear 就误判为"已完成"
    try:
        main.page.wait_for_load_state("networkidle", timeout=5_000)
    except Exception:  # noqa: BLE001
        pass  # 超时不报错，让 wait_loading_disappear 兜底
    main.wait_loading_disappear(timeout=8_000)
    return f"[回放] 已点击「{target}」（固化选择器）"


def _replay_dispatch(entry: dict, main: MainPage, ctx: dict, emit: EventCb) -> str | None:
    """回放单条脚本条目：按 action 直接分发，跳过自然语言解析。

    - click_element → _do_click_fixed（固化 selector，失效抛 ScriptDriftError）；
    - login/navigate/check_health/assert_visual/api_scenario → 原样真跑
      （navigate 本就语义定位、health/visual 每次结果不确定，不固化通过与否；
       失败抛 AssertionError → 直接判不通过，非漂移不触发 AI 兜底）；
    - skip → 原样跳过。
    """
    action = entry.get("action") or "skip"
    args = entry.get("args") or {}
    if action == "skip":
        return f"[回放·跳过] {entry.get('step','')}"
    if action == "click_element":
        return _do_click_fixed(main, entry, emit)
    return _dispatch(action, args, main, ctx, emit)


def _save_script_py(case_id, script_py: str) -> None:
    """把 Playwright Python 脚本写回 test_case.script_py。"""
    if not case_id:
        return
    try:
        from datetime import datetime
        from db import SessionLocal, TestCase  # type: ignore
        with SessionLocal() as s:
            c = s.get(TestCase, int(case_id))
            if not c:
                return
            c.script_py = script_py
            c.script_updated_at = datetime.now()
            s.commit()
    except Exception:  # noqa: BLE001
        pass


def _looks_like_selector_error(error: str) -> bool:
    """判断失败信息是否属于选择器失效（提示重新编译脚本）。"""
    keywords = ("TimeoutError", "locator.click", "locator.wait_for",
                 "locator(", "waiting for", "selector", "element not found",
                 "strict mode violation")
    err_lower = (error or "").lower()
    return any(k.lower() in err_lower for k in keywords)


_OLD_RUN_API = '''\
def _run_api_scenario(scenario_id: int, api_ctx: dict) -> None:'''

_NEW_RUN_API = '''\
_current_page = None

def _run_api_scenario(scenario_id: int, api_ctx: dict, page=None) -> None:'''

_OLD_SETUP_SCREENCAST = '''\
def _setup_screencast(context, page):
    """开启 CDP 投屏，每帧以 base64 JPEG 通过 emit(\'frame\') 上抛。"""
    cdp = context.new_cdp_session(page)'''

_NEW_SETUP_SCREENCAST = '''\
def _setup_screencast(context, page):
    """开启 CDP 投屏，每帧以 base64 JPEG 通过 emit(\'frame\') 上抛。"""
    global _current_page
    _current_page = page
    cdp = context.new_cdp_session(page)'''


def _patch_legacy_script(script_py: str) -> str:
    """对旧编译脚本就地 patch：注入 _current_page 及新版截帧逻辑。

    只在检测到旧版签名时才替换，新脚本不受影响。
    """
    import re as _re

    # 1. 注入 _current_page 变量并更新 _run_api_scenario 签名
    if _OLD_RUN_API in script_py:
        script_py = script_py.replace(_OLD_RUN_API, _NEW_RUN_API, 1)

    # 2. _setup_screencast 里注入 global _current_page 赋值
    if _OLD_SETUP_SCREENCAST in script_py:
        script_py = script_py.replace(_OLD_SETUP_SCREENCAST, _NEW_SETUP_SCREENCAST, 1)

    # 3. 用正则替换 _inner_emit + run_scenario 调用块（兼容 f-string 里的引号）
    #    匹配：def _inner_emit ... run_scenario(scenario, _inner_emit, ctx=api_ctx)
    #    替换为含 _frame_cb 和 frame_cb= 的新版
    _pat = _re.compile(
        r"(    def _inner_emit\(kind, payload\):.*?)"
        r"(    result = run_scenario\(scenario, _inner_emit, ctx=api_ctx\))",
        _re.DOTALL,
    )
    _frame_inject = (
        "    _page = page if page is not None else _current_page\n\n"
        "    def _frame_cb():\n"
        "        if _page is None:\n"
        "            return\n"
        "        try:\n"
        "            import base64 as _b64\n"
        "            _jpg = _page.screenshot(full_page=False, type='jpeg', quality=60)\n"
        "            emit('frame', data=_b64.b64encode(_jpg).decode())\n"
        "        except Exception:\n"
        "            pass\n\n"
    )

    def _replace_inner(m: "_re.Match") -> str:
        inner_emit_block = m.group(1)
        # 在 emit("log", msg=msg) 后追加 _frame_cb() 调用
        inner_emit_block = inner_emit_block.replace(
            '            emit("log", msg=msg)\n'
            '        elif kind == "api":\n',
            '            emit("log", msg=msg)\n'
            '            if payload.get("state") in ("passed", "failed"):\n'
            '                _frame_cb()\n'
            '        elif kind == "api":\n',
            1,
        )
        # 在 api 分支的 emit 后追加 _frame_cb()
        inner_emit_block = _re.sub(
            r'(            emit\("log", msg=f"\[接口\].*?\))\n$',
            r'\1\n            _frame_cb()\n',
            inner_emit_block,
            flags=_re.MULTILINE,
        )
        return (
            _frame_inject
            + inner_emit_block
            + "    result = run_scenario(scenario, _inner_emit, ctx=api_ctx, frame_cb=_frame_cb)"
        )

    if _pat.search(script_py):
        script_py = _pat.sub(_replace_inner, script_py, count=1)

    return script_py


def _run_playwright_script(
    script_py: str,
    emit: EventCb,
    case_id=None,
    cancel_event=None,
) -> dict:
    """把 Playwright Python 脚本写入临时文件，用 subprocess 隔离执行。

    脚本通过 stdout JSON 行回传 step/frame/log/done 事件；
    stderr 以 log 事件转发给前端（异常追踪等调试信息）。

    Returns:
        {status, step_results, screenshots, error}
    """
    step_results: list[dict] = []
    screenshots: list[str] = []
    status = "passed"
    error = ""

    # 把脚本写入临时文件
    # codegen 生成的脚本含 headless=False，执行前统一替换成 True
    script_py = script_py.replace("headless=False", "headless=True")
    # 兼容旧编译脚本：注入 _current_page + 新版 _setup_screencast / _run_api_scenario
    script_py = _patch_legacy_script(script_py)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(script_py)
        tmp.close()

        # 把配置从 config 读出来通过环境变量传给子进程
        env = dict(os.environ)
        try:
            env["PW_BASE_URL"] = config.get("base_url") or ""
        except Exception:
            pass
        try:
            # 登录就绪判据选择器 + token 键名（供生成的脚本使用）
            from core.main_page import ready_menu_selector
            env["PW_LOGIN_READY_SEL"] = ready_menu_selector() or ":text-is('首页'):visible"
            env["PW_TOKEN_KEY"] = config.get("api", "token_storage_key", default="token") or "token"
        except Exception:
            pass
        try:
            creds = config.get("account") or {}
            env["PW_USERNAME"] = creds.get("username") or ""
            env["PW_PASSWORD"] = creds.get("password") or ""
        except Exception:
            pass
        # 覆盖：默认测试环境（DB）优先
        try:
            from db import SessionLocal, TestEnv  # type: ignore
            with SessionLocal() as s:
                env_row = s.query(TestEnv).filter(TestEnv.is_default == True).first()  # noqa: E712
                if env_row:
                    env["PW_BASE_URL"] = (env_row.base_url or "").rstrip("/")
                    env["PW_USERNAME"] = env_row.username or ""
                    env["PW_PASSWORD"] = env_row.password or ""
        except Exception:
            pass

        env["PW_HEADLESS"] = "1"
        env["PW_PROJECT_ROOT"] = str(ROOT)

        proc = subprocess.Popen(
            [sys.executable, tmp.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        import threading as _threading

        # 保活：缓存子进程最后一帧，每 300ms 重推一次，保证执行期间投屏不断流
        _last_frame: dict = {}
        _keepalive_stop = _threading.Event()

        def _keepalive():
            while not _keepalive_stop.wait(timeout=0.3):
                frame = _last_frame.get("data")
                if frame:
                    emit("frame", {"data": frame})

        _keepalive_thread = _threading.Thread(target=_keepalive, daemon=True)
        _keepalive_thread.start()

        def _read_stderr():
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    emit("log", {"msg": f"[脚本stderr] {line}"})

        stderr_thread = _threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        done_received = False
        for raw_line in proc.stdout:
            # 取消信号：立即终止子进程
            if cancel_event and cancel_event.is_set():
                proc.kill()
                emit("log", {"msg": "用例执行已被手动停止"})
                status = "cancelled"
                error = "手动停止"
                break
            raw_line = raw_line.rstrip()
            if not raw_line:
                continue
            try:
                evt = json.loads(raw_line)
            except Exception:
                emit("log", {"msg": f"[脚本输出] {raw_line}"})
                continue

            kind = evt.pop("kind", None)
            if kind == "done":
                done_received = True
                status = evt.get("status", "failed")
                error = evt.get("error") or ""
            elif kind == "step":
                idx = evt.get("index", len(step_results))
                text = evt.get("text", "")
                state = evt.get("state", "")
                detail = evt.get("detail", "")
                if state == "passed":
                    step_results.append({"index": idx, "text": text, "ok": True, "detail": detail})
                elif state == "failed":
                    step_results.append({"index": idx, "text": text, "ok": False, "detail": detail})
                    if not error:
                        error = f"步骤#{idx}「{text}」失败：{detail}"
                emit(kind, evt | {"index": idx, "text": text, "state": state, "detail": detail})
            elif kind == "frame":
                _last_frame["data"] = evt.get("data", "")
                emit("frame", evt)
            else:
                if kind:
                    emit(kind, evt)

        _keepalive_stop.set()
        _keepalive_thread.join(timeout=1)
        proc.wait()
        stderr_thread.join(timeout=2)

        if not done_received:
            # codegen 生成的脚本没有 emit("done")，用退出码判定
            if proc.returncode == 0:
                status = "passed"
            else:
                status = "failed"
                if not error:
                    error = f"脚本进程异常退出（returncode={proc.returncode}）"
        elif proc.returncode != 0 and status == "passed":
            status = "failed"
            error = f"脚本进程退出码 {proc.returncode}，但未收到失败事件"

    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    return {
        "status": status,
        "step_results": step_results,
        "screenshots": screenshots,
        "error": error,
    }


def run_case(case: dict, emit: EventCb = _noop, run_id: int | None = None,
             cancel_event=None) -> dict:
    """执行一条用例：按 driver_type 分流到对应引擎。

    - driver_type='app' → core_app.app_runner.run_app_case（纯 ADB，无头/有头均无）；
    - 其余（缺省 'web'）→ Playwright 无头 Chromium + CDP 实时投屏。

    Web 执行策略（三模式）：
    1. 有 script_py → 直接跑 Playwright 脚本（快）；selector 漂移则重走模式2；
    2. 无 script_py → 自然语言三级定位执行（慢但可靠），成功后后台从轨迹编译
       Playwright 脚本落库，编译不阻塞本次返回；
    3. NL 执行也失败 → 直接返回失败。

    Args:
        case: {id,name,steps,driver_type?,device_serial?,...}
        emit: 事件回调（step / frame / log / heal / done）
        cancel_event: threading.Event，set 时中止执行（可选）
    Returns:
        {status, step_results, screenshots, error}
    """
    # ── App 用例分流：纯 ADB 引擎（无 Playwright） ───────────────────────
    if (case.get("driver_type") or "web") == "app":
        from core_app.app_runner import run_app_case
        return run_app_case(case, emit=emit, run_id=run_id, cancel_event=cancel_event)

    # ── 模式1：有 Playwright 脚本，直接回放 ────────────────────────────────
    script_py: str | None = case.get("script_py")
    if script_py:
        emit("log", {"msg": f"按已保存 Playwright 脚本执行（{len(script_py)} 字节）"})
        result = _run_playwright_script(script_py, emit, case.get("id"), cancel_event=cancel_event)
        if result["status"] == "cancelled":
            return result
        if result["status"] == "failed":
            # 已取消时不回退 NL 重跑，直接返回 cancelled
            if cancel_event and cancel_event.is_set():
                result["status"] = "cancelled"
                return result
            # 任何失败（selector 失效 / 断言失败 / 业务错误）都回退 NL 执行重跑，
            # NL 成功后从新轨迹重编译脚本（自愈）
            emit("log", {"msg": f"脚本执行失败，回退自然语言执行并重编译… 原因：{result['error'][:120]}"})
            result = _run_nl_and_compile_async(case, emit, run_id, cancel_event=cancel_event)
        return result

    # ── 模式2：无脚本，自然语言执行 + 后台编译 ─────────────────────────────
    emit("log", {"msg": "首次执行：自然语言三级定位，成功后后台编译 Playwright 脚本"})
    return _run_nl_and_compile_async(case, emit, run_id, cancel_event=cancel_event)


def _run_nl_and_compile_async(
    case: dict, emit: EventCb, run_id: int | None = None, cancel_event=None
) -> dict:
    """自然语言三级定位执行一条用例，成功后后台线程从执行轨迹编译 Playwright 脚本。

    - 执行过程与原 run_case NL 路径完全相同（不改任何行为）；
    - 执行成功后把 compiled 轨迹交给后台线程调 compile_from_trace，不阻塞本次返回；
    - 编译成功自动落库，下次执行走脚本路径。
    """
    screenshots: list[str] = []
    status = "passed"
    error = ""

    with browser_mod.launch_context(headless=True) as (_browser, context):
        page = context.new_page()

        # 保活线程共享的最后一帧（screencast 回调写入，保活线程只读推送）
        _last_frame: dict = {"data": None}

        # 开 CDP 投屏，逐帧 emit 给前端
        cdp = _start_screencast(context, page, emit, _last_frame)

        # 监听新标签页：点击「远程座舱」这类 target=_blank 会在 context 上新建 page。
        # 事件回调里只做标记，实际的 CDP 切换 / main.page 切换放主循环里同步做，
        # 避免在 Playwright greenlet 事件回调里跨线程操作导致冲突（与 recorder 一致）。
        pending_pages: list = []

        def _on_new_page(new_page):
            pending_pages.append(new_page)
            emit("log", {"msg": f"检测到新标签页：{new_page.url or 'about:blank'}"})

        context.on("page", _on_new_page)

        # 会话登录一次
        login = LoginPage(page)
        login.open()
        creds = config.get("account")
        login.login(creds["username"], creds["password"])
        main = MainPage(page)

        # 登录过程若产生了新页（一般不会），先清掉，避免误切
        pending_pages.clear()

        # 用可变闭包持有当前活动页与 CDP，_switch_to_new_page 内部就地更新
        state = {"page": page, "cdp": cdp}

        # 投屏保活：无头模式静止页面 screencast 会停帧，起一个补帧线程。
        # 保活线程只重推上一帧的 base64 数据，完全不调 Playwright API，
        # 避免跨 greenlet/线程调用导致 "Cannot switch to a different thread"。
        import threading as _threading
        _keepalive_stop = _threading.Event()

        def _keepalive():
            while not _keepalive_stop.wait(timeout=0.2):
                data = _last_frame.get("data")
                if data:
                    emit("frame", {"data": data})

        _keepalive_thread = _threading.Thread(target=_keepalive, daemon=True)
        _keepalive_thread.start()

        def _attach_page_diagnostics(target_page) -> None:
            """给新页挂 pageerror / requestfailed 监听，用例真出错时能定位。"""
            def _on_pageerror(err):
                emit("log", {"msg": f"[新页 pageerror] {err}"})

            def _on_requestfailed(req):
                try:
                    # 只记录被测站点自身的请求失败；第三方域名（Agora/埋点/CDN 等）
                    # 在无头环境里必然有失败，属正常现象，过滤掉避免日志噪音
                    from core import config as _cfg
                    base = (_cfg.get("base_url") or "").rstrip("/")
                    if base and not req.url.startswith(base):
                        return
                    emit("log", {"msg": f"[新页 请求失败] {req.method} {req.url} - {req.failure}"})
                except Exception:
                    pass

            target_page.on("pageerror", _on_pageerror)
            target_page.on("requestfailed", _on_requestfailed)

        def _drain_new_page() -> str:
            """把最新的新标签页切成活动页：停旧 CDP → 开新 CDP → 换 main.page。

            返回新标签页的 URL（无新页时返回空串）。
            """
            if not pending_pages:
                return ""
            # 只取最后一个（多次点击可能堆积），前面的忽略
            new_page = pending_pages[-1]
            pending_pages.clear()
            _attach_page_diagnostics(new_page)
            try:
                new_page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass  # 即便超时也尝试切，后续步骤自己会等
            # 停旧 CDP
            old_cdp = state.get("cdp")
            if old_cdp is not None:
                try:
                    old_cdp.detach()
                except Exception:
                    pass
            # 开新 CDP + 切活动页
            state["page"] = new_page
            main.page = new_page
            new_url = new_page.url or ""
            emit("log", {"msg": f"已切换到新标签页：{new_url}"})
            # 等页面网络空闲再推首帧，避免投屏停在 loading spinner。
            # 座舱有 SSE 长连接不会真正 idle，靠 8s 超时兜底。
            try:
                new_page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            # 重新开 CDP 投屏（切页后旧 CDP 已停）
            # 注意：必须在 main.page 切换后再调，保证在同一 greenlet 里完成
            state["cdp"] = _start_screencast(context, new_page, emit, _last_frame)
            _push_frame(new_page, emit)
            return new_url

        def _reset_baseline() -> None:
            """多趟之间（回放→AI兜底→验证）复位浏览器基线状态。

            回放中途失败时浏览器停在半路某页；若此前点开过新标签页，state['page']
            已被 _drain_new_page 切到新标签、CDP 也在新页上（跨趟污染）。故：
            切回原始 page（detach 新页 CDP、原页重启投屏）→ 清 pending → 回登录页复位。
            下一趟脚本首步（多为「登录」）幂等，能从干净基线重跑。
            """
            if state["page"] is not page:
                old_cdp = state.get("cdp")
                if old_cdp is not None:
                    try:
                        old_cdp.detach()
                    except Exception:
                        pass
                state["page"] = page
                state["cdp"] = _start_screencast(context, page, emit, _last_frame)
                main.page = page
            pending_pages.clear()
            try:
                base_url = config.get("base_url").rstrip("/")
                login_path = config.get("login_path", default="/login")
                page.goto(f"{base_url}{login_path}", wait_until="domcontentloaded")
            except Exception:
                pass
            _push_frame(page, emit)

        def _execute_steps(steps: list, step_fn, record: bool):
            """跑一趟步骤序列，返回 (status, error, step_results, screenshots, compiled)。

            抽成 run_case 内嵌套函数以复用 state/pending_pages/_drain_new_page/main/context/emit
            这些闭包状态；每趟用独立的结果列表，供回放/兜底/验证多趟调用互不串味。

            Args:
                step_fn: (idx, step_obj, text, parallels, ctx) -> detail 描述串；
                         nl_fn 走自然语言解析，replay_fn 走脚本回放。
                record: True 时把每步实际动作记入 ctx['_rec'] 并编译进 compiled。
            """
            sr: list[dict] = []
            sh: list[str] = []
            st = "passed"
            err = ""
            compiled: list[dict] = []
            ctx: dict = {}
            for idx, step in enumerate(steps):
                # 每步开始前检查取消信号
                if cancel_event and cancel_event.is_set():
                    st = "cancelled"
                    err = "手动停止"
                    emit("log", {"msg": "用例执行已被手动停止"})
                    break
                text = _step_text(step)
                parallels = _step_parallels(step)
                if record:
                    ctx["_rec"] = {}
                emit("step", {"index": idx, "text": text, "state": "running"})
                try:
                    result = step_fn(idx, step, text, parallels, ctx)
                    # 步骤执行后可能触发了新标签页（如点击「远程座舱」）
                    new_tab_url = _drain_new_page()
                    # 若该步骤触发了新标签页，把 URL 写入 _rec 供脚本编译生成 expect_popup()
                    if new_tab_url and isinstance(ctx.get("_rec"), dict):
                        ctx["_rec"]["new_tab_url"] = new_tab_url
                    sr.append({"index": idx, "text": text, "ok": True, "detail": result})
                    emit("step", {"index": idx, "text": text, "state": "passed", "detail": result})
                    _push_frame(state["page"], emit)  # 补一帧最新画面，消除投屏滞后
                    if record:
                        compiled.append(_compile_entry(step, ctx.get("_rec") or {}, parallels))
                except Exception as e:  # noqa: BLE001
                    st = "failed"
                    err = f"步骤#{idx}「{text}」失败：{e}"
                    sr.append({"index": idx, "text": text, "ok": False, "detail": str(e)})
                    emit("step", {"index": idx, "text": text, "state": "failed", "detail": str(e)})
                    _push_frame(state["page"], emit)  # 失败也补一帧当前画面
                    shot = _save_shot(state["page"], f"run_{case.get('id','x')}_fail_{idx}")
                    if shot:
                        sh.append(shot)
                    # 定位漂移须穿透到编排层触发 AI 兜底；其余失败就地中断本趟
                    if isinstance(e, ScriptDriftError):
                        raise
                    break
            return st, err, sr, sh, compiled

        def _nl_fn(idx, step_obj, text, parallels, ctx):
            """自然语言执行：并行步骤走并发编排，否则解析→分发。"""
            if parallels:
                return _run_step_with_parallels(text, parallels, ctx, main, emit)
            # 把步骤对象里的手动定位提示透传给 _interpret_step → _dispatch → _do_click
            hint = _step_locator_hint(step_obj)
            return _interpret_step(text, ctx, main, emit, locator_hint=hint,
                                   cancel_event=cancel_event)

        def _make_replay_fn(script_entries: list):
            """构造回放步骤函数：按脚本条目回放；条目缺失则回退自然语言。"""
            def _replay_fn(idx, step_obj, text, parallels, ctx):
                entry = script_entries[idx] if idx < len(script_entries) else None
                if entry is None:
                    return _interpret_step(text, ctx, main, emit, cancel_event=cancel_event)
                if parallels:
                    # 并行步骤回放：UI 侧走回放分发，ScriptDriftError 穿透并发汇总
                    ui_fn = lambda t, c, m, e: _replay_dispatch(entry, m, c, e)
                    return _run_step_with_parallels(text, parallels, ctx, main, emit, ui_fn=ui_fn)
                return _replay_dispatch(entry, main, ctx, emit)
            return _replay_fn

        # ── 无条件 NL 执行（`_run_nl_and_compile_async` 的唯一路径）──────────
        # 旧的 JSON script 回放分支已废弃，`script` 字段仍可能有历史数据，但不再使用。
        # 始终走自然语言三级定位执行，成功后从真实轨迹编译 Playwright 脚本。
        status, error, step_results, screenshots, compiled = _execute_steps(
            case.get("steps", []), _nl_fn, record=True
        )

        _push_frame(state["page"], emit)  # 结束前再补最终画面，避免停在旧帧
        # 先清 cdp 引用再停保活线程：保活线程检测到 cdp_now is None 会跳过截图，
        # 避免浏览器关闭竞态导致 TargetClosedError
        _final_cdp = state.get("cdp")
        state["cdp"] = None
        _keepalive_stop.set()
        _keepalive_thread.join(timeout=1)
        if _final_cdp is not None:
            try:
                _final_cdp.detach()
            except Exception:
                pass

    emit("done", {"status": status, "error": error})

    # 执行成功 → 后台从轨迹编译 Playwright 脚本（不阻塞本次返回）
    # cancelled 时不编译：轨迹不完整，且用户已主动停止
    if status == "passed" and compiled:
        import threading as _th
        _case_snap = dict(case)
        _compiled_snap = list(compiled)

        def _bg_compile():
            # 二次检查取消信号（用户在执行结束前就已停止）
            if cancel_event and cancel_event.is_set():
                return
            try:
                from script_compiler import compile_from_trace  # type: ignore
                script_py = compile_from_trace(_case_snap, _compiled_snap)
                if script_py:
                    _save_script_py(_case_snap.get("id"), script_py)
                    emit("log", {"msg": f"Playwright 脚本已从执行轨迹编译落库（{len(script_py)} 字节）"})
            except Exception:  # noqa: BLE001
                pass

        _th.Thread(target=_bg_compile, daemon=True).start()

    return {
        "status": status,
        "step_results": step_results,
        "screenshots": screenshots,
        "error": error,
    }


def _start_screencast(context, page, emit: EventCb, last_frame: dict | None = None):
    """开启 CDP 屏幕投送，每帧以 base64 JPEG 通过 emit('frame') 上抛。

    last_frame: 可选的 {"data": ...} dict，每收到新帧时同步更新，
                供调用方的保活线程重复推送（不调任何 Playwright API）。
    """
    cdp = context.new_cdp_session(page)

    def on_frame(params):
        data = params["data"]
        emit("frame", {"data": data})  # base64 JPEG
        if last_frame is not None:
            last_frame["data"] = data  # 保活线程只读此值，不跨 greenlet
        try:
            cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:
            pass

    cdp.on("Page.screencastFrame", on_frame)
    cdp.send("Page.startScreencast", {
        "format": "jpeg",
        "quality": 60,
        "maxWidth": 1440,
        "maxHeight": 900,
        "everyNthFrame": 1,
    })
    return cdp


def _push_frame(page, emit: EventCb) -> None:
    """主动截取当前视口一帧推给前端。

    CDP screencast 在页面剧变（如导航跳转）时会丢帧，导致投屏画面停在旧页。
    每步完成后主动补一帧最新画面，消除"看起来没跳转"的滞后。
    """
    try:
        import base64
        # JPEG（quality 60）而非 PNG：补帧体积远小于无损 PNG，传输更快、
        # 与 CDP screencast 帧格式一致（前端统一按 JPEG 解码）。
        jpg = page.screenshot(full_page=False, type="jpeg", quality=60)
        emit("frame", {"data": base64.b64encode(jpg).decode()})
    except Exception:
        pass


def _save_shot(page, name: str) -> str | None:
    """整页截图到 screenshots/platform/，返回相对路径。"""
    try:
        out = ROOT / config.get("screenshot_dir", default="screenshots") / "platform"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        return f"platform/{name}.png"
    except Exception:
        return None
