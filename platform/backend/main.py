"""平台后端入口：FastAPI（REST + WebSocket），本地先跑通。

- REST：用例 CRUD、触发运行、查运行结果；
- WebSocket：把 Worker 的实时事件（投屏帧/步骤日志）推给前端；
- 静态：托管前端构建产物（若存在）。
"""

from __future__ import annotations

import asyncio
import base64
import os
import queue
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import (
    ApiDefinition,
    ApiGlobalVar,
    ApiScenario,
    ApiScenarioRun,
    ExecutionCaseResult,
    ExecutionRun,
    ExecutionScreenshot,
    ExecutionTemplate,
    LLMConfig,
    Locator,
    SessionLocal,
    TestCase,
    TestEnv,
    TestRun,
    init_db,
)
from run_manager import get_session as get_run_session, start_run, cancel_run as cancel_run_session
from api_run_manager import get_api_session, start_api_run
from execution_manager import (
    get_session as get_execution_session,
    start_execution_run,
)
import record_manager

# db 导入时已把项目 root 挂进 sys.path，故此处可 import core.*
from core import crypto  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
SCREENSHOT_DIR = ROOT / "screenshots"

# App 自动化后端（/api/app/* REST + /ws/app/screen/* 投屏），纯 ADB 路线
from app.router import router as app_router  # noqa: E402
from app.screen_ws import router as app_screen_router  # noqa: E402

app = FastAPI(title="vision-ui-autotest 自动化测试平台")

# 本地开发：前端 dev server 与后端不同端口，放开 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# App 路由在 StaticFiles mount("/") 之前注册，避免被前端 catch-all 吞掉
app.include_router(app_router)
app.include_router(app_screen_router)


@app.on_event("startup")
def _startup():
    init_db()


# ------------------------- 数据模型（请求体） -------------------------

class CaseIn(BaseModel):
    name: str
    module: str = ""
    tags: list[str] = []
    steps: list = []
    allow_risky: bool = False
    # APP 自动化（纯 ADB 路线）；driver_type='app' 时由 App 工作台填写
    driver_type: str = "web"
    app_package: str = ""
    app_activity: str = ""
    device_serial: str = ""


# ------------------------- 用例 CRUD -------------------------

@app.get("/api/cases")
def list_cases(status: str | None = None):
    with SessionLocal() as s:
        q = s.query(TestCase)
        if status:
            q = q.filter(TestCase.status == status)
        return [c.to_dict() for c in q.order_by(TestCase.id).all()]


@app.post("/api/cases")
def create_case(body: CaseIn):
    with SessionLocal() as s:
        c = TestCase(**body.model_dump())
        s.add(c)
        s.commit()
        s.refresh(c)
        return c.to_dict()


@app.get("/api/cases/{case_id}")
def get_case(case_id: int):
    with SessionLocal() as s:
        c = s.get(TestCase, case_id)
        if not c:
            raise HTTPException(404, "用例不存在")
        return c.to_dict()


@app.put("/api/cases/{case_id}")
def update_case(case_id: int, body: CaseIn):
    with SessionLocal() as s:
        c = s.get(TestCase, case_id)
        if not c:
            raise HTTPException(404, "用例不存在")

        old_steps = c.steps or []
        new_steps = body.steps or []

        # 判断 steps 变化类型：
        # - 文本变化（步骤数量/顺序/内容改变）→ 清空脚本，下次执行重新编译
        # - 仅 parallel 接口场景变化 → 就地 patch 脚本，保留 UI 操作代码
        def _step_text(st):
            return st.get("text", "") if isinstance(st, dict) else str(st)
        def _step_parallels(st):
            return st.get("parallel") or [] if isinstance(st, dict) else []

        old_texts = [_step_text(s) for s in old_steps]
        new_texts = [_step_text(s) for s in new_steps]
        text_changed = old_texts != new_texts

        old_parallels = [_step_parallels(s) for s in old_steps]
        new_parallels = [_step_parallels(s) for s in new_steps]
        only_parallel_changed = (not text_changed) and (old_parallels != new_parallels)

        for k, v in body.model_dump().items():
            setattr(c, k, v)

        if text_changed:
            # 文本有变 → 清空脚本，执行时重新走 NL+编译
            c.script = None
            c.script_py = None
            c.script_updated_at = None
        elif only_parallel_changed and c.script_py:
            # 仅接口场景变化：
            # - compile_from_trace 生成的脚本含 emit("step"...) 行，可就地 patch；
            # - codegen 录制脚本没有 emit("step"...) 行，patch 无法定位步骤边界，
            #   且缺少 api_ctx / _run_api_scenario 运行环境，直接清空走 NL 重编译。
            is_codegen_script = 'emit("step"' not in c.script_py
            if is_codegen_script:
                c.script_py = None
                c.script_updated_at = None
            else:
                import sys
                _WORKER = Path(__file__).resolve().parent.parent / "worker"
                if str(_WORKER) not in sys.path:
                    sys.path.insert(0, str(_WORKER))
                try:
                    from script_compiler import patch_script_api_scenarios  # type: ignore
                    patched = patch_script_api_scenarios(c.script_py, old_steps, new_steps)
                    if patched:
                        c.script_py = patched
                        c.script_updated_at = datetime.now()
                    else:
                        # patch 失败（步骤块定位不到等）→ 清空让下次执行重编译
                        c.script_py = None
                        c.script_updated_at = None
                except Exception:
                    c.script_py = None
                    c.script_updated_at = None

        s.commit()
        s.refresh(c)
        return c.to_dict()


@app.delete("/api/cases/{case_id}")
def delete_case(case_id: int):
    with SessionLocal() as s:
        c = s.get(TestCase, case_id)
        if c:
            s.delete(c)
            s.commit()
        return {"ok": True}


@app.post("/api/cases/{case_id}/copy")
def copy_case(case_id: int):
    """复制用例：生成一条草稿副本（名称加「- 副本」，status=draft，待人工审核）。"""
    with SessionLocal() as s:
        c = s.get(TestCase, case_id)
        if not c:
            raise HTTPException(404, "用例不存在")
        new_c = TestCase(
            name=f"{c.name} - 副本",
            module=c.module,
            tags=list(c.tags or []),
            steps=list(c.steps or []),
            allow_risky=c.allow_risky,
            status="draft",
            source="human",
        )
        s.add(new_c)
        s.commit()
        s.refresh(new_c)
        return new_c.to_dict()


# ------------------------- AI 生成用例（P3.6） -------------------------

class GenerateIn(BaseModel):
    requirement: str
    count: int = 3


