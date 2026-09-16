#!/usr/bin/env python
"""对比两份存档，打印差异（只读，不修改任何文件）。

用途有两个：

1. **确认计数器口径**：捐 1 枚硬币（或打一个 BOSS、通关一层）前后各存一份存档，
   跑一次本脚本，看是哪一项发生了变化——
   ``python tools/diff_saves.py 旧.dat 新.dat``
2. **反推 BOSS / 小 BOSS 编号**：打一个新 BOSS 前后的存档对比，看 ``BOSS 段`` 里
   由 0 变 1 的下标，即可确定该 BOSS 在存档里的编号（名称表 ``boost`` 填表用）。

输出内容：
    版本 / 大小 / 段对齐情况；每个进度段新增与减少的下标（有名称表的会带中文名）；
    以及全部发生变化的全局计数器（下标、旧值 → 新值）。

用法::

    python tools/diff_saves.py <旧存档.dat> <新存档.dat>
    python tools/diff_saves.py a.dat b.dat --max-counters 60
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from isaac_save import (  # noqa: E402
    TRACK_CHUNKS,
    NameTables,
    SaveAnalysis,
    SaveFormatError,
    parse_save_bytes,
)

DEFAULT_NAMES = Path(__file__).resolve().parent.parent / "assets" / "isaac_save_names.json"
DEFAULT_ACHIEVEMENTS = Path(__file__).resolve().parent.parent / "assets" / "isaac_achievements.json"

TRACK_LABELS = {
    "achievements": "成就",
    "collectibles": "道具",
    "bosses": "BOSS 记录",
    "minibosses": "小 BOSS 记录",
    "challenges": "挑战",
    "special_seeds": "特殊种子",
}


def load(names_path: Path, achievement_path: Path) -> NameTables:
    return NameTables.load(names_path, achievement_path=achievement_path)


def describe_index(tables: NameTables, key: str, index: int) -> str:
    """给下标配一个中文名（没有名表就只给编号）。"""

    name = str(tables.names_for(key).get(index) or "").strip()
    return f"{index} {name}" if name else str(index)


def diff_tracks(old: SaveAnalysis, new: SaveAnalysis, tables: NameTables) -> List[str]:
    lines: List[str] = []
    for key, (chunk_type, label) in TRACK_CHUNKS.items():
        old_chunk = old.chunk(chunk_type)
        new_chunk = new.chunk(chunk_type)
        old_set = set(old_chunk.unlocked) if old_chunk is not None else set()
        new_set = set(new_chunk.unlocked) if new_chunk is not None else set()
        gained = sorted(new_set - old_set)
        lost = sorted(old_set - new_set)
        if not gained and not lost:
            continue
        old_ratio = f"{old_chunk.unlocked_count}/{old_chunk.total}" if old_chunk is not None else "-"
        new_ratio = f"{new_chunk.unlocked_count}/{new_chunk.total}" if new_chunk is not None else "-"
        lines.append(f"■ {label}：{old_ratio} → {new_ratio}")
        if gained:
            lines.append("   新增：" + "、".join(describe_index(tables, key, i) for i in gained[:40]))
            if len(gained) > 40:
                lines.append(f"   （新增共 {len(gained)} 项，此处列前 40）")
        if lost:
            lines.append("   减少：" + "、".join(describe_index(tables, key, i) for i in lost[:40]))
    return lines


def diff_counters(old: SaveAnalysis, new: SaveAnalysis, *, limit: int) -> List[str]:
    from isaac_save import COUNTER_LABELS

    labels: Dict[int, str] = dict(COUNTER_LABELS)
    length = max(len(old.counters), len(new.counters))
    changes: List[Tuple[int, int, int]] = []
    for index in range(length):
        before = old.counter(index)
        after = new.counter(index)
        if before != after:
            changes.append((index, before, after))
    if not changes:
        return ["■ 全局计数器：没有变化"]
    lines = [f"■ 全局计数器：{len(changes)} 项发生变化"]
    for index, before, after in changes[: max(int(limit), 1)]:
        tag = f"  [{labels[index]}]" if index in labels else ""
        delta = after - before
        lines.append(f"   [{index:>3}]{tag} {before} → {after}（{'%+d' % delta}）")
    if len(changes) > limit:
        lines.append(f"   （共 {len(changes)} 项，此处列前 {limit} 项）")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="对比两份以撒存档的差异（只读）")
    parser.add_argument("old", type=Path, help="旧存档（例如捐币前）")
    parser.add_argument("new", type=Path, help="新存档（例如捐币后）")
    parser.add_argument("--names", type=Path, default=DEFAULT_NAMES, help="名称表路径")
    parser.add_argument("--achievements", type=Path, default=DEFAULT_ACHIEVEMENTS, help="成就详情表路径")
    parser.add_argument("--max-counters", type=int, default=60, help="最多列出多少项计数器变化")
    args = parser.parse_args()

    try:
        old = parse_save_bytes(args.old.read_bytes(), file_name=args.old.name)
        new = parse_save_bytes(args.new.read_bytes(), file_name=args.new.name)
    except SaveFormatError as exc:
        print(f"解析失败：{exc}")
        return 1

    tables = load(args.names, args.achievements)

    print(f"旧：{args.old.name}  {old.version_label}  存档位 {old.slot}  {old.file_size} 字节")
    print(f"新：{args.new.name}  {new.version_label}  存档位 {new.slot}  {new.file_size} 字节")
    if old.version_key != new.version_key:
        print("⚠️ 两份存档的版本不同，差异可能来自版本而不是游戏进度。")
    print()

    sections = diff_tracks(old, new, tables) + [""] + diff_counters(old, new, limit=args.max_counters)
    print("\n".join(sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

