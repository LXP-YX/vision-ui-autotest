"""App 用例执行编排：把一条 APP 用例的中文步骤跑起来。

对标 platform/worker/runner.py 的自然语言执行路径，差异：
- 引擎为 core_app（DeviceDriver 抽象，统一 Appium 实现：Android=UiAutomator2 /
  iOS=XCUITest，同一份用例可跨平台复用），无 Playwright/CDP；
- 投屏保活用 driver.screenshot() 周期截图（与平台无关），非 CDP startScreencast；
- 步骤解析走 core_app.planner（App 动作集），LLM 不可用回退规则式；
- 事件协议与 WEB 端一致（step/frame/log/heal/done）。

执行目标直接取自用例字段（device_serial + app_package/app_activity）；
编译产物等独立脚本可通过 env_override 直接传入执行目标，无需连平台 DB。

由 run_manager / execution_manager 按 driver_type=='app' 分流调用：
run_app_case(case, emit, ...)。
"""

from __future__ import annotations

import base64
import io
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 接口场景复用平台 worker（api_runner 纯 HTTP，引擎无关）与 backend（db），
# 与 self_healing.py 注入 backend 同理；不可用时 api_scenario 动作降级为不可用。
_BK = ROOT / "platform" / "backend"
if str(_BK) not in sys.path:
    sys.path.insert(0, str(_BK))
_WK = ROOT / "platform" / "worker"
if str(_WK) not in sys.path:
    sys.path.insert(0, str(_WK))

from core.config import load_config  # noqa: E402  （纯 yaml 读取，不含 WEB 环境逻辑）
from core_app.drivers import create_driver  # noqa: E402
from core_app.drivers.base import DeviceDriver, LOGICAL_KEYS  # noqa: E402

EventCb = Callable[[str, dict], None]


def _noop(kind: str, payload: dict) -> None:
    pass


def _step_text(raw) -> str:
    if isinstance(raw, dict):
        return str(raw.get("text", "")).strip()
    return str(raw).strip()


def _step_locator_hint(raw) -> dict | None:
    if isinstance(raw, dict):
        h = raw.get("locator_hint")
        if isinstance(h, dict) and h.get("by") and h.get("value"):
            return h
    return None


# ------------------------- APP 执行目标解析 -------------------------

def _resolve_app_env(case: dict) -> tuple[Optional[dict], str]:
    """从用例字段解析执行目标（设备 + 被测 App）。返回 (env, "") 或 (None, 错误信息)。

    APP 测试环境管理已移除（测试环境管理仅对 WEB 开放），执行目标直接取自用例：
    device_serial（执行设备）+ app_package/app_activity（被测 App）。
    env_override 传入时由 run_app_case 跳过本函数，独立脚本无需平台 DB。
    """
    serial = case.get("device_serial") or ""
    if not serial:
        return None, "用例未指定执行设备（device_serial），请在 APP 工作台选择设备后保存用例"
    return {
        "name": "用例设备",
        "platform": "android",
        "device_id": serial,
        "app_id": case.get("app_package", ""),
        "app_activity": case.get("app_activity", ""),
    }, ""


# ------------------------- 投屏 -------------------------

