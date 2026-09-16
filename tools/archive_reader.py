#!/usr/bin/env python
"""读取《以撒的结合》``resources/packed/*.a`` 资源归档（只读）。

格式（已用本机游戏文件逐条验证）
--------------------------------
::

    AHeader: char magic[7];  // "ARCH000"
             u8  version;    // 0=加密(方法1) 1=LZW(分块) 2=DEFLATE(分块) 5=加密(方法2)
             u32 tableOffset;// 目录表偏移
             u16 entryCount; // 条目数
    AEntry : u32 hashA; u32 hashB; u32 offset; u32 size; u32 crc;
             // 目录表位于文件尾部：tableOffset + entryCount*20 == 文件长度
    data   : 位于 entry.offset，按块存放，每块 [u32 zsize][zsize 字节]，直到凑满 size 字节

**块语义**（本仓库实测得出，见 ``--check`` 的自检）：解压端按 **每块输出 1024 字节**推进，
因此某一块的 ``zsize`` 恰好等于这一块应有的输出长度时，该块是**原样存储**；
否则这 ``zsize`` 字节是一个**裸 deflate 流**。``zsize`` 的最高位是「最后一块」标记，
不参与长度计算。（只用「能不能 inflate」来判断会误判——PCM/WAV 这类数据偶尔能碰巧解出东西。）

**文件名没有存放在归档里**：目录表只有两个 32 位名字哈希，游戏本体靠内置的 filelist 反查，
所以本工具按**内容**查找条目（``find``）。

已知版本覆盖：``version == 2``（胎衣+ / 忏悔 / 忏悔+ 的 ``afterbirthp.a``、``repentance.a``）
可完整解码，本仓库对 10228 + 4180 条条目全部解码成功、长度与目录表声明一致。
``version == 1``（``config.a``、``animations.a`` 等 LZW 归档）与 ``version == 0``（加密）
**尚未实现**，会明确报错而不是给出错误数据。

格式信息来源
------------
结构体定义来自公开的逆向记录（zenhax 论坛 Ekey 的 AHeader/AEntry、Gibbed.Rebirth 的
ArchiveFile.cs），此处只按事实重新实现，未复制其代码；块语义与「原样存储」判据为本地实测结论。

用法::

    python tools/archive_reader.py list <archive.a>
    python tools/archive_reader.py check <archive.a> [条目数]
    python tools/archive_reader.py find <archive.a> "<entities"
    python tools/archive_reader.py extract <archive.a> <条目号> <输出文件>
"""

from __future__ import annotations

import argparse
import mmap
import struct
import sys
import zlib
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

MAGIC = b"ARCH000"
HEADER_SIZE = 14
ENTRY_SIZE = 20
#: 解压端每块产出的字节数（v1 / v2 共用）
CHUNK_OUTPUT = 1024

VERSION_LABELS = {
    0: "0 = 加密（方法 1，未实现）",
    1: "1 = LZW 分块（未实现）",
    2: "2 = DEFLATE 分块",
    5: "5 = 加密（方法 2，未实现）",
}


class ArchiveError(RuntimeError):
    """归档结构不符合预期时抛出（不返回半成品数据）。"""


def inflate(blob: bytes) -> bytes:
    """解一个裸 deflate 流。"""

    engine = zlib.decompressobj(-15)
    return engine.decompress(blob) + engine.flush()


