"""接口场景执行引擎：纯 HTTP（requests），不起浏览器。

一个「场景」= 前置步骤 + 主步骤，每步引用接口库里的一个接口（api_id）。
执行时维护一个变量上下文 ctx：初始值来自默认测试环境的全局变量模板，
运行中各接口的提取器把响应值写回 ctx，供后续步骤 ${var} 引用。

设计对齐仓库既有 runner.py 的 _do_api_request/_subst_vars，但独立实现、不依赖 Playwright。
"""

from __future__ import annotations

import json as _json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, quote

import requests

# 让 core 可被 import（读 base_url / 默认环境）
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 让 backend 可被 import（读 db 载入接口/全局变量）
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core import config  # noqa: E402

EventCb = Callable[[str, dict], None]


def _noop(kind: str, payload: dict) -> None:  # 默认事件回调
    pass


_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")

# 内置时间变量前缀，避免与用户自定义变量冲突
_BUILTIN_DATE_PREFIX = "_dt_"


def _builtin_date_vars() -> dict:
    """生成当天相关的内置日期变量，在每次请求前注入到 ctx。

    可用变量：
      ${TODAY}             2026-07-29
      ${TODAY_START}       2026-07-29 00:00:00
      ${TODAY_END}         2026-07-29 23:59:59
      ${YESTERDAY}         2026-07-28
      ${YESTERDAY_START}   2026-07-28 00:00:00
      ${YESTERDAY_END}     2026-07-28 23:59:59
    """
    from datetime import date, timedelta
    today = date.today()
    yesterday = today - timedelta(days=1)
    return {
        "TODAY":            today.strftime("%Y-%m-%d"),
        "TODAY_START":      today.strftime("%Y-%m-%d") + " 00:00:00",
        "TODAY_END":        today.strftime("%Y-%m-%d") + " 23:59:59",
        "YESTERDAY":        yesterday.strftime("%Y-%m-%d"),
        "YESTERDAY_START":  yesterday.strftime("%Y-%m-%d") + " 00:00:00",
        "YESTERDAY_END":    yesterday.strftime("%Y-%m-%d") + " 23:59:59",
    }


def _subst_vars(text: str, ctx: dict) -> str:
    """把 ${var} 替换为 ctx 中的变量值（对齐 runner._subst_vars）。"""
    if not text:
        return text

    def _r(m: re.Match) -> str:
        return str(ctx.get(m.group(1), m.group(0)))

    return _VAR_PATTERN.sub(_r, text)


