"""APK 应用名称（label）解析：纯 Python 读取 Android 二进制资源。

背景：`pm list packages` 只给包名，设备端没有现成命令输出应用中文名；
宿主机通常也没装 aapt。本模块用 adb 分片读取 APK 中的
AndroidManifest.xml（AXML）与 resources.arsc（二进制资源表），
解析出 android:label（字面字符串或 @string 引用），供 App 管理列表展示。

只读 zip 中央目录 + 目标条目（几 KB~几百 KB），不整包拉取。
全部 stdlib，无第三方依赖。
"""

from __future__ import annotations

import struct
import zlib
from typing import Optional

from core_app.drivers.android import adb as adb_bridge

# ------------------------- zip 分片读取 -------------------------

_EOCD_SIG = 0x06054B50
_CD_SIG = 0x02014B50
_LFH_SIG = 0x04034B50


def _read_zip_entries(serial: str, apk_path: str, names: set[str]) -> dict[str, bytes]:
    """从设备端 APK 中抽取指定条目内容（不整包拉取）。

    流程：读文件尾 64KB 找 EOCD → 读中央目录 → 定位条目 → 按本地头偏移读数据。
    """
    size = adb_bridge.file_size(serial, apk_path)
    tail_len = min(size, 65536 + 22)
    tail = adb_bridge.read_file_range(serial, apk_path, size - tail_len, tail_len)
    # 从尾部倒查 EOCD 签名
    pos = tail.rfind(struct.pack("<I", _EOCD_SIG))
    if pos < 0:
        raise ValueError("未找到 zip EOCD（非标准 APK）")
    cd_size, cd_offset = struct.unpack_from("<II", tail, pos + 12)

    cd = adb_bridge.read_file_range(serial, apk_path, cd_offset, cd_size)
    entries: dict[str, tuple[int, int, int]] = {}  # name -> (method, data_offset, comp_size)
    off = 0
    while off + 46 <= len(cd):
        if struct.unpack_from("<I", cd, off)[0] != _CD_SIG:
            break
        # 中央目录定长 46 字节头，按字段逐一解析
        method = struct.unpack_from("<H", cd, off + 10)[0]
        comp_size = struct.unpack_from("<I", cd, off + 20)[0]
        name_len = struct.unpack_from("<H", cd, off + 28)[0]
        extra_len = struct.unpack_from("<H", cd, off + 30)[0]
        comment_len = struct.unpack_from("<H", cd, off + 32)[0]
        lfh_off = struct.unpack_from("<I", cd, off + 42)[0]
        name = cd[off + 46: off + 46 + name_len].decode("utf-8", errors="replace")
        if name in names and name not in entries:
            entries[name] = (method, lfh_off, comp_size)
        off += 46 + name_len + extra_len + comment_len

    result: dict[str, bytes] = {}
    for name, (method, lfh_off, comp_size) in entries.items():
        # 本地头 30 字节：取真实 extra 长度后定位数据起点
        lfh = adb_bridge.read_file_range(serial, apk_path, lfh_off, 30)
        if struct.unpack_from("<I", lfh, 0)[0] != _LFH_SIG:
            continue
        n_len, e_len = struct.unpack_from("<HH", lfh, 26)
        data_off = lfh_off + 30 + n_len + e_len
        raw = adb_bridge.read_file_range(serial, apk_path, data_off, comp_size)
        if method == 0:      # stored
            result[name] = raw
        elif method == 8:    # deflate
            result[name] = zlib.decompress(raw, -15)
    return result


# ------------------------- 二进制 String Pool -------------------------

def _read_string_pool(data: bytes, off: int) -> tuple[list[str], int]:
    """解析 RES_STRING_POOL_TYPE chunk，返回 (字符串列表, chunk 总大小)。"""
    _, header_size, chunk_size = struct.unpack_from("<HHI", data, off)
    string_count, _style_count, flags, strings_start = struct.unpack_from("<IIII", data, off + 8)
    utf8 = bool(flags & 0x100)
    offsets_base = off + header_size
    strings_base = off + strings_start
    strings: list[str] = []
    for i in range(string_count):
        soff = strings_base + struct.unpack_from("<I", data, offsets_base + i * 4)[0]
        if utf8:
            # 先 UTF-16 长度（1~2 字节），再 UTF-8 字节长度（1~2 字节）
            v = data[soff]
            soff += 2 if v & 0x80 else 1
            blen = data[soff]
            if blen & 0x80:
                blen = ((blen & 0x7F) << 8) | data[soff + 1]
                soff += 2
            else:
                soff += 1
            strings.append(data[soff:soff + blen].decode("utf-8", errors="replace"))
        else:
            clen = struct.unpack_from("<H", data, soff)[0]
            soff += 2
            if clen & 0x8000:
                clen = ((clen & 0x7FFF) << 16) | struct.unpack_from("<H", data, soff)[0]
                soff += 2
            raw = data[soff:soff + clen * 2]
            strings.append(raw.decode("utf-16-le", errors="replace"))
    return strings, chunk_size


