#!/usr/bin/env python
"""平台一键启动器：一条命令同时拉起前后端。

用法：
    python3 start.py            # 开发模式（默认）
    python3 start.py --prod      # 生产模式

依赖：pip install -r requirements.txt（PEP 668 管控的环境加 --break-system-packages）

开发模式（双进程双端口，前端热更新）：
- 后端：uvicorn main:app（8077，API + WebSocket）
- 前端：npm run dev（5177，热更新）
- 浏览器打开 http://127.0.0.1:5177

生产模式（单进程单端口，最接近真实部署）：
- 先 npm run build 构建前端 → frontend/dist
- 再 uvicorn main:app（80）同一进程托管 dist + API + WebSocket
- 浏览器打开 http://127.0.0.1（80 为特权端口，需 sudo 绑定）

两模式均：日志加 [后端]/[前端] 前缀；Ctrl+C 一并关闭（进程组整组杀）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLATFORM_DIR = ROOT / "platform"
BACKEND_DIR = PLATFORM_DIR / "backend"
FRONTEND_DIR = PLATFORM_DIR / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"

BACKEND_PORT = 8077      # 开发模式后端端口
FRONTEND_PORT = 5177     # 开发模式前端端口
PROD_PORT = 80           # 生产模式单进程端口（80 为特权端口，需 sudo 绑定）

# ANSI 颜色（前缀着色，便于区分两段日志）
CYAN = "\033[36m"
MAGENTA = "\033[35m"
RESET = "\033[0m"

_procs: list[subprocess.Popen] = []


def _stream(proc: subprocess.Popen, prefix: str, color: str) -> None:
    """把子进程输出逐行加前缀转发到本进程 stdout。"""
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"{color}[{prefix}]{RESET} {line.rstrip()}", flush=True)


def _preflight() -> bool:
    """启动前检查：npm 与 node_modules。"""
    if shutil.which("npm") is None:
        print(f"{MAGENTA}[前端]{RESET} 未找到 npm，请确认 Node 已安装并在 PATH 中")
        return False
    if not (FRONTEND_DIR / "node_modules").exists():
        print(f"{MAGENTA}[前端]{RESET} 未检测到 node_modules，请先执行：")
        print(f"    cd {FRONTEND_DIR} && npm install")
        return False
    return True


def _spawn(cmd: list[str], cwd: Path) -> subprocess.Popen:
    """起子进程：合并 stderr 到 stdout、独立进程组（关闭时整组杀）。

    给后端子进程注入 RECORDING_ENABLED=1，开启本地录制入口；云端部署（Docker/k8s）
    不设该变量则接口 403 + 前端隐藏入口。原因：录制需要真实有头浏览器，云端无显示器。
    """
    env = {**os.environ, "RECORDING_ENABLED": "1"}
    p = subprocess.Popen(
        cmd, cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, start_new_session=True,
        env=env,
    )
    _procs.append(p)
    return p


def _install_shutdown() -> None:
    """注册 Ctrl+C / SIGTERM：整组终止所有子进程。"""
    def _shutdown(signum=None, frame=None):
        print("\n正在关闭子进程 ...")
        for p in _procs:
            if p.poll() is None:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    p.terminate()
        for p in _procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    return _shutdown


def _watch_forever(shutdown) -> None:
    """守护循环：任一子进程退出则整体退出。"""
    while True:
        for p in _procs:
            code = p.poll()
            if code is not None:
                print(f"\n某子进程已退出（code={code}），关闭其余进程 ...")
                shutdown()
        try:
            _procs[0].wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _wait_backend_ready(proc: subprocess.Popen, port: int, timeout: float = 60.0) -> bool:
    """轮询探测后端端口是否已监听（就绪判据）。

    uvicorn 先跑 startup 钩子（init_db，此时端口未 listen），完成后才绑定端口。
    端口一旦 listen 即代表 API 可用，此时再起前端可避免代理打到未就绪后端的
    ECONNREFUSED。用状态探测而非固定 sleep：等待期间若后端进程已崩溃则提前返回。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False  # 后端进程已退出，无需再等
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def run_dev() -> int:
    """开发模式：后端 uvicorn + 前端 vite（热更新）。"""
    if not _preflight():
        return 1

    backend = _spawn(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
        BACKEND_DIR,
    )
    threading.Thread(target=_stream, args=(backend, "后端", CYAN), daemon=True).start()

    # 先等后端端口就绪再起前端，避免 vite 代理打到未监听的后端报 ECONNREFUSED
    print(f"{CYAN}[后端]{RESET} 等待就绪 ...")
    if not _wait_backend_ready(backend, BACKEND_PORT):
        print(f"{CYAN}[后端]{RESET} 启动失败或超时，已中止。")
        _install_shutdown()()
        return 1

    frontend = _spawn(["npm", "run", "dev"], FRONTEND_DIR)
    threading.Thread(target=_stream, args=(frontend, "前端", MAGENTA), daemon=True).start()

    print(f"\n{CYAN}后端{RESET} http://127.0.0.1:{BACKEND_PORT}  (API + WebSocket)")
    print(f"{MAGENTA}前端{RESET} http://127.0.0.1:{FRONTEND_PORT}  ← 浏览器打开这个（热更新）")
    print("按 Ctrl+C 关闭全部。\n")

    shutdown = _install_shutdown()
    _watch_forever(shutdown)
    return 0


def run_prod() -> int:
    """生产模式：先构建前端，再由后端单进程单端口托管 dist + API + WS。"""
    if not _preflight():
        return 1

    # ① 构建前端（阻塞直到完成）
    print(f"{MAGENTA}[前端]{RESET} 构建中：npm run build ...")
    build = _spawn(["npm", "run", "build"], FRONTEND_DIR)
    threading.Thread(target=_stream, args=(build, "前端", MAGENTA), daemon=True).start()
    ret = build.wait()
    _procs.remove(build)
    if ret != 0:
        print(f"{MAGENTA}[前端]{RESET} 构建失败（code={ret}），已中止。")
        return ret
    if not FRONTEND_DIST.exists():
        print(f"{MAGENTA}[前端]{RESET} 构建后未生成 dist 目录，已中止。")
        return 1
    print(f"{MAGENTA}[前端]{RESET} 构建完成 → {FRONTEND_DIST}")

    # ② 后端单进程托管（main.py 检测到 dist 存在会自动挂载为静态站点）
    bind_host = os.environ.get("UVICORN_HOST", "127.0.0.1")
    backend = _spawn(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", bind_host, "--port", str(PROD_PORT)],
        BACKEND_DIR,
    )
    threading.Thread(target=_stream, args=(backend, "后端", CYAN), daemon=True).start()

    print(f"\n{CYAN}后端{RESET} http://127.0.0.1:{PROD_PORT}  ← 浏览器打开这个"
          f"（前端页面 + API + WebSocket 同端口）")
    if PROD_PORT < 1024:
        print(f"{CYAN}提示{RESET} 端口 {PROD_PORT} 为特权端口，若报 Permission denied "
              f"请用 sudo 运行，或改用反向代理（nginx）转发到高位端口。")
    print("按 Ctrl+C 关闭。\n")

    shutdown = _install_shutdown()
    _watch_forever(shutdown)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="vision-ui-autotest 自动化测试平台启动器")
    parser.add_argument("--prod", action="store_true",
                        help="生产模式：构建前端后单进程单端口托管（默认开发模式）")
    args = parser.parse_args()
    return run_prod() if args.prod else run_dev()


if __name__ == "__main__":
    raise SystemExit(main())
