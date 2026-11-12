"""执行模板编排器：把一批用例按模板配置串行跑起来。

架构复用 run_manager.py 的 RunSession + Queue + 后台线程模式，关键差异：
- 一次会话跑多条用例（按 template_snapshot['cases'] 顺序）；
- 每条独立调 worker.runner.run_case（内部自己开关浏览器）；
- 失败策略：continue（继续下一条）| abort（剩余全部标 skipped）；
- 单条超时：外层 Thread.join(timeout)，超时标 timeout（不强杀 Playwright，让其内部超时机制自然收）；
- 有 WebSocket 连着才推 frame（省内存）；
- 截图从磁盘读进 BLOB 落库，然后删磁盘文件避免泄漏。
"""

from __future__ import annotations

import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict

# 让 worker/ 可 import（runner.py）
_WORKER_DIR = Path(__file__).resolve().parent.parent / "worker"
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

ROOT = Path(__file__).resolve().parent.parent.parent

from db import (  # noqa: E402
    ExecutionCaseResult,
    ExecutionRun,
    ExecutionScreenshot,
    SessionLocal,
)
from runner_dispatch import run_any_case  # noqa: E402  WEB/APP 分流分发器


class ExecutionSession:
    """一次模板执行的会话：事件队列 + 结束信号 + 是否推投屏帧。"""

    def __init__(self, run_id: int, template_snapshot: dict, config: dict):
        self.run_id = run_id
        self.template_snapshot = template_snapshot
        self.on_failure = config.get("on_failure", "continue")
        self.case_timeout = int(config.get("case_timeout", 180))
        # 有界队列，满时按类型丢弃（frame/log 优先丢，step/done 保留）
        self.events: "queue.Queue[dict]" = queue.Queue(maxsize=500)
        self.finished = threading.Event()
        self.push_frames = False  # 有 WS 客户端连着时置 True
        self.result: dict | None = None

    def emit(self, kind: str, payload: dict) -> None:
        # frame 事件仅当有 WS 客户端时才推；队列满时丢弃低优先级事件
        if kind == "frame" and not self.push_frames:
            return
        item = {"kind": kind, "payload": payload}
        try:
            self.events.put_nowait(item)
        except queue.Full:
            if kind in ("step", "case_start", "case_end", "done", "error"):
                # 关键事件：强制入队（阻塞一下让消费者赶上）
                try:
                    self.events.put(item, timeout=1.0)
                except queue.Full:
                    pass
            # 其他（frame/log/heal）直接丢弃


# run_id -> ExecutionSession（结束后 pop，避免内存泄漏）
_SESSIONS: Dict[int, ExecutionSession] = {}
_SESSIONS_LOCK = threading.Lock()


def get_session(run_id: int) -> ExecutionSession | None:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(run_id)


def start_execution_run(
    run_id: int, template_snapshot: dict, config: dict
) -> ExecutionSession:
    """起后台线程执行模板；立即返回会话（不阻塞）。"""
    session = ExecutionSession(run_id, template_snapshot, config)
    with _SESSIONS_LOCK:
        _SESSIONS[run_id] = session

    def _worker():
        try:
            _run_template(session)
        except Exception as e:  # noqa: BLE001
            session.emit("done", {"status": "error", "error": str(e)})
            _update_run(run_id, status="error", error=str(e), ended_at=datetime.now())
        finally:
            session.finished.set()
            # 结束一定时间后清理会话（给前端断开重连留窗口）
            def _cleanup():
                with _SESSIONS_LOCK:
                    _SESSIONS.pop(run_id, None)

            threading.Timer(30.0, _cleanup).start()

    threading.Thread(target=_worker, daemon=True).start()
    return session


