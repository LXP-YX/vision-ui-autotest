"""App 实时投屏 WebSocket：ADB 截图采集循环 → 推订阅 WS。

对标 Web 端 CDP screencast，差异：截图来源 `adb exec-out screencap`，
在线程池里周期采集 + PIL 缩成 JPEG，以二进制帧推给所有订阅该设备的 WS。

采集策略：
- 仅当至少一个 WS 订阅时才跑采集循环（省 CPU）；
- 最后一帧缓存，新连入的 WS 立刻收到当前画面；
- 设备断开时推一条 error JSON，等 2s 重试（可能重连）。
"""

from __future__ import annotations

import asyncio
import base64
import io
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_app import adb_bridge  # noqa: E402

router = APIRouter(tags=["app-screen"])

FPS = 3  # 投屏目标帧率（adb screencap 单帧 ~0.8s，设 3 为上限更贴合实际）
INTERVAL = 1.0 / FPS


def _capture_jpeg(serial: str) -> Optional[bytes]:
    """在线程池执行的同步采集：adb 截图 → PIL JPEG bytes。失败返回 None。

    screencap 单帧 ~0.8s，PIL 处理 ~0.05s；瓶颈在 adb 传输，无法纯软件提速。
    实际帧率≈1/(采集+INTERVAL)，故 FPS 设 3 作上限而非强承诺。
    """
    try:
        png = adb_bridge.screenshot(serial)
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        if im.mode != "RGB":
            im = im.convert("RGB")  # screencap PNG 常为 RGBA，JPEG 不支持 alpha
        w, h = im.size
        if w > 900:
            im = im.resize((900, int(h * 900 / w)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=60)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


class _ScreencastManager:
    """多设备投屏采集 + 订阅管理（单事件循环，模块级单例）。"""

    def __init__(self) -> None:
        # serial -> {subscribers:set[WebSocket], task, last_frame:bytes}
        self._devices: dict[str, dict] = {}

    async def subscribe(self, serial: str, ws: WebSocket) -> None:
        await ws.accept()
        if not adb_bridge.is_online(serial):
            await ws.send_json({"kind": "error", "payload": {"msg": f"设备 {serial} 不在线"}})
            await ws.close()
            return

        entry = self._devices.get(serial)
        if entry is None:
            entry = {"subscribers": set(), "task": None, "last_frame": b""}
            self._devices[serial] = entry
        entry["subscribers"].add(ws)
        # 立刻推一帧缓存画面，避免黑屏等待
        if entry["last_frame"]:
            try:
                await ws.send_bytes(entry["last_frame"])
            except Exception:  # noqa: BLE001
                pass
        # 首个订阅者：启动采集循环
        if entry["task"] is None or entry["task"].done():
            entry["task"] = asyncio.create_task(self._capture_loop(serial))

    def unsubscribe(self, serial: str, ws: WebSocket) -> None:
        entry = self._devices.get(serial)
        if not entry:
            return
        entry["subscribers"].discard(ws)
        if not entry["subscribers"]:
            # 无订阅者：取消采集循环
            task = entry.get("task")
            if task and not task.done():
                task.cancel()
            self._devices.pop(serial, None)

    async def _capture_loop(self, serial: str) -> None:
        """周期采集 + 广播，仅在有订阅者时运行。

        采集在独立线程池任务跑，本协程起下一帧采集后立刻 await 上一帧结果，
        形成采集流水线（采集耗时与广播重叠），实测帧率 ≈ 1/单帧采集耗时。
        adb screencap 单帧 ~0.8s，故帧率上限 ~1.1 FPS——这是 ADB 投屏的固有限制，
        非 sleep 节流；INTERVAL 仅在采集快于目标时才起限制作用。
        """
        loop = asyncio.get_event_loop()
        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"adb-screen-{serial}")
        pending = loop.run_in_executor(pool, _capture_jpeg, serial)
        try:
            while True:
                entry = self._devices.get(serial)
                if not entry or not entry["subscribers"]:
                    break
                # 起下一帧采集（立刻排队，不阻塞），再等当前帧结果
                nxt = loop.run_in_executor(pool, _capture_jpeg, serial)
                data = await pending
                if data:
                    entry["last_frame"] = data
                    for ws in list(entry["subscribers"]):
                        try:
                            await ws.send_bytes(data)
                        except Exception:  # noqa: BLE001
                            entry["subscribers"].discard(ws)
                else:
                    for ws in list(entry["subscribers"]):
                        try:
                            await ws.send_json({"kind": "log",
                                "payload": {"msg": f"截图失败，设备 {serial} 可能已断开，2s 后重试"}})
                        except Exception:  # noqa: BLE001
                            entry["subscribers"].discard(ws)
                    if not entry["subscribers"]:
                        break
                    await asyncio.sleep(2)
                pending = nxt
                await asyncio.sleep(INTERVAL)
            # 收尾：推最后一帧
            try:
                data = await pending
                entry = self._devices.get(serial)
                if data and entry and entry["subscribers"]:
                    entry["last_frame"] = data
                    for ws in list(entry["subscribers"]):
                        try: await ws.send_bytes(data)
                        except Exception: entry["subscribers"].discard(ws)  # noqa: BLE001
            except Exception:  # noqa: BLE001
                pass
        finally:
            pool.shutdown(wait=False)


_manager = _ScreencastManager()


@router.websocket("/ws/app/screen/{serial}")
async def ws_app_screen(ws: WebSocket, serial: str):
    """实时投屏：后端周期推 JPEG 二进制帧；前端 canvas 逐帧渲染。"""
    await _manager.subscribe(serial, ws)
    try:
        # 下行纯推帧；保持连接，直到客户端断开
        while True:
            await ws.receive_text()  # 忽略上行（前端心跳/指令预留）
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        _manager.unsubscribe(serial, ws)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