@app.post("/api/cases/generate")
def generate(body: GenerateIn):
    """按需求生成用例草稿并入库（status=draft，待人工审核）。"""
    import sys as _sys
    from pathlib import Path as _P
    _bk = _P(__file__).resolve().parent
    if str(_bk) not in _sys.path:
        _sys.path.insert(0, str(_bk))
    from generator import generate_cases
    from core.llm_client import LLMError
    try:
        drafts = generate_cases(body.requirement, body.count)
    except LLMError as e:
        raise HTTPException(502, f"AI 生成失败：{e}")

    created = []
    with SessionLocal() as s:
        for d in drafts:
            c = TestCase(
                name=d["name"], module=d.get("module", ""),
                tags=d.get("tags", []), steps=d["steps"],
                status="draft", source="ai_requirement",
            )
            s.add(c)
            s.flush()
            created.append(c.to_dict())
        s.commit()
    return {"count": len(created), "cases": created}


class ExploreIn(BaseModel):
    top: str
    sub: str
    count: int = 3


@app.post("/api/cases/explore")
def explore(body: ExploreIn):
    """页面探索式生成（方式B）：打开真实页面→VLM看懂→生成草稿入库。

    explorer 用同步 Playwright，放到线程里跑避免阻塞事件循环。
    """
    import sys as _sys
    from pathlib import Path as _P
    _wk = _P(__file__).resolve().parent.parent / "worker"
    if str(_wk) not in _sys.path:
        _sys.path.insert(0, str(_wk))

    import concurrent.futures
    from explorer import explore_and_generate
    from core.llm_client import LLMError

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            drafts = ex.submit(explore_and_generate, body.top, body.sub, body.count).result()
    except LLMError as e:
        raise HTTPException(502, f"页面探索生成失败：{e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"页面探索出错：{type(e).__name__}: {str(e)[:200]}")

    created = []
    with SessionLocal() as s:
        for d in drafts:
            c = TestCase(
                name=d["name"], module=d.get("module", ""),
                tags=d.get("tags", []), steps=d["steps"],
                status="draft", source="ai_explore",
            )
            s.add(c)
            s.flush()
            created.append(c.to_dict())
        s.commit()
    return {"count": len(created), "cases": created}



@app.post("/api/cases/{case_id}/compile")
def compile_case_script(case_id: int):
    """AI 编译/重新编译用例的 Playwright Python 脚本，同步返回脚本内容。

    在线程池里跑 LLM 调用（可能耗时 10-60s），编译成功则落库并返回脚本。
    """
    import concurrent.futures
    from datetime import datetime

    with SessionLocal() as s:
        c = s.get(TestCase, case_id)
        if not c:
            raise HTTPException(404, "用例不存在")
        case_dict = c.to_dict()

    def _do_compile():
        import sys
        from pathlib import Path
        ROOT = Path(__file__).resolve().parent.parent.parent
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        worker_dir = Path(__file__).resolve().parent.parent / "worker"
        if str(worker_dir) not in sys.path:
            sys.path.insert(0, str(worker_dir))
        from script_compiler import compile_to_playwright  # type: ignore
        return compile_to_playwright(case_dict)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_do_compile)
        try:
            script_py = fut.result(timeout=120)
        except concurrent.futures.TimeoutError:
            raise HTTPException(504, "AI 编译超时，请重试")

    if not script_py:
        raise HTTPException(502, "LLM 不可用或编译失败")

    with SessionLocal() as s:
        c = s.get(TestCase, case_id)
        if not c:
            raise HTTPException(404, "用例不存在")
        c.script_py = script_py
        c.script_updated_at = datetime.now()
        s.commit()
        return {"script_py": script_py, "script_updated_at": c.script_updated_at.isoformat()}


class ScriptPyIn(BaseModel):
    script_py: str


@app.put("/api/cases/{case_id}/script-py")
def update_case_script_py(case_id: int, body: ScriptPyIn):
    """手动保存/覆盖用例的 Playwright Python 脚本。"""
    from datetime import datetime
    with SessionLocal() as s:
        c = s.get(TestCase, case_id)
        if not c:
            raise HTTPException(404, "用例不存在")
        c.script_py = body.script_py
        c.script_updated_at = datetime.now()
        s.commit()
        return {"ok": True, "script_updated_at": c.script_updated_at.isoformat()}


@app.post("/api/cases/{case_id}/approve")
def approve_case(case_id: int):
    """草稿审核通过：status draft → active。"""
    with SessionLocal() as s:
        c = s.get(TestCase, case_id)
        if not c:
            raise HTTPException(404, "用例不存在")
        c.status = "active"
        s.commit()
        return c.to_dict()


# ------------------------- 执行 -------------------------

@app.post("/api/cases/{case_id}/run")
def run(case_id: int):
    """执行用例：统一走无头 + 实时投屏，无需选择模式。"""
    with SessionLocal() as s:
        c = s.get(TestCase, case_id)
        if not c:
            raise HTTPException(404, "用例不存在")
        case_dict = c.to_dict()
        run_row = TestRun(case_id=case_id, status="queued")
        s.add(run_row)
        s.commit()
        s.refresh(run_row)
        run_id = run_row.id

    start_run(run_id, case_dict)
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    with SessionLocal() as s:
        r = s.get(TestRun, run_id)
        if not r:
            raise HTTPException(404, "运行不存在")
        return r.to_dict()


@app.post("/api/runs/{run_id}/cancel")
def cancel_run_api(run_id: int):
    """取消正在执行的用例：发送取消信号，worker 在下一个检查点中止。"""
    found = cancel_run_session(run_id)
    if not found:
        raise HTTPException(404, "运行会话不存在或已结束")
    return {"ok": True, "msg": "取消信号已发送"}


@app.get("/api/screenshots/{name:path}")
def screenshot(name: str):
    # 规范化路径并校验仍在截图目录内，防止 ../ 路径穿越读取任意文件
    base = SCREENSHOT_DIR.resolve()
    path = (SCREENSHOT_DIR / name).resolve()
    if not path.is_relative_to(base):
        raise HTTPException(404, "截图不存在")
    if not path.exists():
        raise HTTPException(404, "截图不存在")
    return FileResponse(str(path))


# ------------------------- 大模型配置（P3.5） -------------------------

class LLMConfigIn(BaseModel):
    name: str
    base_url: str = ""
    api_key: str = ""
    chat_model: str = ""
    vision_model: str = ""
    timeout: int = 60
    verify_ssl: bool = True
    enabled: bool = True


