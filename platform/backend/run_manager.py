"""运行管理器：桥接「同步 Playwright Worker（后台线程）」与「异步 WebSocket」。

关键约束：Playwright 同步 API 不能在 asyncio 事件循环线程里跑，
故每次运行起一个后台线程执行 Worker，事件经线程安全 Queue 抛出，
WebSocket 侧异步消费并推给前端。
"""

from __future__ import annotations

import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict

# 让 worker 目录可被 import（worker/runner.py）
_WORKER_DIR = Path(__file__).resolve().parent.parent / "worker"
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from db import SessionLocal, TestRun
from runner_dispatch import run_any_case  # WEB/APP 分流分发器（惰性加载引擎）


class RunSession:
    """一次运行的会话状态：事件队列 + 结果。"""

    def __init__(self, run_id: int, case: dict):
        self.run_id = run_id
        self.case = case
        # 有界队列纯作内存兜底：消费端逐帧完整播放（一帧不丢），正常帧数远小于 maxsize；
        # 仅当消费端严重跟不上、队列堆满时才丢新 frame 保命，避免极端积压吃光内存。
        self.events: "queue.Queue[dict]" = queue.Queue(maxsize=500)
        self.finished = threading.Event()
        self.cancelled = threading.Event()
        self.result: dict | None = None

    def cancel(self) -> None:
        """请求取消：设置信号，worker 在下一个检查点感知并中止。"""
        self.cancelled.set()

    def emit(self, kind: str, payload: dict) -> None:
        item = {"kind": kind, "payload": payload}
        try:
            self.events.put_nowait(item)
        except queue.Full:
            if kind in ("step", "done", "error"):
                # 关键事件：阻塞一小会儿让消费者赶上，尽量不丢
                try:
                    self.events.put(item, timeout=1.0)
                except queue.Full:
                    pass
            # 其他（frame/log/heal）：仅在队列满这一极端情况下才丢弃，作内存兜底


# run_id -> RunSession
_SESSIONS: Dict[int, RunSession] = {}


def start_run(run_id: int, case: dict) -> RunSession:
    """起后台线程执行用例，立即返回会话（不阻塞）。"""
    session = RunSession(run_id, case)
    _SESSIONS[run_id] = session

    def _worker():
        _update_run(run_id, status="running", started_at=datetime.now())
        try:
            result = run_any_case(case, emit=session.emit, run_id=run_id,
                                  cancel_event=session.cancelled)
            session.result = result
            final_status = result["status"]
            _update_run(
                run_id,
                status=final_status,
                step_results=result["step_results"],
                screenshots=result["screenshots"],
                error=result["error"],
                ended_at=datetime.now(),
            )
        except Exception as e:  # noqa: BLE001
            session.result = {"status": "error", "error": str(e)}
            session.emit("done", {"status": "error", "error": str(e)})
            _update_run(run_id, status="error", error=str(e), ended_at=datetime.now())
        finally:
            session.finished.set()

    threading.Thread(target=_worker, daemon=True).start()
    return session


def get_session(run_id: int) -> RunSession | None:
    return _SESSIONS.get(run_id)


def cancel_run(run_id: int) -> bool:
    """取消正在运行的任务，返回是否找到了会话。"""
    s = _SESSIONS.get(run_id)
    if s:
        s.cancel()
        return True
    return False


def _update_run(run_id: int, **fields) -> None:
    with SessionLocal() as s:
        run = s.get(TestRun, run_id)
        if not run:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        s.commit()