# ------------------------- AXML：找 application / 启动 Activity 的 android:label -------------------------

_RES_XML_START_ELEMENT = 0x0102
_RES_XML_END_ELEMENT = 0x0103
_MAIN_ACTION = "android.intent.action.MAIN"
_LAUNCHER_CATEGORY = "android.intent.category.LAUNCHER"


def _attr_value(axml: bytes, ao: int, pool: list[str]) -> tuple[Optional[str], Optional[int]]:
    """解析单个 20 字节属性结构的值，返回 (字符串, 资源 id)，两者必有其一为空。"""
    raw_value = struct.unpack_from("<I", axml, ao + 8)[0]
    data_type = axml[ao + 15]
    data = struct.unpack_from("<I", axml, ao + 16)[0]
    if data_type == 0x03 and data < len(pool):  # TYPE_STRING
        return pool[data], None
    if data_type == 0x01:  # TYPE_REFERENCE @string/xxx
        return None, data
    if raw_value != 0xFFFFFFFF and raw_value < len(pool):
        return pool[raw_value], None
    return None, None


def _find_attr(axml: bytes, off: int, pool: list[str], attr: str) -> tuple[Optional[str], Optional[int]]:
    """在 START_ELEMENT chunk 中按属性名取值。"""
    # ResXMLTree_attrExt：ns(off+16)/name(off+20)/attributeStart(off+24)/attributeSize(off+26)/attributeCount(off+28)
    attr_start = struct.unpack_from("<H", axml, off + 24)[0]
    attr_count = struct.unpack_from("<H", axml, off + 28)[0]
    base = off + 16 + attr_start  # attributeStart 相对 attrExt（ns 字段）起算
    for a in range(attr_count):
        ao = base + a * 20
        aname = struct.unpack_from("<I", axml, ao + 4)[0]
        if aname < len(pool) and pool[aname] == attr:
            return _attr_value(axml, ao, pool)
    return None, None


def _axml_labels(axml: bytes) -> tuple[tuple, tuple]:
    """返回 (application label, 启动 Activity label)，各为 (字符串, 资源 id) 二元组。

    桌面图标显示的是带 MAIN+LAUNCHER intent-filter 的 Activity 的 label
    （未单独设置时才回落 application label），因此两者都要解析。
    """
    empty = (None, None)
    if len(axml) < 8 or struct.unpack_from("<H", axml, 0)[0] != 0x0003:
        return empty, empty
    app: tuple = empty
    launcher: tuple = empty
    pool: list[str] = []
    cur: Optional[dict] = None  # 当前正在遍历的 activity 状态
    off = 8  # 跳过 RES_XML_TYPE 头
    while off + 8 <= len(axml):
        ctype, _hsize, csize = struct.unpack_from("<HHI", axml, off)
        if csize <= 0:
            break
        if ctype == 0x0001:  # String Pool
            pool, _ = _read_string_pool(axml, off)
        elif ctype in (_RES_XML_START_ELEMENT, _RES_XML_END_ELEMENT) and pool:
            name_idx = struct.unpack_from("<I", axml, off + 20)[0]
            ename = pool[name_idx] if name_idx < len(pool) else ""
            if ctype == _RES_XML_START_ELEMENT:
                if ename == "application" and app == empty:
                    app = _find_attr(axml, off, pool, "label")
                elif ename in ("activity", "activity-alias"):
                    cur = {"label": _find_attr(axml, off, pool, "label"), "main": False, "cat": False}
                elif ename == "action" and cur is not None:
                    v, _ = _find_attr(axml, off, pool, "name")
                    if v == _MAIN_ACTION:
                        cur["main"] = True
                elif ename == "category" and cur is not None:
                    v, _ = _find_attr(axml, off, pool, "name")
                    if v == _LAUNCHER_CATEGORY:
                        cur["cat"] = True
            elif ename in ("activity", "activity-alias") and cur is not None:
                # activity 结束：若是桌面入口且有独立 label，记为启动 label
                if cur["main"] and cur["cat"] and launcher == empty:
                    launcher = cur["label"]
                cur = None
        off += csize
    return app, launcher


