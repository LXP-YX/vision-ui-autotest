"""App REST 路由：设备/截图/触控/App 管理 + APP 测试环境 + APP 自愈知识库。

挂载在 /api/app 前缀下（main.py include_router）。底层用 core_app.drivers
（android=ADB；iOS 设备枚举/截图走 xcrun CLI，交互控制与执行走 Appium）。
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

# ADB 命令封装（Android 设备管理）；别名保持 adb_bridge.* 调用点不变
from core_app.drivers.android import adb as adb_bridge  # noqa: E402
from core_app import apk_label  # noqa: E402
from core_app.drivers.ios.driver import default_server_url  # noqa: E402
from . import ios_tools  # noqa: E402

# APP 测试环境 / 自愈知识库落库（DB 不可用时环境接口报清晰错误）
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from db import AppEnv, AppLocator, SessionLocal  # type: ignore
    _DB_OK = True
except Exception:  # noqa: BLE001
    _DB_OK = False

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
def list_devices(platform: str = "android"):
    """列出设备。platform=android（默认，ADB）；platform=ios（xcrun 枚举模拟器+真机）。"""
    if platform == "ios":
        try:
            return ios_tools.list_ios_devices()
        except ios_tools.IosToolError as e:
            raise HTTPException(503, str(e))
    try:
        return adb_bridge.list_devices()
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))


@router.get("/appium-status")
def appium_status():
    """Appium Server 连通性（iOS 执行前置条件检查）。"""
    import requests
    url = default_server_url()
    try:
        r = requests.get(f"{url.rstrip('/')}/status", timeout=5)
        ok = r.status_code == 200
        return {"ok": ok, "server_url": url,
                "error": None if ok else f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "server_url": url,
                "error": f"无法连接 Appium Server：{type(e).__name__}"}


@router.get("/devices/{serial}/status")
def device_status(serial: str):
    """设备在线状态 + 屏幕尺寸。"""
    try:
        return adb_bridge.device_status(serial)
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))


def _png_to_jpeg_b64(png: bytes) -> tuple[str, int, int]:
    """PNG bytes → 限宽 900 的 JPEG base64。返回 (b64, width, height)。"""
    from PIL import Image
    im = Image.open(BytesIO(png))
    if im.mode != "RGB":
        im = im.convert("RGB")  # PNG 常为 RGBA，JPEG 不支持 alpha
    w, h = im.size
    if w > 900:
        im = im.resize((900, int(h * 900 / w)))
    buf = BytesIO()
    im.save(buf, "JPEG", quality=60)
    return base64.b64encode(buf.getvalue()).decode(), im.size[0], im.size[1]


@router.get("/devices/{serial}/screenshot")
def device_screenshot(serial: str, platform: str = "android"):
    """当前截图（JPEG Base64，供前端非投屏场景单帧拉取）。"""
    if platform == "ios":
        png = ios_tools.ios_screenshot(serial)
        if not png:
            raise HTTPException(503, "iOS 截图失败（需 mac + Xcode；模拟器需已启动）")
        try:
            data, w, h = _png_to_jpeg_b64(png)
            return {"data": data, "width": w, "height": h}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"截图处理失败：{e}")
    _check_serial(serial)
    try:
        png = adb_bridge.screenshot(serial)
        data, w, h = _png_to_jpeg_b64(png)
        return {"data": data, "width": w, "height": h}
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # noqa: BLE001
        # PIL 不可用或处理失败，退回原始 PNG base64
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
    """启动指定 App（对应工作台「启动 App」按钮，仅 Android）。"""
    _check_serial(serial)
    try:
        adb_bridge.start_app(serial, body.package, body.activity)
        return {"ok": True}
    except adb_bridge.AdbError as e:
        raise HTTPException(503, str(e))


# ------------------------- APP 测试环境 -------------------------

class AppEnvIn(BaseModel):
    name: str
    platform: str = "android"   # android|ios
    device_id: str = ""         # serial / udid
    app_id: str = ""            # package / bundle_id
    app_activity: str = ""      # Android 启动 Activity，iOS 留空
    username: str = ""
    password: str = ""
    remark: str = ""
    is_default: bool = False


def _require_db():
    if not _DB_OK:
        raise HTTPException(500, "数据库不可用（app_env 需要平台 DB）")


@router.get("/envs")
def list_app_envs():
    """APP 测试环境列表（密码掩码）。"""
    _require_db()
    with SessionLocal() as s:
        return [e.to_dict(mask=True) for e in s.query(AppEnv).order_by(AppEnv.id).all()]


@router.post("/envs")
def create_app_env(body: AppEnvIn):
    _require_db()
    if body.platform not in ("android", "ios"):
        raise HTTPException(400, "platform 仅支持 android / ios")
    with SessionLocal() as s:
        is_first = s.query(AppEnv).count() == 0
        e = AppEnv(**body.model_dump(), is_default=is_first or body.is_default)
        if body.is_default:
            s.query(AppEnv).update({AppEnv.is_default: False})
        s.add(e)
        s.commit()
        s.refresh(e)
        return e.to_dict()


@router.put("/envs/{env_id}")
def update_app_env(env_id: int, body: AppEnvIn):
    _require_db()
    with SessionLocal() as s:
        e = s.get(AppEnv, env_id)
        if not e:
            raise HTTPException(404, "APP 环境不存在")
        data = body.model_dump()
        if "****" in (data.get("password") or ""):  # 掩码：不改密码
            data.pop("password")
        for k, v in data.items():
            setattr(e, k, v)
        if body.is_default:
            # 设为默认时清掉其他默认（排除自身）
            s.query(AppEnv).filter(AppEnv.id != env_id).update({AppEnv.is_default: False})
        s.commit()
        s.refresh(e)
        return e.to_dict()


@router.delete("/envs/{env_id}")
def delete_app_env(env_id: int):
    _require_db()
    with SessionLocal() as s:
        e = s.get(AppEnv, env_id)
        if e:
            was_default = e.is_default
            s.delete(e)
            s.commit()
            # 删的是默认环境时，自动把最早的一条设为默认（与 WEB 环境管理行为一致）
            if was_default:
                nxt = s.query(AppEnv).order_by(AppEnv.id).first()
                if nxt:
                    nxt.is_default = True
                    s.commit()
        return {"ok": True}


@router.post("/envs/{env_id}/set-default")
def set_default_app_env(env_id: int):
    _require_db()
    with SessionLocal() as s:
        e = s.get(AppEnv, env_id)
        if not e:
            raise HTTPException(404, "APP 环境不存在")
        s.query(AppEnv).update({AppEnv.is_default: False})
        e.is_default = True
        s.commit()
        return {"ok": True}


# ------------------------- APP 自愈知识库 -------------------------

@router.get("/locators")
def list_app_locators(platform: str | None = None, env_id: int | None = None):
    """APP 自愈知识库列表（可按 platform / env_id 过滤）。"""
    _require_db()
    with SessionLocal() as s:
        q = s.query(AppLocator)
        if platform:
            q = q.filter((AppLocator.platform == platform) | (AppLocator.platform.is_(None)))
        if env_id:
            q = q.filter((AppLocator.env_id == env_id) | (AppLocator.env_id.is_(None)))
        return [x.to_dict() for x in q.order_by(AppLocator.id).all()]


@router.delete("/locators/{loc_id}")
def delete_app_locator(loc_id: int):
    _require_db()
    with SessionLocal() as s:
        obj = s.get(AppLocator, loc_id)
        if not obj:
            raise HTTPException(404, "app locator not found")
        s.delete(obj)
        s.commit()
        return {"ok": True}
