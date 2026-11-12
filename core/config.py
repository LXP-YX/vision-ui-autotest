"""配置加载：读取 config.yaml，支持 ${ENV_VAR} 环境变量展开。

config.yaml 仅保留浏览器、截图目录、登录判据、数据库连接等基础配置。
站点地址、账号等测试数据优先由平台 DB 的默认测试环境提供；
大模型配置优先由平台 DB 的默认 LLMConfig 提供。
DB 不可用时回退 config.yaml 的 site / account 段（单机无平台用法），
两者都没有才抛错。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def _sub(m: re.Match) -> str:
            return os.environ.get(m.group(1), m.group(0))
        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: Path | None = None) -> dict:
    cfg_path = path or CONFIG_PATH
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return _expand_env(raw)


CONFIG: dict = load_config()

# 测试数据项：由平台 DB 默认测试环境提供，config.yaml 不再保有这些字段
_ENV_KEYS = {
    ("base_url",): "base_url",
    ("account", "username"): "username",
    ("account", "password"): "password",
}


def site_open_url(site: dict) -> str:
    """站点段 -> 打开即用的完整地址。

    登录路径已废弃（平台页面只填站点 URL），但旧的 config.yaml / 旧的库记录可能仍有
    login_path，此时一次性拼到 base_url 尾部，保证打开地址与改造前一致。
    base_url 已以该路径结尾则不重复拼，可安全反复调用。
    """
    base = (site.get("base_url") or "").rstrip("/")
    path = (site.get("login_path") or "").strip()
    if not base or not path or path == "/" or base.endswith(path):
        return base
    return f"{base}{path}"


def _default_env() -> dict:
    """读平台 DB 中的默认测试环境；DB 不可用或无默认时回退 config.yaml 的 site/account 段。"""
    try:
        import sys
        from pathlib import Path as _P
        backend = _P(__file__).resolve().parent.parent / "platform" / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from db import SessionLocal, TestEnv  # type: ignore
        with SessionLocal() as s:
            env = s.query(TestEnv).filter(TestEnv.is_default == True).first()  # noqa: E712
            if not env:
                env = s.query(TestEnv).order_by(TestEnv.id).first()
            if env:
                return {"base_url": env.base_url,
                        "username": env.username, "password": env.password}
    except Exception:
        pass  # DB 不可用：落到 config.yaml 回退
    # 回退：config.yaml 的 site / account 段（不依赖平台的单机用法）
    site = CONFIG.get("site", {}) or {}
    acc = CONFIG.get("account", {}) or {}
    if site.get("base_url"):
        return {"base_url": site_open_url(site),
                "username": acc.get("username", ""),
                "password": acc.get("password", "")}
    raise RuntimeError(
        "无测试环境配置：请在平台「测试环境管理」页面添加一条默认环境，"
        "或在 config.yaml 配置 site.base_url 与 account 段"
    )


def get(*keys: str, default: Any = None) -> Any:
    """按层级取配置项，例如 get('browser', 'headless')。

    base_url / account / account.username / account.password
    优先从平台 DB 默认测试环境读取，DB 不可用时回退 config.yaml 的 site/account 段。
    其余项从 config.yaml 读取。
    """
    field = _ENV_KEYS.get(keys)
    if field is not None:
        return _default_env().get(field)
    # get("account") 整体取账号字典
    if keys == ("account",):
        env = _default_env()
        return {"username": env.get("username", ""), "password": env.get("password", "")}
    node: Any = CONFIG
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node