# ------------------------- resources.arsc：按资源 id 取字符串 -------------------------

def _arsc_string(arsc: bytes, res_id: int) -> Optional[str]:
    """在二进制资源表中按 0x7fTTXXXX 形式的 id 取字符串值。

    同一 id 可能有多份语言配置（values/、values-zh-rCN/……），每份是一个
    RES_TABLE_TYPE chunk。桌面按系统语言选择，这里优先中文配置，
    其次默认配置（values/），最后任一可用配置。
    """
    if len(arsc) < 12 or struct.unpack_from("<H", arsc, 0)[0] != 0x0002:
        return None
    table_size = struct.unpack_from("<I", arsc, 4)[0]
    # 全局字符串池（存放资源值）
    values, pool_size = _read_string_pool(arsc, 12)
    target_type = (res_id >> 16) & 0xFF
    target_entry = res_id & 0xFFFF
    candidates: list[tuple[int, str]] = []  # (优先级, 值)：0=中文 1=默认 2=其他语言

    off = 12 + pool_size
    while off + 8 <= min(table_size, len(arsc)):
        ctype, header_size, csize = struct.unpack_from("<HHI", arsc, off)
        if csize <= 0:
            break
        if ctype != 0x0200:  # 只关心 RES_TABLE_PACKAGE_TYPE
            off += csize
            continue
        # 包内 chunk：遍历 TYPE（0x0201），同一 type 的每个语言配置各一个
        poff = off + header_size
        pend = off + csize
        while poff + 8 <= pend:
            ttype, t_hsize, t_size = struct.unpack_from("<HHI", arsc, poff)
            if t_size <= 0:
                break
            if ttype == 0x0201:  # RES_TABLE_TYPE_TYPE
                type_id = arsc[poff + 8]
                entry_count = struct.unpack_from("<I", arsc, poff + 12)[0]
                entries_start = struct.unpack_from("<I", arsc, poff + 16)[0]
                if type_id == target_type and target_entry < entry_count:
                    eoff = struct.unpack_from("<I", arsc, poff + t_hsize + target_entry * 4)[0]
                    if eoff != 0xFFFFFFFF:  # 该配置确实给此 entry 赋了值
                        eo = poff + entries_start + eoff
                        flags = struct.unpack_from("<H", arsc, eo + 2)[0]
                        if not (flags & 0x0001):  # 非 bag 才有简单值
                            data_type = arsc[eo + 11]
                            data = struct.unpack_from("<I", arsc, eo + 12)[0]
                            if data_type == 0x03 and data < len(values):
                                # 配置语言在 ResTable_config.locale（poff+28 起 2 字节）
                                lang = arsc[poff + 28: poff + 30]
                                if lang == b"zh":
                                    pri = 0
                                elif lang == b"\x00\x00":
                                    pri = 1
                                else:
                                    pri = 2
                                candidates.append((pri, values[data]))
            poff += t_size
        off += csize
    if candidates:
        candidates.sort(key=lambda c: c[0])
        return candidates[0][1]
    return None


# ------------------------- 对外入口 -------------------------

def apk_label(serial: str, apk_path: str) -> Optional[str]:
    """读取设备端 APK 的应用名称（与桌面图标显示一致）。

    桌面图标显示的是启动 Activity（MAIN+LAUNCHER intent-filter）的 label，
    未单独设置时才回落 application 的 label，因此优先取前者。
    解析失败返回 None。
    """
    try:
        files = _read_zip_entries(serial, apk_path, {"AndroidManifest.xml", "resources.arsc"})
        axml = files.get("AndroidManifest.xml")
        if not axml:
            return None
        app, launcher = _axml_labels(axml)
        arsc = files.get("resources.arsc")
        for label, res_id in (launcher, app):
            if label:
                return label
            if res_id is not None and arsc:
                v = _arsc_string(arsc, res_id)
                if v:
                    return v
    except Exception:  # noqa: BLE001 —— 任何解析/读取失败都不影响包列表主流程
        return None
    return None