def _run_template(session: ExecutionSession) -> None:
    """核心：按顺序跑每条用例，落库、发事件、按策略处理失败。"""
    run_id = session.run_id
    cases = session.template_snapshot.get("cases", [])
    total = len(cases)
    summary = {"total": total, "passed": 0, "failed": 0, "skipped": 0, "timeout": 0, "error": 0}

    _update_run(run_id, status="running", started_at=datetime.now())
    session.emit("run_start", {"total": total})

    aborted = False
    for idx, case in enumerate(cases):
        # 先落一条 case_result（queued）以便前端能拿到 id
        cr_id = _create_case_result(run_id, idx, case)

        if aborted:
            # 已中止：剩余用例标 skipped 并落库
            _update_case_result(
                cr_id,
                status="skipped",
                error="前序用例失败且策略为 abort，跳过",
                ended_at=datetime.now(),
            )
            summary["skipped"] += 1
            session.emit(
                "case_end",
                {"case_result_id": cr_id, "case_index": idx, "status": "skipped"},
            )
            continue

        session.emit(
            "case_start",
            {
                "case_result_id": cr_id,
                "case_index": idx,
                "case_id": case.get("id"),
                "case_name": case.get("name", ""),
            },
        )
        _update_case_result(cr_id, status="running", started_at=datetime.now())

        # 收集该 case 的日志（wrapped emit 捕获 log 事件）
        case_logs: list = []

        def wrapped_emit(kind: str, payload: dict, _cr_id=cr_id, _idx=idx):
            # 给所有事件补上 case_result_id / case_index，前端能区分
            enriched = dict(payload)
            enriched["case_result_id"] = _cr_id
            enriched["case_index"] = _idx
            # 收集日志
            if kind == "log":
                case_logs.append(
                    {
                        "level": payload.get("level", "info"),
                        "text": payload.get("text") or payload.get("msg", ""),
                        "ts": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            # 步骤/心跳事件转发（frame 会被 push_frames 门控）
            session.emit(kind, enriched)

        # 用子线程跑 run_case 以便 join(timeout)
        result_box: dict = {}

        def _do_run():
            try:
                result_box["result"] = run_any_case(case, emit=wrapped_emit)
            except Exception as e:  # noqa: BLE001
                result_box["error"] = str(e)

        t = threading.Thread(target=_do_run, daemon=True)
        t.start()
        t.join(timeout=session.case_timeout)

        if t.is_alive():
            # 超时：不强杀线程（Playwright 内部状态会烂掉），标 timeout 让线程自己耗尽
            _update_case_result(
                cr_id,
                status="timeout",
                error=f"单条超时（{session.case_timeout}s）",
                logs=case_logs,
                ended_at=datetime.now(),
            )
            summary["timeout"] += 1
            session.emit(
                "case_end",
                {"case_result_id": cr_id, "case_index": idx, "status": "timeout"},
            )
            # 超时按失败处理策略
            if session.on_failure == "abort":
                aborted = True
            continue

        if "error" in result_box:
            _update_case_result(
                cr_id,
                status="error",
                error=result_box["error"],
                logs=case_logs,
                ended_at=datetime.now(),
            )
            summary["error"] += 1
            session.emit(
                "case_end",
                {
                    "case_result_id": cr_id,
                    "case_index": idx,
                    "status": "error",
                    "error": result_box["error"],
                },
            )
            if session.on_failure == "abort":
                aborted = True
            continue

        result = result_box.get("result") or {}
        case_status = result.get("status", "unknown")
        # 截图从磁盘搬到 BLOB
        _save_screenshots_to_db(cr_id, result.get("screenshots") or [])
        _update_case_result(
            cr_id,
            status=case_status,
            error=result.get("error", ""),
            step_results=result.get("step_results") or [],
            logs=case_logs,
            ended_at=datetime.now(),
        )
        if case_status == "passed":
            summary["passed"] += 1
        elif case_status == "failed":
            summary["failed"] += 1
        else:
            summary["error"] += 1

        session.emit(
            "case_end",
            {
                "case_result_id": cr_id,
                "case_index": idx,
                "status": case_status,
                "error": result.get("error", ""),
            },
        )

        if case_status != "passed" and session.on_failure == "abort":
            aborted = True

    # 汇总 run 状态
    if summary["failed"] + summary["error"] + summary["timeout"] > 0:
        run_status = "failed"
    else:
        run_status = "passed"
    _update_run(
        run_id,
        status=run_status,
        summary=summary,
        ended_at=datetime.now(),
    )
    session.result = {"status": run_status, "summary": summary}
    session.emit("done", {"status": run_status, "summary": summary})


# ---------------------- DB 辅助 ----------------------


def _create_case_result(run_id: int, idx: int, case: dict) -> int:
    """新建 case_result 行并返回 id。"""
    with SessionLocal() as s:
        cr = ExecutionCaseResult(
            run_id=run_id,
            case_index=idx,
            case_id=case.get("id"),
            case_name=case.get("name", ""),
            status="queued",
        )
        s.add(cr)
        s.commit()
        s.refresh(cr)
        return cr.id


def _update_case_result(cr_id: int, **fields) -> None:
    with SessionLocal() as s:
        cr = s.get(ExecutionCaseResult, cr_id)
        if not cr:
            return
        for k, v in fields.items():
            setattr(cr, k, v)
        s.commit()


def _update_run(run_id: int, **fields) -> None:
    with SessionLocal() as s:
        run = s.get(ExecutionRun, run_id)
        if not run:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        s.commit()


def _save_screenshots_to_db(case_result_id: int, rel_paths: list[str]) -> None:
    """把截图从 screenshots/ 目录读进 BLOB 落库，读完删磁盘文件。"""
    from core import config as _core_cfg

    shot_dir = ROOT / _core_cfg.get("screenshot_dir", default="screenshots")
    with SessionLocal() as s:
        for rel in rel_paths:
            fp = shot_dir / rel
            if not fp.exists():
                continue
            try:
                data = fp.read_bytes()
                shot = ExecutionScreenshot(
                    case_result_id=case_result_id,
                    name=fp.name,
                    data=data,
                    content_type="image/png",
                )
                s.add(shot)
                s.flush()
                # 落库成功再删磁盘文件
                try:
                    fp.unlink()
                except Exception:
                    pass
            except Exception:
                # 单张截图失败不影响后续
                pass
        s.commit()