@app.get("/api/llm-configs")
def list_llm_configs():
    with SessionLocal() as s:
        return [c.to_dict(mask=True) for c in s.query(LLMConfig).order_by(LLMConfig.id).all()]


@app.post("/api/llm-configs")
def create_llm_config(body: LLMConfigIn):
    with SessionLocal() as s:
        # 首条自动设为默认
        is_first = s.query(LLMConfig).count() == 0
        data = body.model_dump()
        # api_key 加密落库（明文不入库）
        data["api_key"] = crypto.encrypt(data.get("api_key") or "")
        c = LLMConfig(**data, is_default=is_first)
        s.add(c)
        s.commit()
        s.refresh(c)
        return c.to_dict()


@app.put("/api/llm-configs/{cfg_id}")
def update_llm_config(cfg_id: int, body: LLMConfigIn):
    with SessionLocal() as s:
        c = s.get(LLMConfig, cfg_id)
        if not c:
            raise HTTPException(404, "配置不存在")
        data = body.model_dump()
        # 若 api_key 传的是掩码（含 ****），保留原值不覆盖
        if "****" in (data.get("api_key") or ""):
            data.pop("api_key")
        else:
            # 传了新 key：加密后再落库
            data["api_key"] = crypto.encrypt(data.get("api_key") or "")
        for k, v in data.items():
            setattr(c, k, v)
        s.commit()
        s.refresh(c)
        return c.to_dict()


@app.delete("/api/llm-configs/{cfg_id}")
def delete_llm_config(cfg_id: int):
    with SessionLocal() as s:
        c = s.get(LLMConfig, cfg_id)
        if c:
            s.delete(c)
            s.commit()
        return {"ok": True}


@app.post("/api/llm-configs/{cfg_id}/set-default")
def set_default_llm_config(cfg_id: int):
    with SessionLocal() as s:
        c = s.get(LLMConfig, cfg_id)
        if not c:
            raise HTTPException(404, "配置不存在")
        s.query(LLMConfig).update({LLMConfig.is_default: False})
        c.is_default = True
        s.commit()
        return {"ok": True}


@app.post("/api/llm-configs/{cfg_id}/test")
def test_llm_config(cfg_id: int):
    """连通性测试：用该配置发一次最小对话，返回 ok/延迟/错误。"""
    with SessionLocal() as s:
        c = s.get(LLMConfig, cfg_id)
        if not c:
            raise HTTPException(404, "配置不存在")
        cfg = {
            "base_url": c.base_url, "api_key": c.plain_api_key,
            "chat_model": c.chat_model, "vision_model": c.vision_model,
            "timeout": c.timeout, "verify_ssl": c.verify_ssl,
        }
    import sys as _sys
    from pathlib import Path as _P
    _root = _P(__file__).resolve().parent.parent.parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    from core.llm_client import LLMClient
    return LLMClient(cfg).health_check()


# ------------------------- 自愈知识库 -------------------------

@app.get("/api/locators")
def list_locators():
    with SessionLocal() as s:
        return [x.to_dict() for x in s.query(Locator).order_by(Locator.id).all()]


@app.delete("/api/locators/{loc_id}")
def delete_locator(loc_id: int):
    """删除单条自愈记录（列表页手动清理用）。"""
    with SessionLocal() as s:
        obj = s.get(Locator, loc_id)
        if not obj:
            raise HTTPException(404, "locator not found")
        s.delete(obj)
        s.commit()
        return {"ok": True}


@app.post("/api/locators/classify")
def classify_locators(body: dict = None):
    """用 AI 对知识库中未分类的条目批量判断元素类型。

    传 {"ids": [1,2,3]} 则只处理指定 id；否则处理全部 element_type 为空的条目。
    返回 {"updated": n, "results": [{"id": 1, "anchor": "...", "element_type": "..."}, ...]}
    """
    import sys as _sys
    from pathlib import Path as _Path
    _wk = _Path(__file__).parent.parent / "worker"
    if str(_wk) not in _sys.path:
        _sys.path.insert(0, str(_wk))
    _rt = _Path(__file__).resolve().parent.parent.parent
    if str(_rt) not in _sys.path:
        _sys.path.insert(0, str(_rt))

    try:
        from core.llm_client import get_client, LLMError  # noqa: E402
    except Exception as e:
        raise HTTPException(500, f"LLM 客户端加载失败：{e}")

    ids = (body or {}).get("ids") or []

    with SessionLocal() as s:
        q = s.query(Locator)
        if ids:
            q = q.filter(Locator.id.in_(ids))
        else:
            q = q.filter((Locator.element_type == None) | (Locator.element_type == ""))  # noqa: E711
        rows = q.all()

    if not rows:
        return {"updated": 0, "results": []}

    # 构造批量分类 prompt
    items_text = "\n".join(
        f'{i+1}. anchor="{r.anchor}", selector="{r.selector}"'
        for i, r in enumerate(rows)
    )
    system_prompt = (
        "你是一个 UI 自动化测试专家，负责根据自然语言锚点和选择器判断页面元素类型。\n"
        "每个元素用一个简短的中文标签描述，常见类型包括但不限于：\n"
        "一级菜单、二级菜单、按钮、确认按钮、搜索按钮、输入框、下拉框、表格行操作、链接、图标、弹窗、标签页等。\n"
        "返回 JSON 数组，格式：[{\"index\": 1, \"element_type\": \"按钮\"}, ...]，严格按序号对应，不要多余文字。"
    )
    user_prompt = f"请对以下 {len(rows)} 个元素逐一判断类型：\n{items_text}"

    try:
        client = get_client()
        msg = client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        import json as _json
        content = msg.get("content") or ""
        # 兼容 {"data":[...]} 和直接 [...] 两种返回格式
        parsed = _json.loads(content)
        if isinstance(parsed, dict):
            classify_list = next(
                (v for v in parsed.values() if isinstance(v, list)), []
            )
        else:
            classify_list = parsed if isinstance(parsed, list) else []
    except (LLMError, Exception) as e:
        raise HTTPException(500, f"AI 分类失败：{e}")

    # 写回数据库
    results = []
    idx_map = {item.get("index"): item.get("element_type", "") for item in classify_list}
    with SessionLocal() as s:
        for i, row in enumerate(rows):
            et = idx_map.get(i + 1, "")
            if et:
                obj = s.get(Locator, row.id)
                if obj:
                    obj.element_type = et
            results.append({
                "id": row.id,
                "anchor": row.anchor,
                "element_type": et or row.element_type or "",
            })
        s.commit()

    return {"updated": sum(1 for r in results if r["element_type"]), "results": results}


