"""App 用例执行编排：把一条 App 用例的中文步骤跑起来。

对标 platform/worker/runner.py 的自然语言执行路径，差异：
- 引擎为 core_app（纯 ADB），无 Playwright/CDP；
- 投屏保活用 adb screencap 周期截图，非 CDP startScreencast；
- 步骤解析走 core_app.planner（App 动作集），LLM 不可用回退规则式；
- 事件协议与 Web 端一致（step/frame/log/heal/done），前端 CasesView 可复用。

由 worker/runner.py 按 driver_type=='app' 分流调用：run_app_case(case, emit, ...)。
"""

from __future__ import annotations

import base64
import io
import sys
import threading
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402
from core_app.driver import AppDriver  # noqa: E402

EventCb = Callable[[str, dict], None]

# 常用按键码
_KEYCODES = {"home": 3, "back": 4, "enter": 66, "menu": 82,
             "volume_up": 24, "volume_down": 25, "power": 26, "recent": 187}


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


def _screenshot_jpeg_b64(driver) -> str | None:
    """adb 截图 → JPEG base64（投屏帧用，quality 60 压体积）。失败返回 None。"""
    try:
        png = driver.screenshot()
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        if im.mode != "RGB":
            im = im.convert("RGB")  # screencap PNG 常为 RGBA，JPEG 不支持 alpha
        w0, h0 = im.size
        # 限宽 900，等比缩放
        if w0 > 900:
            im = im.resize((900, int(h0 * 900 / w0)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


def _push_frame(driver, emit: EventCb) -> None:
    """主动补一帧当前画面（投屏保活用）。"""
    data = _screenshot_jpeg_b64(driver)
    if data:
        emit("frame", {"data": data})


def _keepalive_loop(driver, emit: EventCb, stop: threading.Event,
                    interval: float = 0.4) -> None:
    """保活线程：周期截图推帧，保证执行期间投屏不断流。

    与 Web 端不同——这里每帧都真的 adb screencap（无 CDP 推流），interval 取 0.4s
    折中流畅度与 CPU/带宽。线程只读 driver，不调会跨线程冲突的 API（ADB 本身线程安全）。
    """
    while not stop.wait(timeout=interval):
        if stop.is_set():
            break
        _push_frame(driver, emit)


# ------------------------- 动作执行 -------------------------

def _do_tap(driver, target: str, emit: EventCb, hint: dict | None = None) -> str:
    from core_app import self_healing
    hit = self_healing.find_element(driver, target, locator_hint=hint)
    if not hit:
        raise AssertionError(f"未找到元素「{target}」（Store/XML/VLM 均失败，疑为功能缺陷）")
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


def _do_type(driver, target: str, text: str, emit: EventCb) -> str:
    from core_app import self_healing
    hit = self_healing.find_element(driver, target)
    if not hit:
        raise AssertionError(f"未找到输入框「{target}」（三级定位均失败）")
    cx, cy = hit["center"]
    driver.tap(cx, cy)  # 聚焦输入框
    driver.input_text(text)
    return f"已在「{target}」输入「{text}」"


def _do_swipe(driver, direction: str) -> str:
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


def _do_press_key(driver, keycode, name: str = "") -> str:
    # keycode 可能是数字或英文键名
    if isinstance(keycode, str) and not str(keycode).isdigit():
        kc = _KEYCODES.get(str(keycode).lower())
        if kc is None:
            raise AssertionError(f"未知按键：{keycode}")
        keycode = kc
    keycode = int(keycode)
    driver.keyevent(keycode)
    return f"已按键 {name or keycode}（keycode={keycode}）"


def _do_assert_visual(driver, claim: str, emit: EventCb) -> str:
    from core_app import vision
    r = vision.assert_visual(driver, claim)
    emit("heal", {"kind": "assert", "claim": claim,
                   "passed": r["passed"], "reason": r["reason"]})
    if not r["passed"]:
        raise AssertionError(f"视觉断言未通过：{claim} —— {r['reason']}")
    return f"视觉断言通过：{claim} —— {r['reason']}"


def _do_launch(driver, package: str, activity: str) -> str:
    return driver.launch(package, activity)


def _do_wait(seconds: float, driver, emit: EventCb) -> str:
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


def _dispatch(action: str, args: dict, driver: AppDriver, emit: EventCb,
              hint: dict | None = None) -> str | None:
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
        if args.get("keycode") is not None:
            return _do_press_key(driver, args["keycode"], args.get("name", ""))
    elif action == "assert_visual":
        claim = args.get("claim", "")
        if claim:
            return _do_assert_visual(driver, claim, emit)
    elif action == "launch_app":
        pkg, act = args.get("package", ""), args.get("activity", "")
        if pkg and act:
            return _do_launch(driver, pkg, act)
    elif action == "wait":
        return _do_wait(args.get("seconds", 1), driver, emit)
    return None


# ------------------------- 规则式回退 -------------------------

import re as _re


def _interpret_step_rule(step: str) -> dict | None:
    """规则式解析器（AI 不可用时的回退）。只解析、不执行。"""
    s = step.strip()
    m = _re.search(r"(?:等待|暂停|停留)\s*(\d+(?:\.\d+)?)\s*[秒s]?", s, _re.IGNORECASE)
    if m:
        return {"action": "wait", "args": {"seconds": float(m.group(1))}}
    if any(k in s for k in ("返回键", "按返回", "back", "BACK")):
        return {"action": "press_key", "args": {"keycode": 4, "name": "返回键"}}
    if any(k in s for k in ("HOME键", "按home", "回桌面", "home", "HOME")):
        return {"action": "press_key", "args": {"keycode": 3, "name": "HOME键"}}
    for d, kw in [("up", ("上滑", "向上滑")), ("down", ("下滑", "向下滑")),
                  ("left", ("左滑", "向左滑")), ("right", ("右滑", "向右滑"))]:
        if any(k in s for k in kw):
            return {"action": "swipe", "args": {"direction": d}}
    if any(k in s for k in ("点击", "按下", "打开", "选择")) and "输入" not in s:
        # 抽取目标（去动词前缀），交由三级定位处理
        target = _re.sub(r"^(点击|按下|打开|选择)", "", s).strip()
        if target:
            return {"action": "tap_element", "args": {"target": target}}
    if any(k in s for k in ("输入", "填写", "填入")):
        m = _re.search(r"(?:在|把)?\s*([^，,]+?)\s*(?:输入|填写|填入)\s*([^，,\s]+)", s)
        if m:
            return {"action": "type_text", "args": {"target": m.group(1), "text": m.group(2)}}
    return None


def _plan_step(step: str) -> dict | None:
    """解析步骤：规则式（零 LLM）→ AI Planner。"""
    plan = _interpret_step_rule(step)
    if plan is not None:
        return plan
    from core_app.planner import plan_step
    return plan_step(step)


def _interpret_step(step: str, driver: AppDriver, emit: EventCb,
                    hint: dict | None = None) -> str:
    """解析并执行一条中文步骤。"""
    plan = _plan_step(step)
    if plan:
        action, args = plan["action"], plan.get("args") or {}
        if action == "tap_element" and hint:
            args = {**args, "locator_hint": hint}
        if action not in ("tap_element",):
            emit("log", {"msg": f"[AI] 解析为 {action}{args or ''}"})
        result = _dispatch(action, args, driver, emit, hint=hint)
        if result is not None:
            return result
    emit("log", {"msg": f"[跳过] 未识别步骤：{step.strip()}"})
    return f"[跳过] {step.strip()}"


# ------------------------- 主入口 -------------------------

def run_app_case(case: dict, emit: EventCb = _noop, run_id: int | None = None,
                 cancel_event=None) -> dict:
    """执行一条 App 用例：纯 ADB 引擎 + 实时投屏 + 自然语言三级定位。

    Args:
        case: {id,name,steps,driver_type,device_serial,app_package,app_activity}
        emit: 事件回调（step / frame / log / heal / done）
        cancel_event: threading.Event，set 时中止
    Returns:
        {status, step_results, screenshots, error}
    """
    serial = case.get("device_serial") or ""
    if not serial:
        return {"status": "error", "step_results": [], "screenshots": [],
                "error": "用例未配置 device_serial（目标设备序号）"}

    driver = AppDriver(serial)
    try:
        status_info = driver.status()
    except Exception as e:  # noqa: BLE001
        emit("done", {"status": "error", "error": f"设备状态获取失败：{e}"})
        return {"status": "error", "step_results": [], "screenshots": [],
                "error": f"设备状态获取失败：{e}"}
    if not status_info["online"]:
        emit("done", {"status": "error",
                      "error": f"设备 {serial} 离线，请确认 adb 已连接"})
        return {"status": "error", "step_results": [], "screenshots": [],
                "error": f"设备 {serial} 离线"}

    emit("log", {"msg": f"设备 {serial} 在线（{status_info['width']}x{status_info['height']}）"})

    screenshots: list[str] = []
    step_results: list[dict] = []
    status = "passed"
    error = ""

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
                detail = _interpret_step(text, driver, emit, hint=hint)
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

    emit("done", {"status": status, "error": error})
    return {"status": status, "step_results": step_results,
            "screenshots": screenshots, "error": error}


def _save_shot(driver, name: str) -> str | None:
    """失败截图到 screenshots/platform/，返回相对路径。"""
    try:
        png = driver.screenshot()
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        out = ROOT / config.get("screenshot_dir", default="screenshots") / "platform"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{name}.png"
        im.save(str(path), "PNG")
        return f"platform/{name}.png"
    except Exception:  # noqa: BLE001
        return None
