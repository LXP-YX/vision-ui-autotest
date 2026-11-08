"""App REST 路由：设备/截图/触控/App 管理。

挂载在 /api/app 前缀下（main.py include_router）。底层用 core_app.adb_bridge。
"""

from __future__ import annotations

import base64
import sys
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

# 让 core_app 可被 import
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_app import adb_bridge  # noqa: E402
from core_app import apk_label  # noqa: E402

router = APIRouter(prefix="/api/app", tags=["app"])

# APK 临时存储目录（安装后自动清理）
_APK_STORE = Path(__file__).resolve().parent / "apk_store"
_APK_STORE.mkdir(parents=True, exist_ok=True)


# ------------------------- 请求体 -------------------------

class TapIn(BaseModel):
    x: int
    y: int


class SwipeIn(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    duration: int = 300


class KeyeventIn(BaseModel):
    keycode: int


class TypeIn(BaseModel):
    text: str


class LaunchIn(BaseModel):
    package: str
    activity: str


def _check_serial(serial: str) -> None:
    if not serial:
        raise HTTPException(400, "serial 不能为空")
    if not adb_bridge.is_online(serial):
        raise HTTPException(503, f"设备 {serial} 不在线")


# ------------------------- Android 设备管理（ADB） -------------------------

@router.get("/devices")
def list_devices():
    """列出所有已连接 ADB 设备。"""
    try:
        return adb_bridge.list_devices()
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))


@router.get("/devices/{serial}/status")
def device_status(serial: str):
    """设备在线状态 + 屏幕尺寸。"""
    try:
        return adb_bridge.device_status(serial)
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))


@router.get("/devices/{serial}/screenshot")
def device_screenshot(serial: str):
    """当前截图（JPEG Base64，供前端非投屏场景单帧拉取）。"""
    _check_serial(serial)
    try:
        png = adb_bridge.screenshot(serial)
        from PIL import Image
        im = Image.open(BytesIO(png))
        if im.mode != "RGB":
            im = im.convert("RGB")  # screencap PNG 常为 RGBA，JPEG 不支持 alpha
        w, h = im.size
        if w > 900:
            im = im.resize((900, int(h * 900 / w)))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=60)
        return {"data": base64.b64encode(buf.getvalue()).decode(),
                "width": im.size[0], "height": im.size[1]}
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # noqa: BLE001
        # PIL 不可用或截图失败，退回原始 PNG base64
        try:
            png = adb_bridge.screenshot(serial)
            return {"data": base64.b64encode(png).decode()}
        except adb_bridge.AdbError as e2:
            raise HTTPException(503, str(e2))


# ------------------------- 触控 / 按键 / 输入 -------------------------

@router.post("/devices/{serial}/tap")
def device_tap(serial: str, body: TapIn):
    _check_serial(serial)
    try:
        adb_bridge.tap(serial, body.x, body.y)
        return {"ok": True}
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))


@router.post("/devices/{serial}/swipe")
def device_swipe(serial: str, body: SwipeIn):
    _check_serial(serial)
    try:
        adb_bridge.swipe(serial, body.x1, body.y1, body.x2, body.y2, body.duration)
        return {"ok": True}
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))


@router.post("/devices/{serial}/keyevent")
def device_keyevent(serial: str, body: KeyeventIn):
    _check_serial(serial)
    try:
        adb_bridge.keyevent(serial, body.keycode)
        return {"ok": True}
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))


@router.post("/devices/{serial}/type")
def device_type(serial: str, body: TypeIn):
    _check_serial(serial)
    try:
        adb_bridge.input_text(serial, body.text)
        return {"ok": True}
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------- App 管理 -------------------------

@router.post("/install/{serial}")
async def install_app(serial: str, file: UploadFile = File(...)):
    """上传 APK 并安装。写入 apk_store/ 临时目录，安装后删除。"""
    _check_serial(serial)
    if not (file.filename or "").lower().endswith(".apk"):
        raise HTTPException(400, "仅支持 .apk 文件")
    tmp_path = _APK_STORE / f"{uuid.uuid4().hex}_{file.filename}"
    try:
        # aiofiles 写盘（大文件不占内存），缺失时退回同步写
        try:
            import aiofiles
            async with aiofiles.open(tmp_path, "wb") as f:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    await f.write(chunk)
        except ImportError:
            with open(tmp_path, "wb") as f:
                f.write(await file.read())
        ok, msg = adb_bridge.install(serial, str(tmp_path))
        if not ok:
            raise HTTPException(500, f"安装失败：{msg}")
        _PKG_CACHE.pop(serial, None)  # 安装后清空该设备的包信息缓存
        return {"success": True, "message": msg}
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


@router.get("/packages/{serial}")
def list_packages(serial: str):
    """已安装第三方 App 列表（含应用中文名与版本）。

    label 通过解析 APK 内 AndroidManifest/resources.arsc 获得（见 core_app/apk_label.py），
    结果按 serial+package 缓存；安装/卸载后清空该设备缓存。解析失败回退包名。
    """
    _check_serial(serial)
    try:
        pkgs = adb_bridge.list_third_party_packages(serial)
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))
    cache = _PKG_CACHE.setdefault(serial, {})
    result = []
    for p in pkgs:
        info = cache.get(p)
        if info is None:
            info = _resolve_package_info(serial, p)
            cache[p] = info
        result.append(info)
    return result


# 包信息缓存：serial -> package -> {package, label, version}
_PKG_CACHE: dict[str, dict[str, dict]] = {}


def _resolve_package_info(serial: str, package: str) -> dict:
    """单包信息解析：label（APK 资源解析）+ versionName（dumpsys）。"""
    label = None
    try:
        apk_path = adb_bridge.package_apk_path(serial, package)
        label = apk_label.apk_label(serial, apk_path)
    except adb_bridge.AdbError:
        label = None
    return {
        "package": package,
        "label": label or package,
        "version": adb_bridge.package_version(serial, package),
    }


@router.delete("/packages/{serial}/{package}")
def uninstall_app(serial: str, package: str):
    _check_serial(serial)
    ok, msg = adb_bridge.uninstall(serial, package)
    if not ok:
        raise HTTPException(500, f"卸载失败：{msg}")
    _PKG_CACHE.pop(serial, None)  # 卸载后清空该设备的包信息缓存
    return {"success": True, "message": msg}


@router.post("/devices/{serial}/launch")
def launch_app(serial: str, body: LaunchIn):
    """启动指定 App（对应工作台「启动 App」按钮）。"""
    _check_serial(serial)
    try:
        adb_bridge.start_app(serial, body.package, body.activity)
        return {"ok": True}
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))