# ------------------------- 测试数据管理（多套环境，全落库） -------------------------

class TestEnvIn(BaseModel):
    name: str
    base_url: str = ""
    login_path: str = "/login"
    username: str = ""
    password: str = ""


@app.get("/api/test-envs")
def list_test_envs():
    """测试环境列表（密码掩码）。"""
    with SessionLocal() as s:
        return [e.to_dict(mask=True) for e in s.query(TestEnv).order_by(TestEnv.id).all()]


@app.post("/api/test-envs")
def create_test_env(body: TestEnvIn):
    with SessionLocal() as s:
        is_first = s.query(TestEnv).count() == 0
        e = TestEnv(**body.model_dump(), is_default=is_first)
        s.add(e); s.commit(); s.refresh(e)
        return e.to_dict()


@app.put("/api/test-envs/{env_id}")
def update_test_env(env_id: int, body: TestEnvIn):
    with SessionLocal() as s:
        e = s.get(TestEnv, env_id)
        if not e:
            raise HTTPException(404, "环境不存在")
        data = body.model_dump()
        if "****" in (data.get("password") or ""):  # 掩码：不改密码
            data.pop("password")
        for k, v in data.items():
            setattr(e, k, v)
        s.commit(); s.refresh(e)
        return e.to_dict()


@app.delete("/api/test-envs/{env_id}")
def delete_test_env(env_id: int):
    with SessionLocal() as s:
        e = s.get(TestEnv, env_id)
        if e:
            was_default = e.is_default
            s.delete(e); s.commit()
            # 删的是默认环境时，自动把最早的一条设为默认
            if was_default:
                nxt = s.query(TestEnv).order_by(TestEnv.id).first()
                if nxt:
                    nxt.is_default = True; s.commit()
        return {"ok": True}


@app.post("/api/test-envs/{env_id}/set-default")
def set_default_test_env(env_id: int):
    with SessionLocal() as s:
        e = s.get(TestEnv, env_id)
        if not e:
            raise HTTPException(404, "环境不存在")
        s.query(TestEnv).update({TestEnv.is_default: False})
        e.is_default = True; s.commit()
        return {"ok": True}


# ------------------------- WebSocket：实时事件 -------------------------

@app.websocket("/ws/runs/{run_id}")
async def ws_run(ws: WebSocket, run_id: int):
    """把某次运行的实时事件（step/frame/log/done）推给前端。"""
    await ws.accept()
    session = get_run_session(run_id)
    # 运行可能还没起线程，稍等重试拿会话
    for _ in range(50):
        if session:
            break
        await asyncio.sleep(0.1)
        session = get_run_session(run_id)
    if not session:
        await ws.send_json({"kind": "error", "payload": {"msg": "运行会话不存在"}})
        await ws.close()
        return

    try:
        while True:
            try:
                first = await asyncio.get_event_loop().run_in_executor(
                    None, session.events.get, True, 0.5
                )
            except Exception:
                # 队列空：只有 finished 已 set 且队列彻底为空才退出，
                # 否则继续等——保证执行结束前 frame/step/done 全部推完
                if session.finished.is_set() and session.events.empty():
                    # 再做一次非阻塞排空，防止 finished.set() 和最后几帧之间的竞态
                    leftover = []
                    while True:
                        try:
                            leftover.append(session.events.get_nowait())
                        except queue.Empty:
                            break
                    # 把剩余事件发出去
                    done_sent = False
                    for event in leftover:
                        if event["kind"] == "frame":
                            try:
                                await ws.send_bytes(base64.b64decode(event["payload"]["data"]))
                            except Exception:
                                pass
                            continue
                        await ws.send_json(event)
                        if event["kind"] == "done":
                            done_sent = True
                    # 若 runner 没有 emit done（异常路径），由 WS 兜底发一次
                    if not done_sent:
                        await ws.send_json({"kind": "done",
                                            "payload": session.result or {"status": "unknown"}})
                    break
                continue

            batch = [first]
            while True:
                try:
                    batch.append(session.events.get_nowait())
                except queue.Empty:
                    break

            # 按到达顺序逐条发送：frame 走二进制帧（send_bytes，省 base64 膨胀与前端解码），
            # 其余事件走 send_json；done 恒在末尾（runner 收尾最后才 emit done），发完即退出。
            done = False
            for event in batch:
                if event["kind"] == "frame":
                    try:
                        await ws.send_bytes(base64.b64decode(event["payload"]["data"]))
                    except Exception:
                        pass
                    continue
                await ws.send_json(event)
                if event["kind"] == "done":
                    done = True
            if done:
                break
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ------------------------- 录制式生成（方式 C，§3.6） -------------------------
# 仅本地开放：start.py 会给子进程注入 RECORDING_ENABLED=1；云端镜像/k8s 不设置该
# 变量则接口 403 + 前端隐藏入口。原因：云端无物理显示器，录制需要真实有头浏览器。

def _recording_enabled() -> bool:
    return os.environ.get("RECORDING_ENABLED", "").strip() == "1"


def _require_recording() -> None:
    if not _recording_enabled():
        raise HTTPException(403, "录制功能仅在本地部署可用（需 RECORDING_ENABLED=1 且真实显示器）")


@app.get("/api/record/enabled")
def record_enabled():
    """供前端判断是否显示录制入口。"""
    return {"enabled": _recording_enabled()}


@app.post("/api/record/start")
def record_start():
    """开始录制：本地启动真实有头 Chromium。返回 session_id。"""
    _require_recording()
    s = record_manager.create_session()
    return {"session_id": s.sid}


