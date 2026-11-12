"""ADB 命令封装（Android）：设备管理 / 截图 / 触控 / 按键 / 输入 / App 管理 / UI dump。

设计要点：
- 纯 stdlib（subprocess），不引入第三方 Appium/Node 依赖；
- 所有设备相关命令带 `-s <serial>`，多设备互不干扰；
- `adb exec-out screencap -p` 直接走 stdout 取 PNG，不落盘；
- `uiautomator dump` 落 /sdcard 再 cat 回 XML 文本；
- `adb shell input text` 仅支持 ASCII（中文需 ADBKeyboard，本模块检测到非 ASCII 抛错提示）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

# 默认超时（秒）：普通命令快，install/dump 需更长
_TIMEOUT_DEFAULT = 10
_TIMEOUT_SCREENSHOT = 8
_TIMEOUT_DUMP = 15
_TIMEOUT_INSTALL = 180


class AdbError(RuntimeError):
    """ADB 命令执行异常。"""


def _adb(*args: str, timeout: int = _TIMEOUT_DEFAULT, capture: bool = True) -> bytes:
    """底层执行 adb 命令，返回 stdout bytes。

    失败（命令不存在 / 非零退出 / 超时）统一抛 AdbError。
    """
    try:
        proc = subprocess.run(
            ["adb", *args],
            capture_output=capture,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise AdbError("未找到 adb，请确认 Android Platform Tools 已安装且在 PATH") from None
    except subprocess.TimeoutExpired as e:
        raise AdbError(f"adb 命令超时：adb {' '.join(args)}") from None

    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise AdbError(f"adb {' '.join(args)} 失败（exit={proc.returncode}）：{err}")
    return proc.stdout or b""


def _adb_text(*args: str, timeout: int = _TIMEOUT_DEFAULT) -> str:
    """执行 adb 命令并以 UTF-8 文本返回 stdout（去尾换行）。"""
    out = _adb(*args, timeout=timeout)
    return out.decode("utf-8", errors="replace").strip()


# ------------------------- 设备管理 -------------------------

def list_devices() -> list[dict]:
    """列出所有已连接设备：[{serial, state}]。state ∈ device/offline/unauthorized。"""
    out = _adb_text("devices")
    devices = []
    for line in out.splitlines()[1:]:  # 首行 "List of devices attached"
        line = line.strip()
        if not line or not "\t" in line:
            continue
        serial, state = line.split("\t", 1)
        devices.append({"serial": serial.strip(), "state": state.strip()})
    return devices


def device_state(serial: str) -> str:
    """单个设备状态：device/offline/unauthorized（未连接返回空串）。"""
    try:
        return _adb_text("-s", serial, "get-state")
    except AdbError:
        return ""


def is_online(serial: str) -> bool:
    return device_state(serial) == "device"


def screen_size(serial: str) -> tuple[int, int]:
    """设备物理分辨率 (width, height)。解析 `wm size` 的 Physical size 行。"""
    out = _adb_text("-s", serial, "shell", "wm", "size")
    # 形如 "Physical size: 1080x2400"
    m = re.search(r"(\d+)\s*[x×]\s*(\d+)", out)
    if not m:
        raise AdbError(f"无法解析屏幕尺寸：{out}")
    return int(m.group(1)), int(m.group(2))


def device_status(serial: str) -> dict:
    """设备状态汇总：{serial, online, width, height}。离线时宽高为 0。"""
    online = is_online(serial)
    if not online:
        return {"serial": serial, "online": False, "width": 0, "height": 0}
    try:
        w, h = screen_size(serial)
    except AdbError:
        w, h = 0, 0
    return {"serial": serial, "online": True, "width": w, "height": h}


# ------------------------- 截图 -------------------------

def screenshot(serial: str) -> bytes:
    """取当前屏幕 PNG（bytes）。`adb exec-out screencap -p` 直接走 stdout。"""
    return _adb("-s", serial, "exec-out", "screencap", "-p", timeout=_TIMEOUT_SCREENSHOT)


# ------------------------- 触控 / 按键 / 输入 -------------------------

def tap(serial: str, x: int, y: int) -> None:
    _adb("-s", serial, "shell", "input", "tap", str(int(x)), str(int(y)))


def swipe(serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
    _adb("-s", serial, "shell", "input", "swipe",
         str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration_ms)))


def keyevent(serial: str, keycode: int) -> None:
    """发送按键事件。常用：3=HOME, 4=BACK, 24/25=音量±, 66=回车, 187=最近任务。"""
    _adb("-s", serial, "shell", "input", "keyevent", str(int(keycode)))


def input_text(serial: str, text: str) -> None:
    """向当前聚焦输入框输入文本。

    `adb shell input text` 仅支持 ASCII；中文/全角字符需 ADBKeyboard，本函数
    检测到非 ASCII 直接抛 ValueError，避免静默失败。
    """
    if not text:
        return
    if not all(ord(c) < 128 for c in text):
        raise ValueError(
            "adb shell input text 不支持非 ASCII 字符（中文需安装 ADBKeyboard 输入法）"
        )
    # 空格在 input text 里需转义为 %s
    safe = text.replace(" ", "%s")
    _adb("-s", serial, "shell", "input", "text", safe)


# ------------------------- App 管理 -------------------------

def install(serial: str, apk_path: str) -> tuple[bool, str]:
    """安装 APK（-r 覆盖安装）。返回 (success, message)。"""
    if not Path(apk_path).exists():
        return False, f"APK 文件不存在：{apk_path}"
    try:
        out = _adb_text("-s", serial, "install", "-r", apk_path, timeout=_TIMEOUT_INSTALL)
    except AdbError as e:
        return False, str(e)
    ok = "Success" in out
    return ok, out


def uninstall(serial: str, package: str) -> tuple[bool, str]:
    """卸载 App。返回 (success, message)。"""
    try:
        out = _adb_text("-s", serial, "uninstall", package, timeout=_TIMEOUT_INSTALL)
    except AdbError as e:
        return False, str(e)
    ok = "Success" in out
    return ok, out


def list_third_party_packages(serial: str) -> list[str]:
    """已安装第三方 App 包名列表（pm list packages -3）。"""
    out = _adb_text("-s", serial, "shell", "pm", "list", "packages", "-3")
    pkgs = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.append(line[len("package:"):])
    return sorted(pkgs)


def package_apk_path(serial: str, package: str) -> str:
    """App 安装 APK 在设备上的路径（pm path，取首个 split，通常是 base.apk）。"""
    out = _adb_text("-s", serial, "shell", "pm", "path", package)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            return line[len("package:"):]
    raise AdbError(f"pm path {package} 无输出")


def file_size(serial: str, remote_path: str) -> int:
    """设备端文件大小（字节）。优先 stat，失败回退 ls -l 解析。"""
    import shlex
    q = shlex.quote(remote_path)
    try:
        return int(_adb_text("-s", serial, "shell", "stat", "-c", "%s", q))
    except (AdbError, ValueError):
        out = _adb_text("-s", serial, "shell", "ls", "-l", q)
        parts = out.split()
        if len(parts) < 5 or not parts[4].isdigit():
            raise AdbError(f"无法获取文件大小：{remote_path} -> {out[:100]}")
        return int(parts[4])


def read_file_range(serial: str, remote_path: str, offset: int, length: int) -> bytes:
    """分片读取设备端文件的 [offset, offset+length) 区间。

    通过 `tail -c +N | head -c M` 流式截取（toybox tail 支持 +N 直接 seek），
    避免整包 cat 大 APK。/data/app 下 APK 对 shell 可读（0644），无需 root。
    """
    import shlex
    q = shlex.quote(remote_path)
    cmd = f"tail -c +{offset + 1} {q} | head -c {length}"
    return _adb("-s", serial, "exec-out", "sh", "-c", cmd, timeout=30)


def package_version(serial: str, package: str) -> str:
    """App versionName（dumpsys package 解析）。失败返回空串。"""
    try:
        out = _adb_text("-s", serial, "shell", "dumpsys", "package", package)
    except AdbError:
        return ""
    m = re.search(r"versionName=([^\s]+)", out)
    return m.group(1) if m else ""


def start_app(serial: str, package: str, activity: str) -> None:
    """通过 am start 启动指定 Activity。activity 可带或不带包名前缀。"""
    comp = activity if activity.startswith(package) else f"{package}/{activity}"
    _adb("-s", serial, "shell", "am", "start", "-n", comp)


def start_app_by_package(serial: str, package: str) -> None:
    """按包名启动 App 默认 Activity（monkey），activity 未知时使用。"""
    _adb("-s", serial, "shell", "monkey", "-p", package,
         "-c", "android.intent.category.LAUNCHER", "1")


def stop_app(serial: str, package: str) -> None:
    """强制停止 App。"""
    _adb("-s", serial, "shell", "am", "force-stop", package)


# ------------------------- UI dump（语义定位基础） -------------------------

def dump_ui(serial: str) -> str:
    """拉取当前界面 UI Hierarchy XML（uiautomator dump）。

    dump 默认写到设备 /sdcard/window_dump.xml，再 exec-out cat 回文本。
    XML 的 <node> 节点带 resource-id / text / content-desc / bounds / class 属性，
    由 android.ui_parser 归一化为 UiNode 供语义定位。
    """
    remote = "/sdcard/window_dump.xml"
    # dump 失败时（如设备繁忙）输出含 "ERROR"，按异常处理
    _adb("-s", serial, "shell", "uiautomator", "dump", remote, timeout=_TIMEOUT_DUMP)
    xml = _adb_text("-s", serial, "exec-out", "cat", remote, timeout=_TIMEOUT_DUMP)
    if not xml or "<hierarchy" not in xml and "<node" not in xml:
        raise AdbError(f"uiautomator dump 失败：{xml[:200]}")
    return xml