def _subst_deep(obj: Any, ctx: dict) -> Any:
    """递归对 dict/list/str 做 ${var} 替换。"""
    if isinstance(obj, str):
        return _subst_vars(obj, ctx)
    if isinstance(obj, dict):
        return {k: _subst_deep(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_subst_deep(v, ctx) for v in obj]
    return obj


def _json_extract(data: Any, path: str) -> Any:
    """轻量 JSONPath 子集：支持 $、.key、[index]。找不到返回 None。

    例：$.data.token / $.list[0].id / $.code
    """
    if not path:
        return None
    p = path.strip()
    if p.startswith("$"):
        p = p[1:]
    # 拆分 token：.key 与 [index]
    tokens = re.findall(r"\.([A-Za-z0-9_\-]+)|\[(\d+)\]", p)
    node = data
    for key, idx in tokens:
        try:
            if key:
                if not isinstance(node, dict):
                    return None
                node = node.get(key)
            else:
                if not isinstance(node, list):
                    return None
                i = int(idx)
                node = node[i] if 0 <= i < len(node) else None
        except Exception:  # noqa: BLE001
            return None
        if node is None:
            return None
    return node


def _to_number(v: Any) -> float | None:
    """尽力把值转成 float，失败返回 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _check_assertion(a: dict, resp: "requests.Response", body_json: Any,
                     duration_ms: float) -> dict:
    """执行单条断言，返回 {type, ok, detail}。

    支持 4 类：
    - status_code: 期望状态码（expect），op 默认 ==（支持 ==/!=）
    - jsonpath: 取 jsonpath 值后比较（op ∈ eq/ne/contains/not_empty/gt/lt），expect 为期望值
    - contains: 响应文本包含 expect 子串
    - duration: 响应耗时 ms 小于 expect（op 默认 <，支持 </<=）
    """
    t = a.get("type")
    try:
        if t == "status_code":
            op = a.get("op", "==")
            expect = int(a.get("expect", 200))
            actual = resp.status_code
            ok = (actual != expect) if op == "!=" else (actual == expect)
            return {"type": t, "ok": ok, "detail": f"状态码 {actual} {op} {expect}"}

        if t == "jsonpath":
            jp = a.get("jsonpath", "")
            op = a.get("op", "eq")
            expect = a.get("expect", "")
            actual = _json_extract(body_json, jp)
            if op == "not_empty":
                ok = actual not in (None, "", [], {})
                return {"type": t, "ok": ok, "detail": f"{jp} = {actual!r} 非空"}
            if op == "contains":
                ok = str(expect) in str(actual)
                return {"type": t, "ok": ok, "detail": f"{jp}={actual!r} 包含 {expect!r}"}
            if op in ("gt", "lt"):
                an, en = _to_number(actual), _to_number(expect)
                if an is None or en is None:
                    return {"type": t, "ok": False,
                            "detail": f"{jp}={actual!r} 无法与 {expect!r} 做数值比较"}
                ok = an > en if op == "gt" else an < en
                return {"type": t, "ok": ok, "detail": f"{jp}={an} {op} {en}"}
            # eq / ne：字符串化比较（兼容数字/布尔）；也兼容历史误存的 "==" / "!=" 写法
            is_ne = op in ("ne", "!=")
            ok = (str(actual) != str(expect)) if is_ne else (str(actual) == str(expect))
            return {"type": t, "ok": ok, "detail": f"{jp}={actual!r} {op} {expect!r}"}

        if t == "contains":
            expect = str(a.get("expect", ""))
            ok = expect in resp.text
            return {"type": t, "ok": ok, "detail": f"响应包含 {expect!r}"}

        if t == "duration":
            op = a.get("op", "<")
            expect = _to_number(a.get("expect", 0)) or 0
            ok = duration_ms <= expect if op == "<=" else duration_ms < expect
            return {"type": t, "ok": ok, "detail": f"耗时 {duration_ms:.0f}ms {op} {expect:.0f}ms"}

    except Exception as e:  # noqa: BLE001
        return {"type": t, "ok": False, "detail": f"断言执行异常：{e}"}

    return {"type": t, "ok": False, "detail": f"未知断言类型：{t}"}


def _build_url(path: str) -> str:
    """path 为完整 URL 直用，否则拼接默认环境 base_url。"""
    if path.startswith("http"):
        return path
    base = (config.get("base_url") or "").rstrip("/")
    return base + "/" + path.lstrip("/")


def execute_api(api: dict, ctx: dict, emit: EventCb = _noop,
                frame_cb: "Callable[[], None] | None" = None,
                cancel_event=None) -> dict:
    """执行单个接口定义：发请求 → 提取变量 → 跑断言，断言失败按配置轮询重试。

    Args:
        api:          接口定义
        ctx:          变量上下文（就地更新提取到的变量）
        emit:         事件回调
        frame_cb:     可选，每次重试等待期间周期性调用以推送投屏帧，保持实时视图不卡顿
        cancel_event: 可选 threading.Event，set 时立即中止重试循环
    Returns:
        {name, method, url, status_code, duration_ms, ok, extracted, asserts, error,
         attempts, retry_max}
    """
    name = api.get("name", "")
    method = (api.get("method") or "GET").upper()
    # 注入内置日期变量（用户自定义同名变量优先，不覆盖）
    for k, v in _builtin_date_vars().items():
        ctx.setdefault(k, v)
    path = _subst_vars(api.get("path", ""), ctx)
    url = _build_url(path)
    headers = _subst_deep(api.get("headers") or {}, ctx)
    # params 保留原始模板，每次请求前在循环内替换（支持前置步骤提取的变量）
    raw_params = api.get("params") or {}

    result: dict = {"name": name, "method": method, "url": url,
                    "status_code": None, "duration_ms": None, "ok": False,
                    "extracted": {}, "missed_extractors": [],
                    "extractors": [ex for ex in (api.get("extractors") or []) if ex.get("name")],
                    "asserts": [], "error": "",
                    "request": {}, "response": {}}

    # 组装请求体（只组装一次，重试时复用同一请求）
    body_type = api.get("body_type", "json")
    raw_body = api.get("body") or ""
    kwargs: dict = {"headers": headers, "timeout": 30}
    body_sent: Any = None  # 实际发送的请求体（供调试展示）
    if raw_body:
        raw_body = _subst_vars(raw_body, ctx)
        if body_type == "json":
            try:
                kwargs["json"] = _json.loads(raw_body)
                body_sent = kwargs["json"]
            except Exception:  # noqa: BLE001 —— 非合法 JSON 时按原始文本发
                kwargs["data"] = raw_body
                headers.setdefault("Content-Type", "application/json")
                body_sent = raw_body
        elif body_type == "form":
            try:
                kwargs["data"] = _json.loads(raw_body)  # {k:v} 表单
            except Exception:  # noqa: BLE001
                kwargs["data"] = raw_body
            body_sent = kwargs["data"]
        else:  # raw
            kwargs["data"] = raw_body
            body_sent = raw_body

    # 断言失败重试配置：仅「有显式断言且断言未全部通过」时才重试；
    # 请求异常、无断言的状态码兜底失败均不重试（对齐用户选择：仅断言未通过才重试）。
    asserts = api.get("assertions") or []
    try:
        retry_max = max(1, int(api.get("retry_max") or 1))
    except (TypeError, ValueError):
        retry_max = 1
    try:
        _ri = api.get("retry_interval")
        retry_interval = max(0.0, float(_ri if _ri is not None else 1.0))
    except (TypeError, ValueError):
        retry_interval = 1.0

    attempt = 0
    while True:
        attempt += 1
        # 每次尝试重置本次结果字段，最终只保留最后一次的响应/断言/提取
        result["asserts"] = []
        result["extracted"] = {}
        result["missed_extractors"] = []
        result["error"] = ""

        # 每次请求前重新替换 params（确保前置步骤提取的变量能被用上）
        # 用 quote（而非 quote_plus）编码，空格变 %20 而非 +，对齐服务端期望
        params = _subst_deep(raw_params, ctx)
        if params:
            qs = urlencode({k: str(v) for k, v in params.items()}, quote_via=quote)
            req_url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"
        else:
            req_url = url
        # 记录实际发送的请求报文（变量替换后）
        result["request"] = {"method": method, "url": req_url, "headers": headers,
                             "params": params, "body_type": body_type, "body": body_sent}

        emit("api", {"name": name, "method": method, "url": req_url, "phase": "request"})

        # 发请求 + 计时
        t0 = time.monotonic()
        try:
            resp = requests.request(method, req_url, **kwargs)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"请求失败：{type(e).__name__}: {str(e)[:200]}"
            break  # 请求本身异常，不重试
        duration_ms = (time.monotonic() - t0) * 1000
        result["status_code"] = resp.status_code
        result["duration_ms"] = round(duration_ms, 1)
        # 记录完整响应报文（响应头 + 完整响应体，截断保护）
        result["request"]["url"] = resp.request.url  # requests 拼好 query 后的最终 URL
        body_text = resp.text or ""
        result["response"] = {"status_code": resp.status_code,
                              "headers": dict(resp.headers),
                              "body": body_text[:20000],
                              "truncated": len(body_text) > 20000}

        # 解析响应 JSON（失败为 None，contains/耗时断言仍可用）
        try:
            body_json = resp.json()
        except Exception:  # noqa: BLE001
            body_json = None

        emit("api", {"name": name, "method": method, "url": req_url, "phase": "response",
                     "status": resp.status_code, "duration_ms": result["duration_ms"],
                     "preview": resp.text[:300]})

        # 提取变量
        if (api.get("extractors") or []) and body_json is None:
            emit("log", {"msg": f"[提取] 响应体非合法 JSON，无法提取变量（Content-Type: {resp.headers.get('content-type', '未知')}）"})
        for ex in (api.get("extractors") or []):
            var_name = ex.get("name")
            jp = ex.get("jsonpath", "")
            if not var_name:
                continue
            val = _json_extract(body_json, jp)
            if val is not None:
                # 标量转字符串便于后续 ${var} 拼接；dict/list 保留原样
                ctx[var_name] = val if isinstance(val, (dict, list)) else str(val)
                result["extracted"][var_name] = ctx[var_name]
                emit("log", {"msg": f"[提取] {var_name} = {result['extracted'][var_name]!r}"})
            else:
                result["missed_extractors"].append({"name": var_name, "jsonpath": jp,
                    "reason": "响应体非 JSON" if body_json is None else "JSONPath 未命中"})
                emit("log", {"msg": f"[提取失败] {var_name}：JSONPath「{jp}」在响应中未命中（body_json={'None（解析失败）' if body_json is None else '有效'}）"})

        # 跑断言（无断言则以「状态码 < 400」为默认通过判据）
        if asserts:
            # 即使有显式断言，HTTP 层面异常也不应让断言通过
            if resp.status_code >= 400:
                result["asserts"].append({
                    "type": "status_code", "ok": False,
                    "detail": f"HTTP 响应异常（{resp.status_code}），跳过业务断言"
                })
                result["ok"] = False
                result["error"] = f"HTTP {resp.status_code}（响应异常，断言不执行）"
            else:
                all_ok = True
                for a in asserts:
                    r = _check_assertion(a, resp, body_json, duration_ms)
                    result["asserts"].append(r)
                    all_ok = all_ok and r["ok"]
                    emit("assert", {"name": name, "passed": r["ok"], "detail": r["detail"]})
                result["ok"] = all_ok
        else:
            result["ok"] = resp.status_code < 400
            if not result["ok"]:
                result["error"] = f"HTTP {resp.status_code}（无断言，默认要求 <400）"

        # 判定是否重试：仅「有断言 且 断言未通过 且 未达次数上限」才重发
        if result["ok"] or not asserts or attempt >= retry_max:
            break
        emit("log", {"msg": f"[重试] 第 {attempt}/{retry_max} 次断言未通过，{retry_interval:g}s 后重发"})
        # 退避等待：api_runner 为纯 HTTP，无 UI 状态可 wait_for，重试间隔只能 sleep
        # （属重试退避，非 UI 同步，是仓库「禁 sleep 同步」规则的正当例外）
        # 分段 sleep：每 0.3s 检查取消信号或调推帧回调，响应及时
        elapsed = 0.0
        interval = 0.3
        while elapsed < retry_interval:
            time.sleep(min(interval, retry_interval - elapsed))
            elapsed += interval
            if cancel_event and cancel_event.is_set():
                result["error"] = "手动停止"
                result["ok"] = False
                emit("log", {"msg": "[接口场景] 执行已被手动停止"})
                # 直接跳出重试循环
                break
            if frame_cb is not None:
                try:
                    frame_cb()
                except Exception:  # noqa: BLE001
                    pass
        if cancel_event and cancel_event.is_set():
            break

    result["attempts"] = attempt
    result["retry_max"] = retry_max
    final_url = result["request"].get("url") or url
    if result["status_code"] is None:
        step_detail = result["error"] or f"{method} {final_url}"
    else:
        step_detail = f"{method} {final_url} → {result['status_code']}"
        if attempt > 1:
            step_detail += f"（共尝试 {attempt}/{retry_max} 次）"
    emit("step", {"name": name, "state": "passed" if result["ok"] else "failed",
                  "detail": step_detail})
    return result


def _load_env_vars() -> dict:
    """载入默认测试环境的全局变量模板作为 ctx 初始值。"""
    ctx: dict = {}
    try:
        from db import ApiGlobalVar, SessionLocal, TestEnv  # type: ignore
        with SessionLocal() as s:
            env = s.query(TestEnv).filter(TestEnv.is_default == True).first()  # noqa: E712
            if not env:
                env = s.query(TestEnv).order_by(TestEnv.id).first()
            if env:
                for v in s.query(ApiGlobalVar).filter(ApiGlobalVar.env_id == env.id).all():
                    ctx[v.name] = v.value
    except Exception:  # noqa: BLE001
        pass
    return ctx


def _load_api(api_id: int) -> dict | None:
    """按 id 从接口库载入接口定义。"""
    try:
        from db import ApiDefinition, SessionLocal  # type: ignore
        with SessionLocal() as s:
            a = s.get(ApiDefinition, api_id)
            return a.to_dict() if a else None
    except Exception:  # noqa: BLE001
        return None


def run_scenario(scenario: dict, emit: EventCb = _noop, ctx: dict | None = None,
                 frame_cb: "Callable[[], None] | None" = None,
                 cancel_event=None) -> dict:
    """执行一个接口场景：前置步骤 → 主步骤，v1 串行。

    Args:
        scenario:     {id, name, setup_steps, steps}
        emit:         事件回调（api / assert / step / log / done）
        ctx:          可选的外部变量上下文。传入时**就地更新**（双向共享）；
                      不传时按老逻辑从默认环境载入全局变量。
        frame_cb:     可选，透传给 execute_api，重试等待期间周期推帧
        cancel_event: 可选 threading.Event，set 时中止场景执行
    Returns:
        {status, step_results, variables, error}
    """
    if ctx is None:
        ctx = _load_env_vars()
        if ctx:
            emit("log", {"msg": f"[全局变量] 载入 {len(ctx)} 个：{', '.join(ctx.keys())}"})
    else:
        # 外部（UI 用例）传入 ctx：合并环境变量作为初值兜底，同名以 ctx 现有值优先
        env_vars = _load_env_vars()
        merged = 0
        for k, v in env_vars.items():
            if k not in ctx:
                ctx[k] = v
                merged += 1
        if merged:
            emit("log", {"msg": f"[全局变量] 合并 {merged} 个环境变量到上下文"})

    step_results: list[dict] = []
    status = "passed"
    error = ""

    all_steps = [("前置", st) for st in (scenario.get("setup_steps") or [])] + \
                [("步骤", st) for st in (scenario.get("steps") or [])]

    for idx, (phase, st) in enumerate(all_steps):
        # 每步开始前检查取消信号
        if cancel_event and cancel_event.is_set():
            status = "cancelled"
            error = "手动停止"
            emit("log", {"msg": "[接口场景] 执行已被手动停止"})
            break

        api_id = st.get("api_id")
        api = _load_api(api_id) if api_id else None
        label = st.get("name") or (api.get("name") if api else f"api#{api_id}")
        emit("step", {"index": idx, "name": label, "phase": phase, "state": "running"})

        if not api:
            status = "failed"
            error = f"{phase}#{idx}「{label}」引用的接口(id={api_id})不存在"
            step_results.append({"name": label, "phase": phase, "ok": False, "error": error})
            emit("step", {"index": idx, "name": label, "state": "failed", "detail": error})
            break

        res = execute_api(api, ctx, emit, frame_cb=frame_cb, cancel_event=cancel_event)
        res["phase"] = phase
        res["index"] = idx
        step_results.append(res)
        if not res["ok"]:
            status = "cancelled" if (cancel_event and cancel_event.is_set()) else "failed"
            error = res.get("error") or f"{phase}#{idx}「{label}」断言未通过"
            break

    emit("done", {"status": status, "error": error})
    return {"status": status, "step_results": step_results,
            "variables": ctx, "error": error}