@app.post("/api/record/{sid}/stop")
def record_stop(sid: int):
    """结束录制 → AI 翻译 codegen 脚本为中文步骤 → 入库草稿（脚本直接存 script_py）。"""
    _require_recording()
    s = record_manager.get_session(sid)
    if not s:
        raise HTTPException(404, "录制会话不存在")
    from core.llm_client import LLMError

    # 先拿到 codegen 生成的原始脚本，改造成平台兼容格式后存库
    raw_script = s.rec.get_script() if hasattr(s.rec, "get_script") else None

    try:
        drafts = s.stop_and_translate()
    except LLMError as e:
        record_manager.drop_session(sid)
        raise HTTPException(502, f"翻译失败：{e}")

    # 改造脚本（纯代码变换 + LLM 仅标注步骤边界，不重写任何操作行）
    adapted_script: Optional[str] = None
    if raw_script:
        try:
            import sys as _sys
            _WORKER = Path(__file__).resolve().parent.parent / "worker"
            if str(_WORKER) not in _sys.path:
                _sys.path.insert(0, str(_WORKER))
            from recorder import adapt_codegen_script  # type: ignore
            # 用第一条草稿的步骤作为步骤语义参考（供 LLM 分组）
            first_steps = drafts[0]["steps"] if drafts else []
            adapted_script = adapt_codegen_script(raw_script, first_steps)
        except Exception:
            adapted_script = None

    created = []
    with SessionLocal() as db:
        for i, d in enumerate(drafts):
            c = TestCase(name=d["name"], module=d.get("module", ""),
                         tags=d.get("tags", []), steps=d["steps"],
                         status="draft", source="ai_record")
            # 优先存改造后的兼容脚本；改造失败时存原始脚本（runner 有 headless 替换兜底）
            script_to_store = adapted_script or raw_script
            if script_to_store:
                c.script_py = script_to_store
                c.script_updated_at = datetime.now()
            db.add(c)
            db.flush()
            created.append(c.to_dict())
        db.commit()
    record_manager.drop_session(sid)

    return {"count": len(created), "cases": created}


@app.websocket("/ws/record/{sid}")
async def ws_record(ws: WebSocket, sid: int):
    """录制事件下行：ready / record_action / record_log。上行仅支持 stop 指令。"""
    await ws.accept()
    if not _recording_enabled():
        await ws.send_json({"kind": "error", "payload": {"msg": "录制功能仅在本地部署可用"}})
        await ws.close(); return
    s = record_manager.get_session(sid)
    if not s:
        await ws.send_json({"kind": "error", "payload": {"msg": "会话不存在"}})
        await ws.close(); return

    # 等录制会话就绪（登录+投屏）
    ready = await asyncio.get_event_loop().run_in_executor(None, s.wait_ready, 40.0)
    if not ready:
        await ws.send_json({"kind": "error", "payload": {"msg": "录制会话启动超时"}})
        await ws.close(); return
    await ws.send_json({"kind": "ready", "payload": {}})

    stop = asyncio.Event()

    async def pump_events():
        """把录制会话事件（投屏帧/捕获动作）推给前端。"""
        while not stop.is_set():
            try:
                ev = await asyncio.get_event_loop().run_in_executor(
                    None, s.events.get, True, 0.5)
                await ws.send_json(ev)
            except Exception:
                continue

    async def recv_commands():
        """接收前端上行指令（点击/输入坐标）注入录制会话。"""
        try:
            while not stop.is_set():
                cmd = await ws.receive_json()
                s.command(cmd)
        except WebSocketDisconnect:
            stop.set()

    task = asyncio.create_task(pump_events())
    try:
        await recv_commands()
    finally:
        stop.set()
        task.cancel()
        try:
            await ws.close()
        except Exception:
            pass


# ------------------------- 接口场景（通用接口自动化） -------------------------

class ApiDefIn(BaseModel):
    name: str
    module: str = ""
    method: str = "GET"
    path: str = ""
    headers: dict = {}
    params: dict = {}
    body: str = ""
    body_type: str = "json"
    extractors: list = []
    assertions: list = []
    retry_interval: float = 1.0
    retry_max: int = 1


class ApiVarIn(BaseModel):
    env_id: int
    name: str
    value: str = ""
    module: str = ""
    description: str = ""


class ApiScenarioIn(BaseModel):
    name: str
    module: str = ""
    description: str = ""
    setup_steps: list = []
    steps: list = []
    status: str = "active"


# ---- 接口库 CRUD ----

@app.get("/api/api-defs")
def list_api_defs():
    with SessionLocal() as s:
        return [a.to_dict() for a in s.query(ApiDefinition).order_by(ApiDefinition.id).all()]


@app.post("/api/api-defs")
def create_api_def(body: ApiDefIn):
    with SessionLocal() as s:
        a = ApiDefinition(**body.model_dump())
        s.add(a); s.commit(); s.refresh(a)
        return a.to_dict()


@app.get("/api/api-defs/{def_id}")
def get_api_def(def_id: int):
    with SessionLocal() as s:
        a = s.get(ApiDefinition, def_id)
        if not a:
            raise HTTPException(404, "接口不存在")
        return a.to_dict()


@app.put("/api/api-defs/{def_id}")
def update_api_def(def_id: int, body: ApiDefIn):
    with SessionLocal() as s:
        a = s.get(ApiDefinition, def_id)
        if not a:
            raise HTTPException(404, "接口不存在")
        for k, v in body.model_dump().items():
            setattr(a, k, v)
        s.commit(); s.refresh(a)
        return a.to_dict()


@app.delete("/api/api-defs/{def_id}")
def delete_api_def(def_id: int):
    with SessionLocal() as s:
        a = s.get(ApiDefinition, def_id)
        if a:
            s.delete(a); s.commit()
        return {"ok": True}


@app.post("/api/api-defs/{def_id}/test")
def test_api_def(def_id: int):
    """单接口试跑：用默认环境全局变量作 ctx，发一次请求并返回结果（含断言/提取）。"""
    import sys as _sys
    from pathlib import Path as _P
    _wk = _P(__file__).resolve().parent.parent / "worker"
    if str(_wk) not in _sys.path:
        _sys.path.insert(0, str(_wk))
    from api_runner import execute_api, _load_env_vars

    with SessionLocal() as s:
        a = s.get(ApiDefinition, def_id)
        if not a:
            raise HTTPException(404, "接口不存在")
        api = a.to_dict()
    ctx = _load_env_vars()
    result = execute_api(api, ctx)
    return {"result": result, "variables": ctx}


# ---- 接口导入（Postman / curl） ----

class ImportPreviewIn(BaseModel):
    text: str
    kind: str = "auto"  # auto | postman | curl


class ImportCommitIn(BaseModel):
    items: list[ApiDefIn]


@app.post("/api/api-defs/import/preview")
def preview_import(body: ImportPreviewIn):
    """解析 Postman Collection 或 curl 文本，返回接口草稿列表（不入库）。"""
    import sys as _sys
    from pathlib import Path as _P
    _bk = _P(__file__).resolve().parent
    if str(_bk) not in _sys.path:
        _sys.path.insert(0, str(_bk))
    from importer import parse_import
    try:
        items = parse_import(body.text, body.kind)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"解析失败：{e}")
    return {"items": items, "count": len(items)}


@app.post("/api/api-defs/import/commit")
def commit_import(body: ImportCommitIn):
    """把勾选后的接口草稿批量写入接口库。"""
    created = []
    with SessionLocal() as s:
        for item in body.items:
            a = ApiDefinition(**item.model_dump())
            s.add(a)
            created.append(a)
        s.commit()
        return {"count": len(created), "items": [a.to_dict() for a in created]}


