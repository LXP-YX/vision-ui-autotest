"""大模型客户端（OpenAI 兼容接口封装）。

封装对话 / function call / 多模态图像输入，屏蔽端点差异，供 Planner、
视觉定位、视觉断言复用。

配置来源：DB 中 is_default=True 且 enabled=True 的 LLMConfig 记录。
DB 不可用或无默认配置时直接抛 LLMError，不回退 config.yaml。
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from typing import Any, Optional


class LLMError(RuntimeError):
    """大模型调用异常。"""


class LLMClient:
    """OpenAI 兼容对话客户端（stdlib 实现，无第三方依赖）。"""

    def __init__(self, cfg: dict) -> None:
        """cfg 必须传入，来源为 DB 中的 LLMConfig 记录。"""
        g = lambda k, d=None: cfg.get(k, d)
        self.base_url = (g("base_url", "") or "").rstrip("/")
        self.api_key = g("api_key", "") or ""
        self.chat_model = g("chat_model", "") or ""
        self.vision_model = g("vision_model", "") or self.chat_model
        self.timeout = int(g("timeout", 60) or 60)
        self.vision_timeout = int(g("vision_timeout", max(120, self.timeout)) or 120)
        verify = g("verify_ssl", True)
        self._ctx: Optional[ssl.SSLContext] = None
        if verify is False:
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    # ------------------------- 底层请求 -------------------------

    def _post(self, payload: dict, timeout: Optional[int] = None) -> dict:
        """POST /chat/completions，返回解析后的 JSON。"""
        if not self.base_url or not self.api_key:
            raise LLMError("llm 配置缺失：base_url / api_key 未设置")
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=timeout or self.timeout, context=self._ctx)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"HTTP {e.code}: {e.read().decode()[:300]}") from None
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"请求失败：{type(e).__name__}: {e}") from None

    @staticmethod
    def _message(resp: dict) -> dict:
        """从响应取 choices[0].message（兼容可能的 result 包裹）。"""
        root = resp.get("result", resp)
        choices = root.get("choices") or []
        if not choices:
            raise LLMError(f"响应无 choices：{json.dumps(resp, ensure_ascii=False)[:200]}")
        return choices[0].get("message", {})

    # ------------------------- 对外能力 -------------------------

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        timeout: Optional[int] = None,
    ) -> dict:
        """通用对话。返回 message dict（含 content / tool_calls）。"""
        payload: dict[str, Any] = {
            "model": model or self.chat_model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        return self._message(self._post(payload, timeout=timeout))

    def ask(self, prompt: str, model: Optional[str] = None) -> str:
        """最简单问答，返回纯文本 content。"""
        msg = self.chat([{"role": "user", "content": prompt}], model=model)
        return (msg.get("content") or "").strip()

    def ask_with_image(self, prompt: str, image_b64: str, model: Optional[str] = None,
                       mime: str = "jpeg", retries: int = 1) -> str:
        """多模态问答：文本 + 一张 base64 图像，返回纯文本 content。

        视觉推理慢且偶发超时，默认失败重试 1 次。
        """
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{image_b64}"}},
        ]
        last_err: Optional[Exception] = None
        for _ in range(retries + 1):
            try:
                msg = self.chat(
                    [{"role": "user", "content": content}],
                    model=model or self.vision_model,
                    timeout=self.vision_timeout,
                )
                return (msg.get("content") or "").strip()
            except LLMError as e:
                last_err = e
        raise last_err  # type: ignore[misc]

    def health_check(self) -> dict:
        """连通性自检：发一句最小对话，返回 {ok, model, latency_ms?, error?}。"""
        import time
        t0 = time.monotonic()
        try:
            text = self.ask("回复两个字：正常")
            return {
                "ok": True,
                "model": self.chat_model,
                "latency_ms": int((time.monotonic() - t0) * 1000),
                "reply": text[:20],
            }
        except LLMError as e:
            return {"ok": False, "model": self.chat_model, "error": str(e)}


# 模块级单例
_client: Optional[LLMClient] = None


def _load_db_default_cfg() -> dict:
    """从平台 DB 读默认大模型配置；不可用或无配置时抛 LLMError。"""
    try:
        import sys
        from pathlib import Path
        backend = Path(__file__).resolve().parent.parent / "platform" / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from db import LLMConfig, SessionLocal  # type: ignore
        with SessionLocal() as s:
            c = (
                s.query(LLMConfig)
                .filter(LLMConfig.enabled == True, LLMConfig.is_default == True)  # noqa: E712
                .first()
            )
            if c:
                return {
                    "base_url": c.base_url, "api_key": c.plain_api_key,
                    "chat_model": c.chat_model, "vision_model": c.vision_model,
                    "timeout": c.timeout, "verify_ssl": c.verify_ssl,
                }
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"DB 不可用，无法加载大模型配置：{e}") from None
    raise LLMError("DB 中无默认大模型配置，请在平台「大模型配置」页面设置一条默认配置")


def get_client(reload: bool = False) -> LLMClient:
    """获取客户端单例，配置来自 DB 默认 LLMConfig。DB 不可用或无配置时抛 LLMError。"""
    global _client
    if _client is None or reload:
        _client = LLMClient(_load_db_default_cfg())
    return _client


def plan_with_tools(step: str, system: str, tools: list[dict]) -> Optional[dict]:
    """通用 function-call 步骤解析骨架（WEB / APP Planner 共用）。

    把一条中文步骤交给模型在 tools 动作集中选择，返回 {"action": fn_name, "args": {...}}；
    LLM 不可用（LLMError）或未返回工具调用时返回 None（调用方回退规则式解析）。
    """
    try:
        msg = get_client().chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": step},
            ],
            tools=tools,
        )
    except LLMError:
        return None
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        return None
    fn = tool_calls[0].get("function", {})
    name = fn.get("name")
    if not name:
        return None
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except (json.JSONDecodeError, TypeError):
        args = {}
    return {"action": name, "args": args}
