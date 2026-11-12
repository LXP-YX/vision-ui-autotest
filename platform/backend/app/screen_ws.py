"""App 实时投屏 WebSocket：截图采集循环 → 推订阅 WS（双平台）。

对标 Web 端 CDP screencast，差异：截图来源按平台选择——
- android：`adb exec-out screencap`；
- ios：`xcrun simctl / devicectl screenshot`（空闲预览；执行期间走 Appium session 帧）。
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

from core_app.drivers.android import adb as adb_bridge  # noqa: E402
from . import ios_tools  # noqa: E402

router = APIRouter(tags=["app-screen"])

FPS = 3  # 投屏目标帧率（adb screencap 单帧 ~0.8s，设 3 为上限更贴合实际）
INTERVAL = 1.0 / FPS


def _png_to_jpeg(png: bytes) -> Optional[bytes]:
    """PNG bytes → 限宽 900 的 JPEG bytes。失败返回 None。"""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        if im.mode != "RGB":
            im = im.convert("RGB")  # PNG 常为 RGBA，JPEG 不支持 alpha
        w, h = im.size
        if w > 900:
            im = im.resize((900, int(h * 900 / w)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=60)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _capture_jpeg(device_id: str, platform: str = "android") -> Optional[bytes]:
    """在线程池执行的同步采集：截图 → PIL JPEG bytes。失败返回 None。

    android screencap 单帧 ~0.8s；iOS xcrun 截图单帧 ~0.5-2s（模拟器较快）。
    瓶颈在传输与命令执行，无法纯软件提速。
    """
    try:
        if platform == "ios":
            png = ios_tools.ios_screenshot(device_id)
        else:
            png = adb_bridge.screenshot(device_id)
        return _png_to_jpeg(png) if png else None
    except Exception:  # noqa: BLE001
        return None


class _ScreencastManager:
    """多设备投屏采集 + 订阅管理（单事件循环，模块级单例）。"""

    def __init__(self) -> None:
        # (platform, device_id) -> {subscribers:set[WebSocket], task, last_frame:bytes}
        self._devices: dict[tuple[str, str], dict] = {}

    async def subscribe(self, device_id: str, ws: WebSocket,
                        platform: str = "android") -> None:
        await ws.accept()
        if not self._device_online(device_id, platform):
            await ws.send_json({"kind": "error", "payload": {"msg": f"设备 {device_id} 不在线"}})
            await ws.close()
            return

        key = (platform, device_id)
        entry = self._devices.get(key)
        if entry is None:
            entry = {"subscribers": set(), "task": None, "last_frame": b""}
            self._devices[key] = entry
        entry["subscribers"].add(ws)
        # 立刻推一帧缓存画面，避免黑屏等待
        if entry["last_frame"]:
            try:
                await ws.send_bytes(entry["last_frame"])
            except Exception:  # noqa: BLE001
                pass
        # 首个订阅者：启动采集循环
        if entry["task"] is None or entry["task"].done():
            entry["task"] = asyncio.create_task(self._capture_loop(device_id, platform))

    @staticmethod
    def _device_online(device_id: str, platform: str) -> bool:
        try:
            if platform == "ios":
                return any(d["udid"] == device_id for d in ios_tools.list_ios_devices())
            return adb_bridge.is_online(device_id)
        except Exception:  # noqa: BLE001
            return False

    def unsubscribe(self, device_id: str, ws: WebSocket,
                    platform: str = "android") -> None:
        key = (platform, device_id)
        entry = self._devices.get(key)
        if not entry:
            return
        entry["subscribers"].discard(ws)
        if not entry["subscribers"]:
            # 无订阅者：取消采集循环
            task = entry.get("task")
            if task and not task.done():
                task.cancel()
            self._devices.pop(key, None)

    async def _capture_loop(self, device_id: str, platform: str) -> None:
        """周期采集 + 广播，仅在有订阅者时运行。

        采集在独立线程池任务跑，本协程起下一帧采集后立刻 await 上一帧结果，
        形成采集流水线（采集耗时与广播重叠），实测帧率 ≈ 1/单帧采集耗时。
        adb screencap 单帧 ~0.8s，故帧率上限 ~1.1 FPS——这是投屏的固有限制，
        非 sleep 节流；INTERVAL 仅在采集快于目标时才起限制作用。
        """
        loop = asyncio.get_event_loop()
        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"app-screen-{device_id}")
        pending = loop.run_in_executor(pool, _capture_jpeg, device_id, platform)
        try:
            while True:
                entry = self._devices.get((platform, device_id))
                if not entry or not entry["subscribers"]:
                    break
                # 起下一帧采集（立刻排队，不阻塞），再等当前帧结果
                nxt = loop.run_in_executor(pool, _capture_jpeg, device_id, platform)
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
                                "payload": {"msg": f"截图失败，设备 {device_id} 可能已断开，2s 后重试"}})
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
                entry = self._devices.get((platform, device_id))
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


@router.websocket("/ws/app/screen/{device_id}")
async def ws_app_screen(ws: WebSocket, device_id: str, platform: str = "android"):
    """实时投屏：后端周期推 JPEG 二进制帧；前端 canvas 逐帧渲染。

    platform 查询参数：android（默认，ADB 采集）/ ios（xcrun 采集）。
    """
    await _manager.subscribe(device_id, ws, platform)
    try:
        # 下行纯推帧；保持连接，直到客户端断开
        while True:
            await ws.receive_text()  # 忽略上行（前端心跳/指令预留）
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        _manager.unsubscribe(device_id, ws, platform)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