# ---- 全局变量（环境级） ----

@app.get("/api/api-vars")
def list_api_vars(env_id: int | None = None):
    with SessionLocal() as s:
        q = s.query(ApiGlobalVar)
        if env_id is not None:
            q = q.filter(ApiGlobalVar.env_id == env_id)
        return [v.to_dict() for v in q.order_by(ApiGlobalVar.id).all()]


@app.post("/api/api-vars")
def create_api_var(body: ApiVarIn):
    with SessionLocal() as s:
        dup = s.query(ApiGlobalVar).filter(
            ApiGlobalVar.env_id == body.env_id, ApiGlobalVar.name == body.name
        ).first()
        if dup:
            raise HTTPException(409, f"当前环境已存在同名变量「{body.name}」")
        v = ApiGlobalVar(**body.model_dump())
        s.add(v); s.commit(); s.refresh(v)
        return v.to_dict()


@app.put("/api/api-vars/{var_id}")
def update_api_var(var_id: int, body: ApiVarIn):
    with SessionLocal() as s:
        v = s.get(ApiGlobalVar, var_id)
        if not v:
            raise HTTPException(404, "变量不存在")
        dup = s.query(ApiGlobalVar).filter(
            ApiGlobalVar.env_id == body.env_id,
            ApiGlobalVar.name == body.name,
            ApiGlobalVar.id != var_id,
        ).first()
        if dup:
            raise HTTPException(409, f"当前环境已存在同名变量「{body.name}」")
        for k, val in body.model_dump().items():
            setattr(v, k, val)
        s.commit(); s.refresh(v)
        return v.to_dict()


@app.delete("/api/api-vars/{var_id}")
def delete_api_var(var_id: int):
    with SessionLocal() as s:
        v = s.get(ApiGlobalVar, var_id)
        if v:
            s.delete(v); s.commit()
        return {"ok": True}


# ---- 接口场景 CRUD ----

@app.get("/api/api-scenarios")
def list_api_scenarios():
    with SessionLocal() as s:
        return [x.to_dict() for x in s.query(ApiScenario).order_by(ApiScenario.id).all()]


@app.post("/api/api-scenarios")
def create_api_scenario(body: ApiScenarioIn):
    with SessionLocal() as s:
        x = ApiScenario(**body.model_dump())
        s.add(x); s.commit(); s.refresh(x)
        return x.to_dict()


@app.get("/api/api-scenarios/{sid}")
def get_api_scenario(sid: int):
    with SessionLocal() as s:
        x = s.get(ApiScenario, sid)
        if not x:
            raise HTTPException(404, "场景不存在")
        return x.to_dict()


@app.put("/api/api-scenarios/{sid}")
def update_api_scenario(sid: int, body: ApiScenarioIn):
    with SessionLocal() as s:
        x = s.get(ApiScenario, sid)
        if not x:
            raise HTTPException(404, "场景不存在")
        for k, v in body.model_dump().items():
            setattr(x, k, v)
        s.commit(); s.refresh(x)
        return x.to_dict()


@app.delete("/api/api-scenarios/{sid}")
def delete_api_scenario(sid: int):
    with SessionLocal() as s:
        x = s.get(ApiScenario, sid)
        if x:
            s.delete(x); s.commit()
        return {"ok": True}


# ---- 场景执行 + 结果 ----

@app.post("/api/api-scenarios/{sid}/run")
def run_api_scenario(sid: int):
    with SessionLocal() as s:
        x = s.get(ApiScenario, sid)
        if not x:
            raise HTTPException(404, "场景不存在")
        scenario = x.to_dict()
        run_row = ApiScenarioRun(scenario_id=sid, status="queued")
        s.add(run_row); s.commit(); s.refresh(run_row)
        run_id = run_row.id
    start_api_run(run_id, scenario)
    return {"run_id": run_id}


@app.post("/api/api-scenarios/{sid}/debug")
def debug_api_scenario(sid: int):
    """场景调试：同步执行整个场景，返回每步完整请求/响应/提取/断言（不落 run 表）。"""
    import sys as _sys
    from pathlib import Path as _P
    _wk = _P(__file__).resolve().parent.parent / "worker"
    if str(_wk) not in _sys.path:
        _sys.path.insert(0, str(_wk))
    from api_runner import run_scenario

    with SessionLocal() as s:
        x = s.get(ApiScenario, sid)
        if not x:
            raise HTTPException(404, "场景不存在")
        scenario = x.to_dict()
    result = run_scenario(scenario)  # 同步执行，emit 默认 noop
    return result


@app.get("/api/api-runs/{run_id}")
def get_api_run(run_id: int):
    with SessionLocal() as s:
        r = s.get(ApiScenarioRun, run_id)
        if not r:
            raise HTTPException(404, "运行不存在")
        return r.to_dict()


@app.websocket("/ws/api-runs/{run_id}")
async def ws_api_run(ws: WebSocket, run_id: int):
    """把某次场景运行的实时事件（step/api/assert/log/done）推给前端。"""
    await ws.accept()
    session = get_api_session(run_id)
    for _ in range(50):
        if session:
            break
        await asyncio.sleep(0.1)
        session = get_api_session(run_id)
    if not session:
        await ws.send_json({"kind": "error", "payload": {"msg": "运行会话不存在"}})
        await ws.close()
        return

    try:
        while True:
            try:
                event = await asyncio.get_event_loop().run_in_executor(
                    None, session.events.get, True, 0.5
                )
                await ws.send_json(event)
                if event["kind"] == "done":
                    break
            except Exception:
                if session.finished.is_set() and session.events.empty():
                    await ws.send_json({"kind": "done",
                                        "payload": session.result or {"status": "unknown"}})
                    break
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ------------------------- 执行模板（用例执行） -------------------------


class ExecutionTemplateIn(BaseModel):
    name: str
    case_ids: list[int] = []
    on_failure: str = "continue"  # continue|abort
    case_timeout: int = 180
    description: str = ""


@app.get("/api/execution-templates")
def list_execution_templates():
    with SessionLocal() as s:
        rows = s.query(ExecutionTemplate).order_by(ExecutionTemplate.id.desc()).all()
        return [t.to_dict() for t in rows]


