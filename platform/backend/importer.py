"""接口导入解析器：Postman Collection v2.1 JSON 与 curl 命令 → 接口库草稿。

产出统一的接口草稿 dict（对齐 ApiDefinition 字段）：
    {name, module, method, path, headers, params, body, body_type}
不含提取器/断言（导入后人工补充）。

变量策略（对齐仓库 ${var} 体系）：
- {{var}} 一律转成 ${var}；
- URL 若以变量（如 ${baseUrl}/${host}）开头，剥离该前缀只留相对路径，
  避免与本项目 base_url 重复拼接。
"""

from __future__ import annotations

import json as _json
import re
import shlex
from typing import Any
from urllib.parse import parse_qsl, urlsplit

# {{var}} / {{ var }} → ${var}
_MUSTACHE = re.compile(r"\{\{\s*([\w\-\.]+)\s*\}\}")
# 以变量开头：${var} 或 ${var}/... （用于剥离 URL 的 host 变量前缀）
_LEADING_VAR = re.compile(r"^\$\{[\w\-\.]+\}")


def _conv(text: str) -> str:
    """{{var}} → ${var}（其余原样）。"""
    if not text:
        return text or ""
    return _MUSTACHE.sub(lambda m: "${" + m.group(1) + "}", text)


def _norm_path(raw_url: str) -> tuple[str, dict]:
    """把 URL 拆成 (path, params)。

    - 先做 {{}}→${} 转换；
    - 完整 http(s) URL：保留完整 URL 作 path，query 拆入 params；
    - 以变量开头（${baseUrl}/...）：剥离变量前缀只留相对路径；
    - 其余：原样作相对路径，query 拆入 params。
    """
    url = _conv((raw_url or "").strip())
    if not url:
        return "", {}

    # 拆 query（? 之后）——变量场景下 urlsplit 也能正确切出 query
    if "?" in url:
        base, qs = url.split("?", 1)
        params = {k: v for k, v in parse_qsl(qs, keep_blank_values=True)}
    else:
        base, params = url, {}

    if base.startswith("http://") or base.startswith("https://"):
        # 完整 URL：若 host 段是变量（如 https://${baseUrl}/...），视作站点根变量剥离，
        # 只留相对路径避免与 base_url 重复拼接；真实域名则原样保留。
        rest = base.split("://", 1)[1]
        if rest.startswith("${"):
            m = _LEADING_VAR.match(rest)
            if m:
                tail = rest[m.end():]
                return ("/" + tail.lstrip("/")) if tail.lstrip("/") else "/", params
        return base, params  # 真实完整 URL：直用，query 已拆出

    # 剥离开头的 ${var} host 前缀 → 相对路径
    stripped = _LEADING_VAR.sub("", base)
    if stripped != base:
        base = stripped
        if not base.startswith("/"):
            base = "/" + base
    return base, params


def _headers_to_dict(header_list: list) -> dict:
    """Postman header[] → {k:v}，跳过 disabled。"""
    out: dict = {}
    for h in header_list or []:
        if not isinstance(h, dict) or h.get("disabled"):
            continue
        k = h.get("key")
        if k:
            out[k] = _conv(str(h.get("value", "")))
    return out


def _looks_json(text: str) -> bool:
    try:
        _json.loads(text)
        return True
    except Exception:  # noqa: BLE001
        return False


def _pm_body(body: dict) -> tuple[str, str]:
    """Postman body → (body_text, body_type)。form 统一存 {k:v} 的 JSON 串。"""
    if not isinstance(body, dict):
        return "", "json"
    mode = body.get("mode")
    if mode == "raw":
        raw = _conv(body.get("raw", ""))
        lang = (body.get("options", {}).get("raw", {}) or {}).get("language", "")
        btype = "json" if (lang == "json" or _looks_json(raw)) else "raw"
        return raw, btype
    if mode == "urlencoded":
        kv = {i.get("key"): _conv(str(i.get("value", "")))
              for i in body.get("urlencoded", []) if i.get("key") and not i.get("disabled")}
        return _json.dumps(kv, ensure_ascii=False), "form"
    if mode == "formdata":
        kv = {i.get("key"): _conv(str(i.get("value", "")))
              for i in body.get("formdata", [])
              if i.get("key") and not i.get("disabled") and i.get("type") != "file"}
        return _json.dumps(kv, ensure_ascii=False), "form"
    return "", "json"


def _walk_items(items: list, module: str, out: list) -> None:
    """递归遍历 Postman item[]（文件夹→子项），收集接口草稿。"""
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if "item" in it:  # 文件夹：名字作 module 下钻
            _walk_items(it.get("item", []), it.get("name", module), out)
            continue
        req = it.get("request")
        if not req:
            continue
        if isinstance(req, str):  # 极简写法：request 直接是 URL 字符串
            req = {"method": "GET", "url": req}
        url_raw = req.get("url")
        if isinstance(url_raw, dict):
            url_raw = url_raw.get("raw", "")
        path, params = _norm_path(url_raw or "")
        body_text, body_type = _pm_body(req.get("body", {}))
        out.append({
            "name": it.get("name") or path or "未命名接口",
            "module": module or "",
            "method": (req.get("method") or "GET").upper(),
            "path": path,
            "headers": _headers_to_dict(req.get("header", [])),
            "params": params,
            "body": body_text,
            "body_type": body_type,
            "extractors": [],
            "assertions": [],
        })


