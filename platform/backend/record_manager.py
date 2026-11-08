"""录制会话管理器：桥接同步 Playwright 录制线程与异步 WebSocket。

与 run_manager 同构：录制会话在后台线程跑（RecordSession 自带线程），
事件经线程安全队列抛给 WebSocket；前端指令经 send_command 入队。
"""

from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path
from typing import Dict, Optional

_WORKER = Path(__file__).resolve().parent.parent / "worker"
if str(_WORKER) not in sys.path:
    sys.path.insert(0, str(_WORKER))

from recorder import RecordSession, translate_script  # noqa: E402


class _Session:
    """包装 RecordSession：加事件队列供 WS 消费。"""

    def __init__(self, sid: int):
        self.sid = sid
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.rec = RecordSession(sid, self._emit)

    def _emit(self, kind: str, payload: dict) -> None:
        self.events.put({"kind": kind, "payload": payload})

    def start(self) -> None:
        self.rec.start()

    def wait_ready(self, timeout: float) -> bool:
        return self.rec.ready.wait(timeout=timeout)

    def command(self, cmd: dict) -> None:
        self.rec.send_command(cmd)

    def stop_and_translate(self) -> list[dict]:
        """停止录制并翻译 codegen 脚本为用例草稿。"""
        self.rec.send_command({"type": "stop"})
        self.rec.stopped.wait(timeout=15)
        script = self.rec.get_script()
        self.rec.cleanup()
        if not script:
            return []
        return translate_script(script)


_SESSIONS: Dict[int, _Session] = {}
_counter = {"n": 0}
_lock = threading.Lock()


def create_session() -> _Session:
    with _lock:
        _counter["n"] += 1
        sid = _counter["n"]
    s = _Session(sid)
    _SESSIONS[sid] = s
    s.start()
    return s


def get_session(sid: int) -> Optional[_Session]:
    return _SESSIONS.get(sid)


def drop_session(sid: int) -> None:
    _SESSIONS.pop(sid, None)