@app.post("/api/execution-templates")
def create_execution_template(body: ExecutionTemplateIn):
    if not body.name.strip():
        raise HTTPException(400, "模板名不能为空")
    if not body.case_ids:
        raise HTTPException(400, "至少选择一条用例")
    if body.on_failure not in ("continue", "abort"):
        raise HTTPException(400, "on_failure 必须是 continue|abort")
    with SessionLocal() as s:
        t = ExecutionTemplate(**body.model_dump())
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.to_dict()


@app.get("/api/execution-templates/{tid}")
def get_execution_template(tid: int):
    with SessionLocal() as s:
        t = s.get(ExecutionTemplate, tid)
        if not t:
            raise HTTPException(404, "模板不存在")
        return t.to_dict()


@app.put("/api/execution-templates/{tid}")
def update_execution_template(tid: int, body: ExecutionTemplateIn):
    if not body.case_ids:
        raise HTTPException(400, "至少选择一条用例")
    if body.on_failure not in ("continue", "abort"):
        raise HTTPException(400, "on_failure 必须是 continue|abort")
    with SessionLocal() as s:
        t = s.get(ExecutionTemplate, tid)
        if not t:
            raise HTTPException(404, "模板不存在")
        for k, v in body.model_dump().items():
            setattr(t, k, v)
        s.commit()
        s.refresh(t)
        return t.to_dict()


@app.delete("/api/execution-templates/{tid}")
def delete_execution_template(tid: int):
    """物理删模板，级联删除对应 run/case_result/screenshot。"""
    with SessionLocal() as s:
        t = s.get(ExecutionTemplate, tid)
        if not t:
            return {"ok": True}
        # 级联删：先找出所有 run.id，再删 case_result + screenshot
        run_ids = [r.id for r in s.query(ExecutionRun).filter(
            ExecutionRun.template_id == tid).all()]
        if run_ids:
            _cascade_delete_runs(s, run_ids)
        s.delete(t)
        s.commit()
        return {"ok": True}


@app.post("/api/execution-templates/{tid}/run")
def run_execution_template(tid: int):
    """触发执行：组装 template_snapshot（当前模板 + 所有用例全量），起后台线程。"""
    with SessionLocal() as s:
        t = s.get(ExecutionTemplate, tid)
        if not t:
            raise HTTPException(404, "模板不存在")
        # 拉取用例全量作为快照
        cases = []
        for cid in (t.case_ids or []):
            c = s.get(TestCase, cid)
            if c:
                cases.append(c.to_dict())
            else:
                # 用例已删：塞一个 stub，执行时会失败但不影响其他
                cases.append({"id": cid, "name": f"[已删除 #{cid}]", "steps": []})
        snapshot = {"template": t.to_dict(), "cases": cases}
        config = {"on_failure": t.on_failure, "case_timeout": t.case_timeout}
        # 建 run 行，拿 id
        run_row = ExecutionRun(
            template_id=tid,
            template_name=t.name,
            template_snapshot=snapshot,
            status="queued",
        )
        s.add(run_row)
        s.commit()
        s.refresh(run_row)
        run_id = run_row.id

    start_execution_run(run_id, snapshot, config)
    return {"run_id": run_id}


@app.post("/api/execution-templates/{tid}/run-sync")
def run_execution_template_sync(tid: int, timeout: int = 1800):
    """同步触发：等所有用例跑完再返回，便于 CI/流水线一次调用拿到全部结果。

    Query 参数：
        timeout: 总超时秒数（默认 1800=30min）。到达即返回 success=false 的超时响应。

    返回体：
        {
            "success": bool,                # 所有用例 passed 才 true
            "run_id": int,
            "template_id": int,
            "template_name": str,
            "status": str,                  # passed|failed|error|timeout
            "started_at": ISO,
            "ended_at": ISO,
            "summary": {total, passed, failed, error, timeout, skipped},
            "failed_cases": [
                {"case_id": int, "case_name": str, "status": str, "error": str}
            ]
        }
    """
    # 1) 组 snapshot、建 run 行（与 run_execution_template 一致）
    with SessionLocal() as s:
        t = s.get(ExecutionTemplate, tid)
        if not t:
            raise HTTPException(404, "测试计划不存在")
        cases = []
        for cid in (t.case_ids or []):
            c = s.get(TestCase, cid)
            if c:
                cases.append(c.to_dict())
            else:
                cases.append({"id": cid, "name": f"[已删除 #{cid}]", "steps": []})
        snapshot = {"template": t.to_dict(), "cases": cases}
        config = {"on_failure": t.on_failure, "case_timeout": t.case_timeout}
        run_row = ExecutionRun(
            template_id=tid,
            template_name=t.name,
            template_snapshot=snapshot,
            status="queued",
        )
        s.add(run_row)
        s.commit()
        s.refresh(run_row)
        run_id = run_row.id
        template_name = t.name

    # 2) 起后台执行、阻塞等 finished
    session = start_execution_run(run_id, snapshot, config)
    finished = session.finished.wait(timeout=max(1, int(timeout)))

    # 3) 组装返回体
    with SessionLocal() as s:
        run = s.get(ExecutionRun, run_id)
        case_results = (
            s.query(ExecutionCaseResult)
            .filter(ExecutionCaseResult.run_id == run_id)
            .order_by(ExecutionCaseResult.case_index.asc())
            .all()
        )
        # 未 passed 的都算失败用例（含 failed / error / timeout / skipped）
        failed_cases = [
            {
                "case_id": cr.case_id,
                "case_name": cr.case_name or "",
                "status": cr.status,
                "error": cr.error or "",
            }
            for cr in case_results
            if cr.status != "passed"
        ]
        run_dict = run.to_dict() if run else {}

    if not finished:
        # 超时：run 状态可能还是 running；接口层直接判为 timeout
        return {
            "success": False,
            "run_id": run_id,
            "template_id": tid,
            "template_name": template_name,
            "status": "timeout",
            "started_at": run_dict.get("started_at"),
            "ended_at": None,
            "summary": run_dict.get("summary") or {},
            "failed_cases": failed_cases,
            "error": f"总超时（{timeout}s），执行未在期限内结束",
        }

    summary = run_dict.get("summary") or {}
    status = run_dict.get("status", "error")
    total = summary.get("total") or len(case_results)
    passed = summary.get("passed") or 0
    success = status == "passed" and total > 0 and passed == total

    return {
        "success": success,
        "run_id": run_id,
        "template_id": tid,
        "template_name": template_name,
        "status": status,
        "started_at": run_dict.get("started_at"),
        "ended_at": run_dict.get("ended_at"),
        "summary": summary,
        "failed_cases": failed_cases,
    }


# ------------------------- 执行记录 -------------------------