class Archive:
    """只读的 ``.a`` 归档。支持 ``with`` 语句。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._handle = open(self.path, "rb")
        try:
            self._map = mmap.mmap(self._handle.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError as exc:  # 空文件
            self._handle.close()
            raise ArchiveError(f"{self.path.name}: 无法映射（文件为空？）") from exc
        if self._map[:7] != MAGIC:
            self.close()
            raise ArchiveError(f"{self.path.name}: 魔数不是 {MAGIC!r}")
        self.version = self._map[7]
        self.table_offset, self.entry_count = struct.unpack_from("<IH", self._map, 8)
        self.entries: List[Tuple[int, int, int, int, int]] = [
            struct.unpack_from("<IIIII", self._map, self.table_offset + index * ENTRY_SIZE)
            for index in range(self.entry_count)
        ]

    # -- 生命周期 ------------------------------------------------------
    def close(self) -> None:
        if getattr(self, "_map", None) is not None:
            self._map.close()
            self._map = None  # type: ignore[assignment]
        if getattr(self, "_handle", None) is not None:
            self._handle.close()
            self._handle = None  # type: ignore[assignment]

    def __enter__(self) -> "Archive":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- 结构自检 ------------------------------------------------------
    def describe(self) -> str:
        label = VERSION_LABELS.get(self.version, f"{self.version} = 未知")
        end = self.table_offset + self.entry_count * ENTRY_SIZE
        return (
            f"{self.path.name}: 版本 {label}，条目 {self.entry_count}，"
            f"目录表 {self.table_offset}，文件 {len(self._map)} 字节"
            f"（目录表末尾 {end}）"
        )

    def structure_ok(self) -> Tuple[bool, str]:
        end = self.table_offset + self.entry_count * ENTRY_SIZE
        if end != len(self._map):
            return False, f"目录表末尾 {end} != 文件长度 {len(self._map)}"
        offsets = [entry[2] for entry in self.entries]
        if offsets != sorted(offsets):
            return False, "条目数据偏移不是递增的"
        if offsets and offsets[0] < HEADER_SIZE:
            return False, f"首个条目偏移 {offsets[0]} 落在头部里"
        return True, "头 / 目录表 / 数据区对得上"

    # -- 条目 ----------------------------------------------------------
    def span(self, index: int) -> Tuple[int, int]:
        """条目的数据区 ``[起, 止)``：止于下一条目起点（最后一条止于目录表）。"""

        start = self.entries[index][2]
        end = self.entries[index + 1][2] if index + 1 < self.entry_count else self.table_offset
        return start, end

    def chunks(self, index: int) -> List[Tuple[int, bytes]]:
        """按块切开条目数据区，返回 ``[(是否最后一块, 该块字节), …]``。"""

        if self.version != 2:
            raise ArchiveError(
                f"版本 {self.version} 的块编码未实现（本工具目前只支持 version == 2）"
            )
        start, end = self.span(index)
        cursor = start
        blocks: List[Tuple[int, bytes]] = []
        while cursor + 4 <= end:
            (raw_size,) = struct.unpack_from("<I", self._map, cursor)
            size = raw_size & 0x7FFFFFFF
            if size == 0 or cursor + 4 + size > end:
                raise ArchiveError(f"条目 {index}: 块长度 {size} 越界（{cursor}）")
            blocks.append((raw_size >> 31, bytes(self._map[cursor + 4 : cursor + 4 + size])))
            cursor += 4 + size
        if cursor != end:
            raise ArchiveError(f"条目 {index}: 分块在 {cursor} 结束，数据区到 {end}")
        return blocks

    def read_entry(self, index: int) -> bytes:
        """解出条目的原始内容；长度与目录表声明不符时抛错。"""

        expect = self.entries[index][3]
        out = bytearray()
        for _last, blob in self.chunks(index):
            wanted = min(CHUNK_OUTPUT, expect - len(out))
            if len(blob) == wanted:
                out += blob  # 原样存储
            else:
                out += inflate(blob)
        if len(out) != expect:
            raise ArchiveError(f"条目 {index}: 解出 {len(out)} 字节，目录表声明 {expect}")
        return bytes(out)

    def find(self, needle: bytes, *, limit: int = 0) -> Iterator[Tuple[int, bytes]]:
        """按内容查找条目，产出 ``(条目号, 内容)``。

        归档不保存文件名，只能按内容找；``limit`` > 0 时只返回前 ``limit`` 字节。
        """

        for index in range(self.entry_count):
            try:
                data = self.read_entry(index)
            except (ArchiveError, zlib.error):
                continue
            if needle not in data:
                continue
            yield index, (data[:limit] if limit else data)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    with Archive(args.archive) as archive:
        print(archive.describe())
        ok, verdict = archive.structure_ok()
        print(f"结构自检：{'通过' if ok else '不通过'} —— {verdict}")
        for index in range(min(args.count or archive.entry_count, archive.entry_count)):
            _a, _b, offset, size, crc = archive.entries[index]
            start, end = archive.span(index)
            print(
                f"  [{index:>6}] offset={offset:>10} size={size:>10} "
                f"span={end - start:>10} crc={crc:#010x}"
            )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    with Archive(args.archive) as archive:
        print(archive.describe())
        ok, verdict = archive.structure_ok()
        print(f"结构自检：{'通过' if ok else '不通过'} —— {verdict}")
        total = args.count or 200
        good = bad = 0
        for index in range(min(total, archive.entry_count)):
            try:
                data = archive.read_entry(index)
            except (ArchiveError, zlib.error) as exc:
                bad += 1
                if bad <= 5:
                    print(f"  [{index}] 失败：{exc}")
                continue
            good += 1
        print(f"解码 {good + bad} 条：成功 {good}，失败 {bad}")
    return 0 if bad == 0 else 2


def cmd_find(args: argparse.Namespace) -> int:
    needle = args.needle.encode("utf-8")
    with Archive(args.archive) as archive:
        print(archive.describe())
        found = 0
        for index, data in archive.find(needle):
            found += 1
            print(f"  命中 [{index}] size={archive.entries[index][3]} 头 80 字节：{data[:80]!r}")
        print(f"共 {found} 条命中")
    return 0 if found else 1


def cmd_extract(args: argparse.Namespace) -> int:
    with Archive(args.archive) as archive:
        data = archive.read_entry(args.index)
        Path(args.out).write_bytes(data)
        print(f"已写出 {len(data)} 字节到 {args.out}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="读取以撒的 .a 资源归档（只读）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出条目")
    p_list.add_argument("archive", type=Path)
    p_list.add_argument("--count", type=int, default=0)
    p_list.set_defaults(func=cmd_list)

    p_check = sub.add_parser("check", help="解码前 N 条做自检")
    p_check.add_argument("archive", type=Path)
    p_check.add_argument("count", nargs="?", type=int, default=200)
    p_check.set_defaults(func=cmd_check)

    p_find = sub.add_parser("find", help="按内容查找条目")
    p_find.add_argument("archive", type=Path)
    p_find.add_argument("needle")
    p_find.set_defaults(func=cmd_find)

    p_extract = sub.add_parser("extract", help="解出某一条")
    p_extract.add_argument("archive", type=Path)
    p_extract.add_argument("index", type=int)
    p_extract.add_argument("out", type=Path)
    p_extract.set_defaults(func=cmd_extract)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ArchiveError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
