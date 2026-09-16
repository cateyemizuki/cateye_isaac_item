#!/usr/bin/env python
"""从游戏本体的中文语言包中提取中文名称表。

数据来源是玩家自己安装的游戏，不依赖任何第三方站点，可离线重复执行。

原理
----
游戏把资源打包进 ``resources/packed/*.a``。归档格式（社区逆向所得）为::

    偏移 0   7 字节   魔数 "ARCH000"
    偏移 7   1 字节   类型：0=异或加密  1=LZW 压缩  2=ISAAC 加密 + MiniZ
    偏移 8   4 字节   记录表起始偏移
    偏移 12  2 字节   记录条数

记录表每条 20 字节::

    hash(u32) key(u32) dataStart(u32) dataLen(u32) unused(u32)

其中 ``repentance_zh.a`` 的类型为 0（异或加密），解密算法见 ``decrypt_record``。
解出来的记录里，主 ``<stringtable>`` 是多语言对照表：每个 ``<key>`` 下按语言顺序
排列 8 个 ``<string>``，中文（简体）固定是第 4 个（下标 3）。

用法::

    python tools/extract_game_strings.py                     # 自动探测游戏目录
    python tools/extract_game_strings.py --pack <repentance_zh.a 路径>
    python tools/extract_game_strings.py --dry-run           # 只报告，不写文件
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

MASK = 0xFFFFFFFF

# 归档中记录的加密 key 会先经过这一步变换
_XOR_CONST = 0xF9524287

# stringtable 里语言顺序固定，中文（简体）在第 4 位
_LANG_ORDER = [
    "English", "Japanese", "Korean", "Chinese (Simple)",
    "Russian", "German", "Spanish", "French",
]
_ZH_INDEX = 3

# 默认游戏安装路径（Steam）
_DEFAULT_GAME_DIRS = [
    Path(r"D:\Steam\steamapps\common\The Binding of Isaac Rebirth"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\The Binding of Isaac Rebirth"),
    Path.home() / "Library/Application Support/Steam/steamapps/common/The Binding of Isaac Rebirth",
]

# 要导出的分类 -> 输出文件名
_CATEGORY_OUTPUT = {
    "Entities": "isaac_entities_zh.json",
    "Minibosses": "isaac_minibosses_zh.json",
    "Items": "isaac_items_zh_names.json",
    "Players": "isaac_players_zh.json",
    "Stages": "isaac_stages_zh.json",
    "Curses": "isaac_curses_zh.json",
}


# ----------------------------------------------------------------------
# .a 归档解密（类型 0：异或 + 字节交换）
# ----------------------------------------------------------------------

def _xor_key(key: int) -> int:
    return ((key ^ _XOR_CONST) | 1) & MASK


def decrypt_record(data: bytes, key: int) -> bytes:
    """解密类型 0 的记录。

    每 4 字节整体异或 key，再按 key 低 4 位做字节交换；随后 key 自身按
    「移位 + 异或」推进。注意先补齐到 255 的倍数，因为补齐会影响循环次数。
    """
    key = _xor_key(key)
    orig_size = len(data)
    if len(data) % 255 != 0:
        data = data + b"\x00" * (255 - len(data) % 255)

    buf = bytearray(data)
    for i in range(len(buf) // 4):
        o = i * 4
        word = int.from_bytes(buf[o:o + 4], "little") ^ key
        buf[o:o + 4] = word.to_bytes(4, "little")

        sel = (key & 0xF)
        sel = (sel - 2) & MASK
        if sel == 0:                       # 整字反转
            buf[o], buf[o + 3] = buf[o + 3], buf[o]
            buf[o + 1], buf[o + 2] = buf[o + 2], buf[o + 1]
        else:
            sel = (sel - 7) & MASK
            if sel == 0:                   # 两两互换
                buf[o], buf[o + 1] = buf[o + 1], buf[o]
                buf[o + 2], buf[o + 3] = buf[o + 3], buf[o + 2]
            else:
                sel = (sel - 4) & MASK
                if sel == 0:               # 前后半互换
                    buf[o], buf[o + 2] = buf[o + 2], buf[o]
                    buf[o + 1], buf[o + 3] = buf[o + 3], buf[o + 1]

        t = key
        t = (t << 8) & MASK
        t ^= key
        key = t
        key >>= 9
        t ^= key
        key = t
        key = (key << 0x17) & MASK
        key ^= t

    return bytes(buf[:orig_size])


def read_archive(path: Path) -> tuple[int, List[tuple]]:
    """返回 (类型, 记录表)。记录为 (hash, key, data_start, data_len, unused)。"""
    import struct

    raw = path.read_bytes()
    if raw[:7] != b"ARCH000":
        raise ValueError(f"{path.name} 不是 .a 归档（魔数 {raw[:7]!r}）")
    ftype = raw[7]
    rec_start, rec_cnt = struct.unpack_from("<IH", raw, 8)
    records = [struct.unpack_from("<IIIII", raw, rec_start + i * 20) for i in range(rec_cnt)]
    return ftype, records, raw


# ----------------------------------------------------------------------
# stringtable 解析
# ----------------------------------------------------------------------

def _parse_stringtable(xml_text: str) -> Dict[str, Dict[str, str]]:
    """把 <stringtable> 解析成 {分类: {键: 中文}}。"""
    result: Dict[str, Dict[str, str]] = {}
    for cat_match in re.finditer(r'<category name="([^"]+)">', xml_text):
        cat = cat_match.group(1)
        end = xml_text.find("</category>", cat_match.end())
        body = xml_text[cat_match.end():end if end > 0 else len(xml_text)]
        entries: Dict[str, str] = {}
        for key_match in re.finditer(r'<key name="([^"]+)">(.*?)</key>', body, re.S):
            key = key_match.group(1)
            strings = re.findall(r"<string>(.*?)</string>", key_match.group(2), re.S)
            strings = [s.strip() for s in strings]
            if len(strings) > _ZH_INDEX:
                zh = strings[_ZH_INDEX]
                if zh:
                    entries[key] = zh
        if entries:
            result[cat] = entries
    return result


def _find_stringtable(raw: bytes, ftype: int, records: List[tuple]) -> Optional[str]:
    """在归档里找出体积最大的那个 <stringtable> 记录。"""
    best: Optional[bytes] = None
    for _hash, key, dstart, dlen, _un in records:
        body = raw[dstart:dstart + dlen]
        if ftype == 0:
            body = decrypt_record(body, key)
        if body[:5] != b"<?xml" or b"<stringtable" not in body[:400]:
            continue
        if best is None or len(body) > len(best):
            best = body
    return best.decode("utf-8", "replace") if best else None


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------

def _locate_pack(explicit: Optional[str]) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise FileNotFoundError(f"找不到归档：{p}")
        return p
    for game_dir in _DEFAULT_GAME_DIRS:
        pack = game_dir / "resources" / "packed" / "repentance_zh.a"
        if pack.exists():
            return pack
    raise FileNotFoundError(
        "未找到 repentance_zh.a，请用 --pack 显式指定游戏语言包路径。"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从游戏语言包提取中文名称表")
    parser.add_argument("--pack", help="repentance_zh.a 的路径")
    parser.add_argument("--out", default="assets", help="输出目录（默认 assets）")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args(argv)

    pack = _locate_pack(args.pack)
    print(f"语言包：{pack}")
    ftype, records, raw = read_archive(pack)
    print(f"归档类型：{ftype}（0=异或加密）  记录数：{len(records)}")

    if ftype != 0:
        print("警告：本脚本目前只实现了类型 0 的解密，该归档可能无法解析。", file=sys.stderr)

    xml_text = _find_stringtable(raw, ftype, records)
    if not xml_text:
        print("未找到 <stringtable> 记录。", file=sys.stderr)
        return 1

    tables = _parse_stringtable(xml_text)
    print(f"解析到 {len(tables)} 个分类：")
    for cat, entries in sorted(tables.items(), key=lambda kv: -len(kv[1])):
        print(f"  {cat:20s} {len(entries):5d} 条")

    out_dir = Path(args.out)
    written = []
    for cat, filename in _CATEGORY_OUTPUT.items():
        entries = tables.get(cat)
        if not entries:
            continue
        payload = {
            "meta": {
                "source": "游戏本体 resources/packed/repentance_zh.a",
                "category": cat,
                "note": "从玩家自装游戏的语言包提取，非第三方整理；中文为游戏官方简体译文。",
                "count": len(entries),
            },
            "entries": entries,
        }
        if args.dry_run:
            print(f"[dry-run] 将写入 {out_dir / filename}（{len(entries)} 条）")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / filename
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written.append(target)
        print(f"已写入 {target}（{len(entries)} 条）")

    if written:
        print(f"\n完成，共 {len(written)} 个文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