def _cascade_delete_runs(s, run_ids: list[int]) -> None:
    """从数据库删除给定 run_ids 对应的 run/case_result/screenshot。调用方持有 session。"""
    if not run_ids:
        return
    # 找出所有 case_result_id
    cr_ids = [
        cr.id
        for cr in s.query(ExecutionCaseResult).filter(
            ExecutionCaseResult.run_id.in_(run_ids)
        ).all()
    ]
    if cr_ids:
        s.query(ExecutionScreenshot).filter(
            ExecutionScreenshot.case_result_id.in_(cr_ids)
        ).delete(synchronize_session=False)
    s.query(ExecutionCaseResult).filter(
        ExecutionCaseResult.run_id.in_(run_ids)
    ).delete(synchronize_session=False)
    s.query(ExecutionRun).filter(
        ExecutionRun.id.in_(run_ids)
    ).delete(synchronize_session=False)


# 内存节流：最近一次跑清理的时间戳
_LAST_CLEANUP: dict[str, datetime] = {}


def _cleanup_expired_runs() -> None:
    """列表接口触发的过期清理：超 7 天的软删记录物理删。1h 最多跑一次。"""
    now = datetime.now()
    last = _LAST_CLEANUP.get("at")
    if last and (now - last).total_seconds() < 3600:
        return
    _LAST_CLEANUP["at"] = now
    try:
        cutoff = now - timedelta(days=7)
        with SessionLocal() as s:
            expired_ids = [
                r.id
                for r in s.query(ExecutionRun)
                .filter(ExecutionRun.is_deleted == True)  # noqa: E712
                .filter(ExecutionRun.deleted_at != None)  # noqa: E711
                .filter(ExecutionRun.deleted_at < cutoff)
                .limit(100)
                .all()
            ]
            if expired_ids:
                _cascade_delete_runs(s, expired_ids)
                s.commit()
                print(f"[cleanup] 物理清理过期执行记录：{len(expired_ids)} 条")
    except Exception as e:  # noqa: BLE001
        print(f"[cleanup] 过期清理失败（已忽略）：{e}")


@app.get("/api/execution-runs")
def list_execution_runs():
    """列表：显示未软删的记录（按 id desc）；顺带触发一次过期清理。"""
    _cleanup_expired_runs()
    with SessionLocal() as s:
        rows = (
            s.query(ExecutionRun)
            .filter(ExecutionRun.is_deleted == False)  # noqa: E712
            .order_by(ExecutionRun.id.desc())
            .all()
        )
        return [r.to_dict() for r in rows]


@app.get("/api/execution-runs/{run_id}")
def get_execution_run(run_id: int):
    """详情：run 元信息 + 所有 case_result（含 step_results/logs）。"""
    with SessionLocal() as s:
        r = s.get(ExecutionRun, run_id)
        if not r:
            raise HTTPException(404, "执行记录不存在")
        results = (
            s.query(ExecutionCaseResult)
            .filter(ExecutionCaseResult.run_id == run_id)
            .order_by(ExecutionCaseResult.case_index)
            .all()
        )
        # 每条 case_result 加上 screenshots 元信息（不含 data）
        result_dicts = []
        for cr in results:
            d = cr.to_dict()
            shots = (
                s.query(ExecutionScreenshot)
                .filter(ExecutionScreenshot.case_result_id == cr.id)
                .order_by(ExecutionScreenshot.id)
                .all()
            )
            d["screenshots"] = [sh.to_dict() for sh in shots]
            result_dicts.append(d)
        run_dict = r.to_dict(snapshot=True)
        run_dict["case_results"] = result_dicts
        return run_dict


@app.delete("/api/execution-runs/{run_id}")
def soft_delete_execution_run(run_id: int):
    """逻辑删除：置 is_deleted=True + deleted_at=now，超 7 天由列表接口触发物理删。"""
    with SessionLocal() as s:
        r = s.get(ExecutionRun, run_id)
        if not r:
            return {"ok": True}
        r.is_deleted = True
        r.deleted_at = datetime.now()
        s.commit()
        return {"ok": True}


@app.get("/api/execution-case-results/{cr_id}")
def get_execution_case_result(cr_id: int):
    with SessionLocal() as s:
        cr = s.get(ExecutionCaseResult, cr_id)
        if not cr:
            raise HTTPException(404, "结果不存在")
        d = cr.to_dict()
        shots = (
            s.query(ExecutionScreenshot)
            .filter(ExecutionScreenshot.case_result_id == cr_id)
            .order_by(ExecutionScreenshot.id)
            .all()
        )
        d["screenshots"] = [sh.to_dict() for sh in shots]
        return d


@app.get("/api/execution-screenshots/{shot_id}")
def get_execution_screenshot(shot_id: int):
    """返回截图 BLOB 二进制。带 Cache-Control 让浏览器缓存。"""
    with SessionLocal() as s:
        sh = s.get(ExecutionScreenshot, shot_id)
        if not sh:
            raise HTTPException(404, "截图不存在")
        return Response(
            content=sh.data,
            media_type=sh.content_type or "image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )


@app.websocket("/ws/execution-runs/{run_id}")
async def ws_execution_run(ws: WebSocket, run_id: int):
    """把某次模板执行的实时事件推给前端。"""
    await ws.accept()
    session = get_execution_session(run_id)
    # 会话可能还没起线程，稍等重试
    for _ in range(50):
        if session:
            break
        await asyncio.sleep(0.1)
        session = get_execution_session(run_id)
    if not session:
        # 会话不在了（已结束且清理）：读 DB 一次性回放最终态
        with SessionLocal() as s:
            r = s.get(ExecutionRun, run_id)
        if not r:
            await ws.send_json({"kind": "error", "payload": {"msg": "执行记录不存在"}})
        else:
            await ws.send_json({"kind": "done", "payload": {
                "status": r.status, "summary": r.summary or {},
            }})
        await ws.close()
        return

    # 有 WS 连上时才推投屏帧
    session.push_frames = True
    try:
        while True:
            try:
                event = await asyncio.get_event_loop().run_in_executor(
                    None, session.events.get, True, 0.5
                )
                await ws.send_json(event)
                if event["kind"] == "done":
                    break
            except Exception:
                if session.finished.is_set() and session.events.empty():
                    await ws.send_json({"kind": "done", "payload":
                                        session.result or {"status": "unknown"}})
                    break
    except WebSocketDisconnect:
        pass
    finally:
        session.push_frames = False
        try:
            await ws.close()
        except Exception:
            pass


# ------------------------- 静态前端（构建产物存在才挂载） -------------------------

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
