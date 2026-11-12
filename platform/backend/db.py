"""平台数据库：MySQL（首选）/ SQLite（回退）+ SQLAlchemy。

数据库连接读自 config.yaml 的 database 段：
- database.enabled=true 且连接成功 -> MySQL（库名 vision_ui_autotest）；
- 否则回退本地 SQLite（platform/platform.db），保证无 MySQL 时也能跑通。

表：TestCase（用例）、TestRun（运行）、LLMConfig（大模型配置）、Locator（自愈知识库）。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# 让 core 可被 import（读 config）
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402
from core import crypto  # noqa: E402

SQLITE_PATH = Path(__file__).resolve().parent.parent / "platform.db"


def _build_engine():
    """按配置构建 engine：MySQL 首选，失败回退 SQLite。返回 (engine, backend_str)。"""
    db = config.get("database", default={}) or {}
    if db.get("enabled"):
        user = db.get("user", "root")
        pwd = db.get("password", "")
        host = db.get("host", "127.0.0.1")
        port = int(db.get("port", 3306))
        name = db.get("name", "vision_ui_autotest")
        charset = db.get("charset", "utf8mb4")
        url = f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{name}?charset={charset}"
        try:
            eng = create_engine(url, echo=False, future=True, pool_pre_ping=True)
            with eng.connect():  # 探活
                pass
            return eng, f"MySQL({host}:{port}/{name})"
        except Exception as e:  # noqa: BLE001
            print(f"[db] MySQL 连接失败，回退 SQLite：{type(e).__name__}: {str(e)[:120]}")
    eng = create_engine(f"sqlite:///{SQLITE_PATH}", echo=False, future=True)
    return eng, f"SQLite({SQLITE_PATH.name})"


ENGINE, BACKEND = _build_engine()
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


class TestCase(Base):
    """自然语言用例。"""

    __tablename__ = "test_case"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    module: Mapped[str] = mapped_column(String(100), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    allow_risky: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # draft|active
    source: Mapped[str] = mapped_column(String(30), default="human")
    # 结构化执行脚本（编译产物）：steps 首次成功执行后编译落库，null 表示未编译。
    # 自然语言 steps 仍是唯一真源；改 steps 即作废脚本（置 null）重新编译。
    script: Mapped[list] = mapped_column(JSON, nullable=True, default=None)
    # Playwright Python 脚本（AI 编译产物）：由 script_compiler 从 steps 生成。
    script_py: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    script_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    # APP 自动化：driver_type='app' 走 core_app，缺省 'web' 走 Playwright。
    # app_env_id 绑定 app_env 环境（平台+设备+App）；device_serial 保留为临时手动覆盖。
    # app_package/app_activity 为旧字段，仅当用例未绑环境时兼容使用（android）。
    driver_type: Mapped[str] = mapped_column(String(20), default="web")
    app_package: Mapped[str] = mapped_column(String(200), default="")
    app_activity: Mapped[str] = mapped_column(String(200), default="")
    device_serial: Mapped[str] = mapped_column(String(100), default="")
    app_env_id: Mapped[int] = mapped_column(Integer, nullable=True)  # 关联 app_env.id
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "module": self.module,
            "tags": self.tags or [],
            "steps": self.steps or [],
            "allow_risky": self.allow_risky,
            "status": self.status,
            "source": self.source,
            "script": self.script,
            "script_py": self.script_py,
            "script_updated_at": self.script_updated_at.isoformat() if self.script_updated_at else None,
            "driver_type": self.driver_type or "web",
            "app_package": self.app_package or "",
            "app_activity": self.app_activity or "",
            "device_serial": self.device_serial or "",
            "app_env_id": self.app_env_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TestRun(Base):
    """一次运行记录。"""

    __tablename__ = "test_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="visual")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    step_results: Mapped[list] = mapped_column(JSON, default=list)
    screenshots: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "mode": self.mode,
            "status": self.status,
            "step_results": self.step_results or [],
            "screenshots": self.screenshots or [],
            "error": self.error or "",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


class LLMConfig(Base):
    """大模型配置（页面维护，多套可切换）。"""

    __tablename__ = "llm_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(300), default="")
    api_key: Mapped[str] = mapped_column(String(300), default="")
    chat_model: Mapped[str] = mapped_column(String(100), default="")
    vision_model: Mapped[str] = mapped_column(String(100), default="")
    timeout: Mapped[int] = mapped_column(Integer, default=60)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    @property
    def plain_api_key(self) -> str:
        """解密后的明文 api_key（供调用方发接口用）。"""
        return crypto.decrypt(self.api_key or "")

    def to_dict(self, mask: bool = True) -> dict:
        key = self.plain_api_key
        if mask and key:
            key = key[:6] + "****" + key[-4:] if len(key) > 12 else "****"
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key": key,
            "chat_model": self.chat_model,
            "vision_model": self.vision_model,
            "timeout": self.timeout,
            "verify_ssl": self.verify_ssl,
            "is_default": self.is_default,
            "enabled": self.enabled,
        }


class Locator(Base):
    """WEB 自愈知识库：自然语言锚点 → 稳定选择器（跨 run 共享）。

    与 APP 的自愈知识库（app_locator 表）表级拆分，互不混存：
    本表仅存 Playwright 选择器串；env_id 区分 WEB 测试环境（test_env.id），
    NULL 表示全局通用（旧数据/与环境无关的锚点）。
    """

    __tablename__ = "locator"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anchor: Mapped[str] = mapped_column(String(300), nullable=False)  # 自然语言描述
    selector: Mapped[str] = mapped_column(String(500), default="")
    strategy: Mapped[str] = mapped_column(String(20), default="store")  # store|semantic|vlm|hint
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    element_type: Mapped[str] = mapped_column(String(50), default="")  # AI 分类：一级菜单/按钮/输入框等
    env_id: Mapped[int] = mapped_column(Integer, nullable=True)  # 关联 test_env.id，NULL=全局
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "anchor": self.anchor,
            "selector": self.selector,
            "strategy": self.strategy,
            "hit_count": self.hit_count,
            "fail_count": self.fail_count,
            "element_type": self.element_type or "",
            "env_id": self.env_id,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AppLocator(Base):
    """APP 自愈知识库（与 WEB 的 locator 表级拆分）：自然语言锚点 → 结构化定位。

    selector 存 JSON 字符串 {"by":..,"value":..}，by ∈ text/desc/resource_id；
    strategy ∈ store|xml|vlm；platform ∈ android|ios（NULL=平台通用）；
    env_id 关联 app_env.id（NULL=全局通用，与环境无关的锚点）。
    """

    __tablename__ = "app_locator"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anchor: Mapped[str] = mapped_column(String(300), nullable=False)  # 自然语言描述
    selector: Mapped[str] = mapped_column(String(500), default="")  # JSON {"by","value"}
    strategy: Mapped[str] = mapped_column(String(20), default="store")  # store|xml|vlm
    platform: Mapped[str] = mapped_column(String(10), nullable=True)  # android|ios，NULL=通用
    env_id: Mapped[int] = mapped_column(Integer, nullable=True)  # 关联 app_env.id，NULL=全局
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    __table_args__ = (Index("idx_app_loc_anchor", "anchor"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "anchor": self.anchor,
            "selector": self.selector,
            "strategy": self.strategy,
            "platform": self.platform,
            "env_id": self.env_id,
            "hit_count": self.hit_count,
            "fail_count": self.fail_count,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }



class TestEnv(Base):
    """测试环境（页面维护，多套可切换，全落库）。

    每套是一组完整测试数据：名称 + 站点 URL + 账号 + 密码。
    站点 URL 就是「打开即用」的地址（一般填登录页 URL），不再单独维护登录路径；
    login_path 列仅保留不用（见下方字段注释），接口与页面均不再暴露。
    默认环境（is_default）供登录/录制/执行使用（见 core/config.py），
    完全以库为准、不读 config.yaml（仅首次空库时从 config.yaml 播种一条）。
    """

    __tablename__ = "test_env"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(300), default="")
    # 已废弃（登录路径并入站点 URL）：旧库该列为 NOT NULL 且无默认值，去掉映射会让
    # 新建环境写库失败，故保留列、统一写空串；按「现有字段只增不改」约定也不做删列迁移。
    login_path: Mapped[str] = mapped_column(String(100), default="")
    username: Mapped[str] = mapped_column(String(100), default="")
    password: Mapped[str] = mapped_column(String(200), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    def to_dict(self, mask: bool = True) -> dict:
        pwd = self.password or ""
        if mask and pwd:
            pwd = "****"
        return {
            "id": self.id, "name": self.name, "base_url": self.base_url,
            "username": self.username,
            "password": pwd, "is_default": self.is_default,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AppEnv(Base):
    """APP 测试环境：平台 + 设备 + 被测 App（+ 可选登录账号）绑成一套环境。

    与 WEB 的 test_env 完全独立。执行 APP 用例时按 app_env_id 取环境：
    platform 决定驱动实现（android=ADB / ios=Appium），device_id 为
    设备序号（serial）或 iOS 设备标识（udid）。
    """

    __tablename__ = "app_env"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    platform: Mapped[str] = mapped_column(String(10), default="android")  # android|ios
    device_id: Mapped[str] = mapped_column(String(100), default="")  # serial / udid
    app_id: Mapped[str] = mapped_column(String(200), default="")  # package / bundle_id
    app_activity: Mapped[str] = mapped_column(String(200), default="")  # Android 启动 Activity，iOS 留空
    username: Mapped[str] = mapped_column(String(100), default="")  # 可选登录账号
    password: Mapped[str] = mapped_column(String(200), default="")  # 可选登录密码
    remark: Mapped[str] = mapped_column(String(300), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    def to_dict(self, mask: bool = True) -> dict:
        pwd = self.password or ""
        if mask and pwd:
            pwd = "****"
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform or "android",
            "device_id": self.device_id or "",
            "app_id": self.app_id or "",
            "app_activity": self.app_activity or "",
            "username": self.username or "",
            "password": pwd,
            "remark": self.remark or "",
            "is_default": bool(self.is_default),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ApiDefinition(Base):
    """接口库：可复用的单个接口定义（method/path/头/参数/体 + 提取器 + 断言）。

    场景/前置步骤按 id 引用本接口。提取器与断言定义在接口上，场景只负责编排顺序。
    """

    __tablename__ = "api_definition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    module: Mapped[str] = mapped_column(String(100), default="")
    method: Mapped[str] = mapped_column(String(10), default="GET")
    path: Mapped[str] = mapped_column(String(500), default="")  # 相对 base_url 或完整 URL
    headers: Mapped[dict] = mapped_column(JSON, default=dict)  # {k: v}
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # query {k: v}
    body: Mapped[str] = mapped_column(Text, default="")  # 请求体（原始文本，支持 ${var}）
    body_type: Mapped[str] = mapped_column(String(10), default="json")  # json|form|raw
    extractors: Mapped[list] = mapped_column(JSON, default=list)  # [{name, jsonpath}]
    assertions: Mapped[list] = mapped_column(JSON, default=list)  # [{type, ...}]
    # 断言失败重试：间隔秒数 + 请求次数上限（默认 1 次=不重试，与旧行为一致）
    retry_interval: Mapped[float] = mapped_column(Float, default=1.0)  # 两次请求间隔（秒）
    retry_max: Mapped[int] = mapped_column(Integer, default=1)  # 请求次数上限（含首次）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "module": self.module,
            "method": self.method,
            "path": self.path,
            "headers": self.headers or {},
            "params": self.params or {},
            "body": self.body or "",
            "body_type": self.body_type,
            "extractors": self.extractors or [],
            "assertions": self.assertions or [],
            "retry_interval": self.retry_interval if self.retry_interval is not None else 1.0,
            "retry_max": self.retry_max if self.retry_max is not None else 1,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ApiGlobalVar(Base):
    """环境级全局变量模板：绑定某测试环境，作为场景运行的变量初始值。"""

    __tablename__ = "api_global_var"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    env_id: Mapped[int] = mapped_column(Integer, nullable=False)  # 关联 test_env.id
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
    module: Mapped[str] = mapped_column(String(100), default="")  # 所属模块
    description: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "env_id": self.env_id,
            "name": self.name,
            "value": self.value or "",
            "module": self.module or "",
            "description": self.description or "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ApiScenario(Base):
    """接口场景：编排多个接口（前置步骤 + 主步骤），v1 串行执行。"""

    __tablename__ = "api_scenario"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    module: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(String(500), default="")
    setup_steps: Mapped[list] = mapped_column(JSON, default=list)  # [{api_id, name, group}]
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{api_id, name, group}]
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "module": self.module,
            "description": self.description or "",
            "setup_steps": self.setup_steps or [],
            "steps": self.steps or [],
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ApiScenarioRun(Base):
    """接口场景运行记录。"""

    __tablename__ = "api_scenario_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    step_results: Mapped[list] = mapped_column(JSON, default=list)
    variables: Mapped[dict] = mapped_column(JSON, default=dict)  # 运行结束时变量快照
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "scenario_id": self.scenario_id,
            "status": self.status,
            "step_results": self.step_results or [],
            "variables": self.variables or {},
            "error": self.error or "",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


class ExecutionTemplate(Base):
    """用例执行模板：把若干条用例编排成一次批量运行。"""

    __tablename__ = "execution_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    case_ids: Mapped[list] = mapped_column(JSON, default=list)  # list[int]，按顺序串行
    on_failure: Mapped[str] = mapped_column(String(20), default="continue")  # continue|abort
    case_timeout: Mapped[int] = mapped_column(Integer, default=180)  # 单条用例超时（秒）
    description: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "case_ids": self.case_ids or [],
            "on_failure": self.on_failure,
            "case_timeout": self.case_timeout,
            "description": self.description or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ExecutionRun(Base):
    """一次模板执行的顶层记录（软删，超 7 天由列表接口触发物理清理）。"""

    __tablename__ = "execution_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(Integer, nullable=True)  # 模板可能被物理删
    template_name: Mapped[str] = mapped_column(String(100), default="")  # 冗余名，删模板也能查
    template_snapshot: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # 全量快照 {template:..., cases:[{id,name,steps,...}]}
    status: Mapped[str] = mapped_column(
        String(20), default="queued"
    )  # queued|running|passed|failed|error
    summary: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # {total, passed, failed, skipped, timeout}
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("idx_exec_run_deleted", "is_deleted", "deleted_at"),)

    def to_dict(self, snapshot: bool = False) -> dict:
        d = {
            "id": self.id,
            "template_id": self.template_id,
            "template_name": self.template_name or "",
            "status": self.status,
            "summary": self.summary or {},
            "error": self.error or "",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "is_deleted": bool(self.is_deleted),
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
        if snapshot:
            d["template_snapshot"] = self.template_snapshot or {}
        return d


class ExecutionCaseResult(Base):
    """一次模板执行中，某一条用例的结果记录。"""

    __tablename__ = "execution_case_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_index: Mapped[int] = mapped_column(Integer, default=0)  # 该 run 内顺序
    case_id: Mapped[int] = mapped_column(Integer, nullable=True)  # 用例可能被删
    case_name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(
        String(20), default="queued"
    )  # queued|running|passed|failed|error|timeout|skipped
    error: Mapped[str] = mapped_column(Text, default="")
    step_results: Mapped[list] = mapped_column(JSON, default=list)  # runner 的 step_results
    logs: Mapped[list] = mapped_column(JSON, default=list)  # [{level, text, ts}]
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("idx_exec_case_run", "run_id"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "case_index": self.case_index,
            "case_id": self.case_id,
            "case_name": self.case_name or "",
            "status": self.status,
            "error": self.error or "",
            "step_results": self.step_results or [],
            "logs": self.logs or [],
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


class ExecutionScreenshot(Base):
    """失败截图 BLOB 存 DB（MySQL 用 LONGBLOB，SQLite 用 BLOB）。"""

    __tablename__ = "execution_screenshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_result_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")  # 原文件名
    # MySQL 端强制 LONGBLOB（4GB 上限），SQLite 端自动是 BLOB
    data: Mapped[bytes] = mapped_column(LargeBinary().with_variant(LONGBLOB, "mysql"))
    content_type: Mapped[str] = mapped_column(String(50), default="image/png")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (Index("idx_exec_shot_result", "case_result_id"),)

    def to_dict(self) -> dict:
        # 不返回 data 二进制，另有 /api/execution-screenshots/{id} 走 Response 直出
        return {
            "id": self.id,
            "case_result_id": self.case_result_id,
            "name": self.name or "",
            "content_type": self.content_type or "image/png",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def init_db() -> None:
    """建表 + 首次写入种子用例 + 首次从 config.yaml 播种一条默认测试环境。"""
    print(f"[db] 使用后端：{BACKEND}")
    Base.metadata.create_all(ENGINE)
    _migrate_add_columns()
    _seed_test_env()
    with SessionLocal() as s:
        if s.query(TestCase).count() == 0:
            s.add_all(_seed_cases())
            s.commit()


def _migrate_add_columns() -> None:
    """轻量迁移：为已存在的旧表补新增列（只增不改）。

    现有字段只增不改；此处仅处理「代码已加列、旧库尚无该列」的场景，
    避免旧库查询报错。生产 MySQL 亦可靠 deploy 的 ALTER 脚本手动执行。
    """
    specs = [
        ("api_global_var", "module", "VARCHAR(100) DEFAULT ''"),
        ("api_definition", "retry_interval", "FLOAT DEFAULT 1.0"),
        ("api_definition", "retry_max", "INTEGER DEFAULT 1"),
        ("test_case", "script", "JSON NULL"),
        ("test_case", "script_py", "TEXT NULL"),
        ("test_case", "script_updated_at", "DATETIME NULL"),
        # APP 自动化：driver_type 缺省 web；app_* 仅 driver_type=app 时使用
        ("test_case", "driver_type", "VARCHAR(20) DEFAULT 'web'"),
        ("test_case", "app_package", "VARCHAR(200) DEFAULT ''"),
        ("test_case", "app_activity", "VARCHAR(200) DEFAULT ''"),
        ("test_case", "device_serial", "VARCHAR(100) DEFAULT ''"),
        # WEB/APP 解耦：自愈知识库加环境维度；用例绑定 APP 测试环境
        ("locator", "env_id", "INTEGER NULL"),
        ("test_case", "app_env_id", "INTEGER NULL"),
    ]
    insp = inspect(ENGINE)
    for table, column, ddl in specs:
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        if column in existing:
            continue
        with ENGINE.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        print(f"[db] 迁移：{table} 补列 {column}")


def _seed_test_env() -> None:
    """首次空库时，从 config.yaml 播种一条默认测试环境（仅此一次用到 config.yaml）。"""
    import sys as _sys
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent.parent
    if str(root) not in _sys.path:
        _sys.path.insert(0, str(root))
    with SessionLocal() as s:
        if s.query(TestEnv).count() > 0:
            return
        # 直接读 config.yaml 原始值（绕过 config.get 的 DB 覆盖逻辑，避免循环）
        from core.config import load_config
        cfg = load_config()
        site = cfg.get("site", {}) or {}
        acc = cfg.get("account", {}) or {}
        s.add(TestEnv(
            name="默认环境",
            base_url=config.site_open_url(site),
            username=acc.get("username", ""),
            password=acc.get("password", ""),
            is_default=True,
        ))
        s.commit()
        print("[db] 已从 config.yaml 播种默认测试环境")


def _seed_cases() -> list:
    """空库时播种的示例只读用例（与具体站点无关，可在页面自行增删）。

    步骤仅用 Planner 可执行动作：登录 / 进入X→Y / 点击 / 确认(视觉断言) / 确认无报错。
    全部只读，不含破坏性操作。
    """
    return [
        TestCase(name="登录冒烟", module="登录", tags=["冒烟", "只读"],
                 steps=["登录系统", "确认顶部导航可见且页面无报错"]),
        TestCase(name="首页健康检查", module="示例", tags=["示例", "只读"],
                 steps=["登录系统", "确认页面正常无报错"]),
    ]

