"""iOS 设备工具：设备枚举 + 截图（xcrun CLI，best-effort，仅 mac 环境可用）。

- 模拟器：`xcrun simctl list devices -j`（稳定 JSON 输出）；
- 真机：`xcrun devicectl list devices --json-output <file>`（Xcode 15+，失败则跳过）；
- 截图：模拟器 `xcrun simctl io <udid> screenshot <file>`；
        真机 `xcrun devicectl device screenshot --device-id <udid> <file>`。

命令不可用（非 mac / 无 Xcode）时返回空列表或 None，由调用方给出清晰错误。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


class IosToolError(RuntimeError):
    """xcrun 命令执行失败。"""


def _xcrun(*args: str, timeout: int = 30) -> str:
    if not shutil.which("xcrun"):
        raise IosToolError("未找到 xcrun（需要 mac + Xcode）")
    try:
        proc = subprocess.run(["xcrun", *args], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise IosToolError(f"xcrun {' '.join(args)} 超时") from None
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise IosToolError(f"xcrun {' '.join(args)} 失败：{err[:200]}")
    return proc.stdout.decode("utf-8", errors="replace")


def list_ios_devices() -> list[dict]:
    """枚举 iOS 设备：[{udid, name, state, kind, model}]。

    kind ∈ simulator|device；state：模拟器 Booted/Shutdown，真机 connected/available。
    真机枚举失败（Xcode < 15 等）不影响模拟器结果。
    """
    devices: list[dict] = []

    # 模拟器
    try:
        out = _xcrun("simctl", "list", "devices", "-j")
        data = json.loads(out)
        for runtime, sims in (data.get("devices") or {}).items():
            for sim in sims:
                devices.append({
                    "udid": sim.get("udid", ""),
                    "name": f'{sim.get("name", "")}（{runtime}）',
                    "state": sim.get("state", ""),
                    "kind": "simulator",
                    "model": "Simulator",
                })
    except (IosToolError, json.JSONDecodeError):
        pass

    # 真机（Xcode 15+ 的 devicectl；JSON 输出走临时文件）
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp_path = tf.name
        try:
            _xcrun("devicectl", "list", "devices", "--json-output", tmp_path, timeout=15)
            data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
            for d in (data.get("result") or {}).get("devices", []):
                props = d.get("deviceProperties") or {}
                devices.append({
                    "udid": d.get("identifier", ""),
                    "name": props.get("name", ""),
                    "state": d.get("connectionState", "") or "available",
                    "kind": "device",
                    "model": props.get("model", "") or d.get("deviceType", ""),
                })
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
    except (IosToolError, json.JSONDecodeError, KeyError):
        pass  # 真机枚举失败跳过（可能仅 Xcode 版本不支持），模拟器结果仍然可用

    return devices


def ios_screenshot(udid: str) -> bytes | None:
    """取当前屏幕 PNG bytes。模拟器/真机各走对应 xcrun 命令；失败返回 None。"""
    kind = _device_kind(udid)
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tmp_path = tf.name
        try:
            if kind == "simulator":
                _xcrun("simctl", "io", udid, "screenshot", tmp_path, timeout=15)
            else:
                _xcrun("devicectl", "device", "screenshot", "--device-id", udid,
                       tmp_path, timeout=20)
            data = Path(tmp_path).read_bytes()
            return data if data else None
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
    except (IosToolError, OSError):
        return None


def _device_kind(udid: str) -> str:
    """判断 udid 是模拟器还是真机：先按已有枚举查，查不到默认按模拟器处理。"""
    for d in list_ios_devices():
        if d["udid"] == udid:
            return d["kind"]
    # 模拟器 udid 是标准 UUID 格式，真机 udid 通常不是
    import re
    return "simulator" if re.fullmatch(
        r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",
        udid) else "device"