def _screenshot_jpeg_b64(driver: DeviceDriver) -> str | None:
    """截图 → JPEG base64（投屏帧用，quality 60 压体积）。失败返回 None。"""
    try:
        png = driver.screenshot()
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        if im.mode != "RGB":
            im = im.convert("RGB")  # PNG 常为 RGBA，JPEG 不支持 alpha
        w0, h0 = im.size
        # 限宽 900，等比缩放
        if w0 > 900:
            im = im.resize((900, int(h0 * 900 / w0)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


def _push_frame(driver: DeviceDriver, emit: EventCb) -> None:
    """主动补一帧当前画面（投屏保活用）。"""
    data = _screenshot_jpeg_b64(driver)
    if data:
        emit("frame", {"data": data})


def _keepalive_loop(driver: DeviceDriver, emit: EventCb, stop: threading.Event,
                    interval: float = 0.4) -> None:
    """保活线程：周期截图推帧，保证执行期间投屏不断流。

    与 WEB 端不同——这里每帧都真的截图（无 CDP 推流），interval 取 0.4s
    折中流畅度与 CPU/带宽。线程只读 driver（各平台驱动的并发截图按最优努力处理，
    单帧失败静默跳过）。
    """
    while not stop.wait(timeout=interval):
        if stop.is_set():
            break
        _push_frame(driver, emit)


# ------------------------- 动作执行 -------------------------

def _do_tap(driver: DeviceDriver, target: str, emit: EventCb,
            hint: dict | None = None) -> str:
    from core_app import self_healing
    hit = self_healing.find_element(driver, target, locator_hint=hint)
    if not hit:
        raise AssertionError(f"未找到元素「{target}」（Store/语义/VLM 均失败，疑为功能缺陷）")
    strat = hit["strategy"]
    emit("heal", {
        "kind": "vlm" if strat == "vlm" else "locate",
        "target": target,
        "strategy": strat,
        "selector": f'{hit["by"]}={hit["value"]}',
        "healed": strat == "vlm",
    })
    cx, cy = hit["center"]
    driver.tap(cx, cy)
    return f"已点击「{target}」（策略 {strat}，坐标 {cx},{cy}）"


def _is_editable_node(n) -> bool:
    """控件树节点是否为可输入控件（Android=EditText；iOS=TextField/SecureTextField/SearchField/TextView）。"""
    cls = (n.cls or "")
    return cls in ("android.widget.EditText", "XCUIElementTypeTextField",
                   "XCUIElementTypeSecureTextField", "XCUIElementTypeSearchField",
                   "XCUIElementTypeTextView")


def _find_input(driver: DeviceDriver, target: str):
    """定位输入框：先等可输入控件出现（状态等待，页面切换后输入框渲染较慢），
    再走三级定位，并要求命中可输入控件——避免把同名标题/入口当输入框。"""
    import time as _t
    from core_app import self_healing
    deadline = _t.monotonic() + _INPUT_WAIT_S
    while _t.monotonic() < deadline:
        try:
            if any(_is_editable_node(n) for n in driver.ui_tree()):
                break
        except Exception:  # noqa: BLE001
            pass
        _t.sleep(0.5)
    hit = self_healing.find_element(driver, target)
    if hit:
        try:
            nodes = driver.ui_tree()
        except Exception:  # noqa: BLE001
            nodes = []
        editables = [n for n in nodes if _is_editable_node(n) and n.area > 0]
        if editables:
            same = [n for n in editables if tuple(n.bounds) == tuple(hit["bounds"])]
            pick = (same or editables)[0]
            cx, cy = pick.center
            return {**hit, "by": "type", "value": pick.cls,
                    "bounds": list(pick.bounds), "center": [cx, cy]}
    return hit


def _do_type(driver: DeviceDriver, target: str, text: str, emit: EventCb) -> str:
    from core_app import self_healing
    hit = _find_input(driver, target)
    if not hit:
        raise AssertionError(f"未找到输入框「{target}」（三级定位均失败）")
    driver.type_into(hit, text)  # 双平台均为元素级 send_keys（原生 Unicode）
    return f"已在「{target}」输入「{text}」"


def _do_swipe(driver: DeviceDriver, direction: str) -> str:
    w, h = driver.screen_size()
    cx = w // 2
    # 手势方向：手指从起点滑到终点。down=手指下移（看上方内容），up=手指上移（看下方内容）
    edge = int(h * 0.3)
    mid_y = h // 2
    points = {
        "up":    (cx, mid_y + edge, cx, mid_y - edge),  # 手指上移
        "down":  (cx, mid_y - edge, cx, mid_y + edge),  # 手指下移
        "left":  (cx + int(w * 0.3), mid_y, cx - int(w * 0.3), mid_y),
        "right": (cx - int(w * 0.3), mid_y, cx + int(w * 0.3), mid_y),
    }
    d = (direction or "").lower()
    if d not in points:
        raise AssertionError(f"未知滑动方向：{direction}（仅支持 up/down/left/right）")
    x1, y1, x2, y2 = points[d]
    driver.swipe(x1, y1, x2, y2, duration_ms=400)
    return f"已向 {d} 滑动"


def _do_press_key(driver: DeviceDriver, key, name: str = "") -> str:
    """按逻辑键。兼容旧用例里的数字 keycode（仅 Android 有意义）。"""
    driver.press_key(key)
    label = name or str(key)
    return f"已按键 {label}（{driver.platform}）"


def _do_assert_visual(driver: DeviceDriver, claim: str, emit: EventCb) -> str:
    from core_app import vision
    r = vision.assert_visual(driver, claim)
    emit("heal", {"kind": "assert", "claim": claim,
                   "passed": r["passed"], "reason": r["reason"]})
    if not r["passed"]:
        raise AssertionError(f"视觉断言未通过：{claim} —— {r['reason']}")
    return f"视觉断言通过：{claim} —— {r['reason']}"


def _do_launch(driver: DeviceDriver, args: dict, env: dict) -> str:
    """启动被测 App：优先取步骤解析出的 app_id，缺省用环境绑定的 app_id。"""
    app_id = args.get("app_id") or env.get("app_id") or ""
    if not app_id:
        raise AssertionError("未指定被测 App（步骤未给 app_id 且环境未绑定 app_id）")
    activity = args.get("activity") or env.get("app_activity") or ""
    return driver.launch(app_id, activity)


def _do_wait(seconds: float, driver: DeviceDriver, emit: EventCb) -> str:
    """等待指定秒数，期间每 400ms 截一帧推给前端。"""
    seconds = max(0.0, float(seconds))
    elapsed = 0.0
    interval = 0.4
    while elapsed < seconds:
        _push_frame(driver, emit)
        step = min(interval, seconds - elapsed)
        time.sleep(step)
        elapsed += step
    return f"已等待 {seconds:.0f} 秒"


def _do_api_scenario(scenario_id: int, ctx: dict, emit: EventCb,
                     cancel_event=None) -> str:
    """执行接口场景：从 db 载入 → 复用 api_runner.run_scenario → 变量双向共享。

    与 WEB 端 worker/runner.py 的 _do_api_scenario 对齐，差异仅在投屏：
    APP 无 MainPage，接口场景执行期间由 run_app_case 的独立保活线程（_keepalive_loop）
    持续截图推帧，故这里不传 frame_cb，step/done 事件同样不外抛、改以 log 前缀展示。

    - ctx（APP 用例变量上下文）传入场景作为初始变量，场景内新提取变量就地写回供后续步骤引用；
    - 场景任一步骤断言未通过 → 抛 AssertionError，run_app_case 立即中断后续 APP 步骤；
    - base_url 由 api_runner 内部统一用 web 默认环境 URL（引擎无关，web/app 共用）。
    """
    try:
        from db import ApiScenario, SessionLocal  # type: ignore
        from api_runner import run_scenario
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"接口场景不可用（依赖缺失：{e}）")

    with SessionLocal() as s:
        sc = s.get(ApiScenario, scenario_id)
        if not sc:
            raise AssertionError(f"接口场景不存在：id={scenario_id}")
        scenario = sc.to_dict()

    scenario_name = scenario.get("name") or f"scenario#{scenario_id}"

    def _inner_emit(kind: str, payload: dict) -> None:
        if kind == "done":
            return  # 场景 done 不外抛，避免前端把整个 APP 用例判成结束
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

    emit("log", {"msg": f"▶ 进入接口场景「{scenario_name}」（携带上下文变量 {len(ctx)} 个）"})
    result = run_scenario(scenario, _inner_emit, ctx=ctx, cancel_event=cancel_event)

    result_status = result.get("status")
    if result_status == "cancelled":
        raise AssertionError("手动停止")
    if result_status != "passed":
        raise AssertionError(
            f"接口场景「{scenario_name}」未通过：{result.get('error') or '断言失败'}"
        )
    passed_cnt = sum(1 for r in result.get("step_results", []) if r.get("ok"))
    return f"接口场景「{scenario_name}」通过（{passed_cnt} 步）"


def _dispatch(action: str, args: dict, driver: DeviceDriver, emit: EventCb,
              env: dict, hint: dict | None = None, ctx: dict | None = None,
              cancel_event=None) -> str | None:
    if action == "tap_element":
        target = args.get("target", "")
        if target:
            return _do_tap(driver, target, emit, hint=hint)
    elif action == "type_text":
        target = args.get("target", "")
        text = args.get("text", "")
        if target and text is not None:
            return _do_type(driver, target, text, emit)
    elif action == "swipe":
        if args.get("direction"):
            return _do_swipe(driver, args["direction"])
    elif action == "press_key":
        if args.get("key") is not None or args.get("keycode") is not None:
            return _do_press_key(driver, args.get("key") or args.get("keycode"),
                                 args.get("name", ""))
    elif action == "assert_visual":
        claim = args.get("claim", "")
        if claim:
            return _do_assert_visual(driver, claim, emit)
    elif action == "launch_app":
        return _do_launch(driver, args, env)
    elif action == "wait":
        return _do_wait(args.get("seconds", 1), driver, emit)
    elif action == "api_scenario":
        sid = args.get("id")
        if sid is not None and ctx is not None:
            return _do_api_scenario(int(sid), ctx, emit, cancel_event=cancel_event)
    return None


# ------------------------- 规则式回退 -------------------------

def _interpret_step_rule(step: str) -> dict | None:
    """规则式解析器（AI 不可用时的回退）。只解析、不执行。"""
    s = step.strip()
    m = re.search(r"(?:等待|暂停|停留)\s*(\d+(?:\.\d+)?)\s*[秒s]?", s, re.IGNORECASE)
    if m:
        return {"action": "wait", "args": {"seconds": float(m.group(1))}}
    if any(k in s for k in ("返回键", "按返回", "back", "BACK")):
        return {"action": "press_key", "args": {"key": "back", "name": "返回键"}}
    if any(k in s for k in ("HOME键", "按home", "回桌面", "home", "HOME")):
        return {"action": "press_key", "args": {"key": "home", "name": "HOME键"}}
    for d, kw in [("up", ("上滑", "向上滑")), ("down", ("下滑", "向下滑")),
                  ("left", ("左滑", "向左滑")), ("right", ("右滑", "向右滑"))]:
        if any(k in s for k in kw):
            return {"action": "swipe", "args": {"direction": d}}
    if any(k in s for k in ("点击", "按下", "打开", "选择")) and "输入" not in s:
        # 抽取目标（去动词前缀），交由三级定位处理
        target = re.sub(r"^(点击|按下|打开|选择)", "", s).strip()
        if target:
            return {"action": "tap_element", "args": {"target": target}}
    if any(k in s for k in ("输入", "填写", "填入")):
        m = re.search(r"(?:在|把)?\s*([^，,]+?)\s*(?:输入|填写|填入)\s*([^，,\s]+)", s)
        if m:
            return {"action": "type_text", "args": {"target": m.group(1), "text": m.group(2)}}
    return None


def _plan_step(step: str, platform: str) -> dict | None:
    """解析步骤：接口场景正则 → 规则式（零 LLM）→ AI Planner（平台感知动作集）。"""
    m = re.match(r"\s*\[\s*接口场景\s*#\s*(\d+)\s*\]", step)
    if m:
        return {"action": "api_scenario", "args": {"id": int(m.group(1))}}

    plan = _interpret_step_rule(step)
    if plan is not None:
        return plan
    from core_app.planner import plan_step
    return plan_step(step, platform)


def _interpret_step(step: str, driver: DeviceDriver, emit: EventCb,
                    env: dict, hint: dict | None = None, ctx: dict | None = None,
                    cancel_event=None) -> str:
    """解析并执行一条中文步骤。"""
    plan = _plan_step(step, driver.platform)
    if plan:
        action, args = plan["action"], plan.get("args") or {}
        if action == "tap_element" and hint:
            args = {**args, "locator_hint": hint}
        if action not in ("tap_element", "api_scenario"):
            emit("log", {"msg": f"[AI] 解析为 {action}{args or ''}"})
        result = _dispatch(action, args, driver, emit, env, hint=hint,
                           ctx=ctx, cancel_event=cancel_event)
        if result is not None:
            return result
    emit("log", {"msg": f"[跳过] 未识别步骤：{step.strip()}"})
    return f"[跳过] {step.strip()}"


# ------------------------- 主入口 -------------------------

def run_app_case(case: dict, emit: EventCb = _noop, run_id: int | None = None,
                 cancel_event=None,
                 env_override: dict | None = None) -> dict:
    """执行一条 APP 用例：Appium 引擎（双平台同框架）+ 实时投屏 + 自然语言三级定位。

    Args:
        case: {id,name,steps,driver_type,device_serial,app_package,app_activity}
        emit: 事件回调（step / frame / log / heal / done）
        cancel_event: threading.Event，set 时中止
        env_override: 直接给出执行目标 dict（平台/设备/App），跳过用例字段解析；
                      供独立脚本使用（编译产物无需连平台 DB 即可跨平台运行）
    Returns:
        {status, step_results, screenshots, error}
    """
    if env_override:
        env, err = env_override, ""
    else:
        env, err = _resolve_app_env(case)
    if not env:
        emit("done", {"status": "error", "error": err})
        return {"status": "error", "step_results": [], "screenshots": [], "error": err}

    platform = (env.get("platform") or "android").lower()
    device_id = env.get("device_id") or ""
    if not device_id:
        msg = "用例未配置执行设备（device_serial 为空）"
        emit("done", {"status": "error", "error": msg})
        return {"status": "error", "step_results": [], "screenshots": [], "error": msg}

    try:
        driver = create_driver(platform, device_id, env.get("app_id", ""),
                               env.get("app_activity", ""),
                               device_name=env.get("device_name", ""))
    except ValueError as e:
        emit("done", {"status": "error", "error": str(e)})
        return {"status": "error", "step_results": [], "screenshots": [], "error": str(e)}

    try:
        driver.connect()  # 双平台：创建 Appium session（首次较慢）
        status_info = driver.status()
    except Exception as e:  # noqa: BLE001
        driver.close()
        emit("done", {"status": "error", "error": f"设备状态获取失败：{e}"})
        return {"status": "error", "step_results": [], "screenshots": [],
                "error": f"设备状态获取失败：{e}"}
    if not status_info["online"]:
        driver.close()
        msg = f"设备 {device_id} 离线（平台 {platform}），请确认设备已连接" + (
            "且 Appium Server 已启动、appium-uiautomator2-driver 已安装" if platform == "android"
            else "且 Appium Server 已启动、WebDriverAgent 已就绪")
        emit("done", {"status": "error", "error": msg})
        return {"status": "error", "step_results": [], "screenshots": [], "error": msg}

    emit("log", {"msg": f"执行目标：{platform} 设备 {device_id} 在线"
                        + (f"（{status_info['width']}x{status_info['height']}）"
                           if status_info["width"] else "")})

    screenshots: list[str] = []
    step_results: list[dict] = []
    status = "passed"
    error = ""
    # 用例变量上下文：接口场景（api_scenario）与之双向共享变量（token 等），跨步骤累积
    ctx: dict = {}

    # 投屏保活线程
    stop = threading.Event()
    t = threading.Thread(target=_keepalive_loop, args=(driver, emit, stop), daemon=True)
    t.start()

    def _check_cancel() -> bool:
        return bool(cancel_event and cancel_event.is_set())

    try:
        steps = case.get("steps", []) or []
        for idx, step in enumerate(steps):
            if _check_cancel():
                status = "cancelled"
                error = "手动停止"
                emit("log", {"msg": "用例执行已被手动停止"})
                break
            text = _step_text(step)
            hint = _step_locator_hint(step)
            emit("step", {"index": idx, "text": text, "state": "running"})
            try:
                detail = _interpret_step(text, driver, emit, env, hint=hint,
                                         ctx=ctx, cancel_event=cancel_event)
                step_results.append({"index": idx, "text": text, "ok": True, "detail": detail})
                emit("step", {"index": idx, "text": text, "state": "passed", "detail": detail})
                _push_frame(driver, emit)
            except Exception as e:  # noqa: BLE001
                status = "failed"
                error = f"步骤#{idx}「{text}」失败：{e}"
                step_results.append({"index": idx, "text": text, "ok": False, "detail": str(e)})
                emit("step", {"index": idx, "text": text, "state": "failed", "detail": str(e)})
                _push_frame(driver, emit)
                shot = _save_shot(driver, f"app_run_{case.get('id', 'x')}_fail_{idx}")
                if shot:
                    screenshots.append(shot)
                break
    finally:
        stop.set()
        t.join(timeout=2)
        _push_frame(driver, emit)  # 结束前补最终画面
        driver.close()             # iOS：退出 Appium session；Android：无操作

    emit("done", {"status": status, "error": error})
    return {"status": status, "step_results": step_results,
            "screenshots": screenshots, "error": error}


def _save_shot(driver: DeviceDriver, name: str) -> str | None:
    """失败截图到 screenshots/platform/，返回相对路径。"""
    try:
        png = driver.screenshot()
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        shot_dir = load_config().get("screenshot_dir") or "screenshots"
        out = ROOT / shot_dir / "platform"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{name}.png"
        im.save(str(path), "PNG")
        return f"platform/{name}.png"
    except Exception:  # noqa: BLE001
        return None
