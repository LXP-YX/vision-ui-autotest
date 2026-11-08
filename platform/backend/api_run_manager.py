"""接口场景运行管理器：桥接「同步执行引擎（后台线程）」与「异步 WebSocket」。

镜像 run_manager.py 的模式：每次运行起后台线程执行场景，事件经线程安全 Queue
抛出，WebSocket 侧异步消费推给前端。运行结果落库到 api_scenario_run。
"""

from __future__ import annotations

import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict

# 让 worker 目录可被 import（api_runner.py）
_WORKER_DIR = Path(__file__).resolve().parent.parent / "worker"
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from api_runner import run_scenario
from db import ApiScenarioRun, SessionLocal


class ApiRunSession:
    """一次场景运行的会话状态：事件队列 + 结果。"""

    def __init__(self, run_id: int, scenario: dict):
        self.run_id = run_id
        self.scenario = scenario
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.finished = threading.Event()
        self.result: dict | None = None

    def emit(self, kind: str, payload: dict) -> None:
        self.events.put({"kind": kind, "payload": payload})


# run_id -> ApiRunSession
_SESSIONS: Dict[int, ApiRunSession] = {}


def start_api_run(run_id: int, scenario: dict) -> ApiRunSession:
    """起后台线程执行场景，立即返回会话（不阻塞）。"""
    session = ApiRunSession(run_id, scenario)
    _SESSIONS[run_id] = session

    def _worker():
        _update_run(run_id, status="running", started_at=datetime.now())
        try:
            result = run_scenario(scenario, emit=session.emit)
            session.result = result
            _update_run(
                run_id,
                status=result["status"],
                step_results=result["step_results"],
                variables=result["variables"],
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


def get_api_session(run_id: int) -> ApiRunSession | None:
    return _SESSIONS.get(run_id)


def _update_run(run_id: int, **fields) -> None:
    with SessionLocal() as s:
        run = s.get(ApiScenarioRun, run_id)
        if not run:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        s.commit()