def parse_postman(text: str) -> list[dict]:
    """解析 Postman Collection JSON 文本，返回接口草稿列表。"""
    data = _json.loads(text)
    if not isinstance(data, dict) or "item" not in data:
        raise ValueError("不是有效的 Postman Collection（缺少 item 字段）")
    out: list = []
    root_module = (data.get("info", {}) or {}).get("name", "")
    _walk_items(data.get("item", []), root_module, out)
    if not out:
        raise ValueError("Postman Collection 中未解析到任何接口")
    return out


# ------------------------- curl -------------------------

_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def parse_curl(text: str) -> list[dict]:
    """解析单条 curl 命令，返回单元素接口草稿列表。"""
    if not text or "curl" not in text:
        raise ValueError("不是有效的 curl 命令")
    # 归并行续（\ 换行）与多余空白后分词
    cleaned = re.sub(r"\\\s*\n", " ", text.strip())
    cleaned = cleaned.replace("\n", " ")
    tokens = shlex.split(cleaned)
    if tokens and tokens[0] == "curl":
        tokens = tokens[1:]

    method: str | None = None
    url = ""
    headers: dict = {}
    data_parts: list[str] = []
    form_kv: dict = {}
    is_get = False

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-X", "--request"):
            i += 1
            if i < len(tokens):
                method = tokens[i].upper()
        elif t in ("-H", "--header"):
            i += 1
            if i < len(tokens) and ":" in tokens[i]:
                k, v = tokens[i].split(":", 1)
                headers[k.strip()] = _conv(v.strip())
        elif t in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"):
            i += 1
            if i < len(tokens):
                data_parts.append(tokens[i])
        elif t in ("-F", "--form"):
            i += 1
            if i < len(tokens) and "=" in tokens[i]:
                k, v = tokens[i].split("=", 1)
                # 剥离 formdata 的文件标记（;type= / @file 只留占位）
                form_kv[k.strip()] = _conv(v.split(";")[0].lstrip("@"))
        elif t in ("-G", "--get"):
            is_get = True
        elif t == "--url":
            i += 1
            if i < len(tokens):
                url = tokens[i]
        elif t in ("-u", "--user"):
            i += 1  # basic auth：跳过取值，不入库敏感信息
        elif t.startswith("-"):
            pass  # 其余 flag（-s/-k/-i/-L/--compressed 等）忽略
        elif not url:
            url = t  # 第一个非 flag 裸参数为 URL
        i += 1

    if not url:
        raise ValueError("curl 命令中未找到 URL")

    data_text = "&".join(data_parts)
    path, params = _norm_path(url)

    # -G：data 并入 query
    if is_get and data_text:
        for k, v in parse_qsl(data_text, keep_blank_values=True):
            params[k] = v
        data_text = ""

    # 组装 body
    body, body_type = "", "json"
    if form_kv:
        body, body_type = _json.dumps(form_kv, ensure_ascii=False), "form"
    elif data_text:
        conv = _conv(data_text)
        ct = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        if "json" in ct.lower() or _looks_json(conv):
            body, body_type = conv, "json"
        elif "=" in conv and "{" not in conv:
            kv = {k: v for k, v in parse_qsl(conv, keep_blank_values=True)}
            body, body_type = _json.dumps(kv, ensure_ascii=False), "form"
        else:
            body, body_type = conv, "raw"

    # method 推断：显式 -X 优先；否则有 body → POST，其余 GET
    if not method:
        method = "POST" if (data_text or form_kv) and not is_get else "GET"

    # 接口名：取 path 末段
    name = (urlsplit(path).path if path.startswith("http") else path).rstrip("/").split("/")[-1] or "curl接口"

    return [{
        "name": name,
        "module": "",
        "method": method,
        "path": path,
        "headers": headers,
        "params": params,
        "body": body,
        "body_type": body_type,
        "extractors": [],
        "assertions": [],
    }]


def parse_import(text: str, kind: str = "auto") -> list[dict]:
    """统一入口：kind ∈ auto|postman|curl。auto 按内容猜测。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("导入内容为空")
    if kind == "postman":
        return parse_postman(text)
    if kind == "curl":
        return parse_curl(text)
    # auto：以 { 开头且含 "item" 视作 Postman，含 curl 视作 curl
    if text.lstrip().startswith("{"):
        return parse_postman(text)
    if "curl" in text.split("\n", 1)[0]:
        return parse_curl(text)
    raise ValueError("无法识别导入内容（既非 Postman JSON 也非 curl）")
