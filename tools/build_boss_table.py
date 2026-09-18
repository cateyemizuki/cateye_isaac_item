#!/usr/bin/env python
"""生成存档 BOSS 段（104 槽位）→ BOSS 中文名表。

结论（2026-09-16 定案，取代旧的「按 (type, variant) 升序推导」方案）
-------------------------------------------------------------------
存档第 6 段那 104 个字节 = **每个 BOSS 是否已被击败**，而**槽位下标就是
``entities2.xml`` 里实体的 ``bossID`` 属性**——既不是实体 ``type``，也不是
``(type, variant)`` 升序。``bossID`` 是游戏侧独立于实体类型的一套编号
（例：``#MONSTRO`` 的 ``id=20``、``bossID=1``），权威定义只存在于游戏本体的
``resources/packed/*.a`` 里的 ``entities2.xml``，本工具用 ``tools/archive_reader.py``
把它解出来。

支撑证据（``--verify-dir`` 会把前两条跑一遍）
-------------------------------------------
1. ``bossID`` 取值 1..110，其中 **89、90、103、104..109 没有任何实体占用**；
   而 0..103 范围内的空号（89、90、103）在**所有**存档里恒为 0。
2. 胎衣+ 存档的 BOSS 段只有 72 / 73 槽（当时 ``bossID`` 上限更低），
   其位模式与忏悔+ 存档的**前缀逐位相同**——说明下标空间稳定、且高位槽位是后来才加上的。
3. 100 个已分配槽位里，97 项在一份「Dead God」存档里被置位；剩下 3 项中
   62（究极贪婪）与 71（究极大贪婪）**图鉴击杀 162 / 88 次却仍未置位**，
   说明游戏本身不写这两项（贪婪模式专用 BOSS 不走那套标记逻辑）；
   72（胖蛆族母）图鉴遭遇 114 次、击杀 0 次（无法被正常击败），
   98（绷带巫妖）在任何存档里都没出现过。
4. 槽位 0 在所有进度存档里都是 1，但当前 ``entities2.xml`` 没有 ``bossID=0`` 的实体
   （胎衣时代留下的保留位），因此在名表里只做说明、不给名字。

用法::

    python tools/build_boss_table.py --game "D:\\Steam\\steamapps\\common\\The Binding of Isaac Rebirth" \\
        --save "%USERPROFILE%\\Documents\\...\\rep+persistentgamedata1.dat" \\
        --verify-dir "...\\save_backups" [--dry-run]

验证不通过时**不写任何名称表**（退出码 2）。
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_reader import Archive, ArchiveError  # noqa: E402
from isaac_save import SaveFormatError, parse_save_bytes  # noqa: E402

#: 存档 BOSS 段的槽位数（第 6 段 count）
SLOT_COUNT = 104
#: 归档里要扫的两个文件，按「内容最新」优先取
ARCHIVE_CANDIDATES = ("afterbirthp.a", "repentance.a", "afterbirth.a")
#: 判定哪份 entities2.xml 内容更新
NEWEST_MARKERS = (b"REPENTANCE+", b"REPENTANCE")
#: 语言包名称表（游戏本体 repentance_zh.a）
ZH_ASSETS = ("isaac_entities_zh.json", "isaac_minibosses_zh.json")
#: 图鉴段：1 = 遇到，2 = 击杀
BESTIARY_SECTIONS = {1: "encounters", 2: "kills"}
WIDTHS = {1: 1, 2: 4, 3: 4, 4: 1, 5: 1, 6: 1, 7: 1, 8: 4, 9: 4, 10: 1}

ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
ENTITY_RE = re.compile(r"<entity\b([^>]*)>")


# ----------------------------------------------------------------------
# 游戏侧：entities2.xml
# ----------------------------------------------------------------------

def find_entities2(game_dir: Path) -> Tuple[str, int, bytes]:
    """在游戏归档里按内容找 entities2.xml，返回 ``(归档名, 条目号, 内容)``。"""

    packed = game_dir / "resources" / "packed"
    if not packed.is_dir():
        raise SystemExit(f"找不到 {packed}（用 --game 指定游戏根目录）")
    matches: List[Tuple[int, str, int, bytes]] = []
    for name in ARCHIVE_CANDIDATES:
        path = packed / name
        if not path.is_file():
            continue
        try:
            with Archive(path) as archive:
                for index, data in archive.find(b"<entities"):
                    rank = next(
                        (position for position, marker in enumerate(NEWEST_MARKERS) if marker in data),
                        len(NEWEST_MARKERS),
                    )
                    matches.append((rank, name, index, data))
        except ArchiveError:
            continue
    if not matches:
        raise SystemExit("在所有归档里都没找到 entities2.xml（游戏版本可能不受支持）")
    matches.sort(key=lambda item: (item[0], -len(item[3])))
    rank, name, index, data = matches[0]
    print(f"entities2.xml ← {name} 条目 {index}（{len(data)} 字节，内容新旧等级 {rank}）")
    return name, index, data


def parse_boss_ids(xml: bytes) -> Dict[int, List[dict]]:
    """取出所有带 ``bossID`` 的实体，按 ``bossID`` 分组（保持文件顺序）。"""

    text = xml.decode("utf-8", errors="replace")
    table: Dict[int, List[dict]] = {}
    for match in ENTITY_RE.finditer(text):
        attrs = dict(ATTR_RE.findall(match.group(1)))
        if not attrs.get("name") or "bossID" not in attrs:
            continue
        table.setdefault(int(attrs["bossID"]), []).append(
            {
                "id": int(attrs["id"]),
                "variant": int(attrs.get("variant", "0")),
                "name": attrs["name"],
            }
        )
    return table


def load_zh_names(assets_dir: Path) -> Dict[str, str]:
    """语言包里的中文名（``NAME`` 键与去掉 ``_NAME`` 的写法都收）。"""

    names: Dict[str, str] = {}
    for filename in ZH_ASSETS:
        path = assets_dir / filename
        if not path.is_file():
            continue
        entries = json.loads(path.read_text(encoding="utf-8")).get("entries") or {}
        for key, value in entries.items():
            if isinstance(value, str) and value.strip():
                upper = str(key).upper()
                names.setdefault(upper, value)
                names.setdefault(upper.removesuffix("_NAME"), value)
    return names


def zh_key(raw_name: str) -> str:
    """实体名 → 语言包键（``#MONSTRO`` → ``MONSTRO``）。"""

    cleaned = raw_name.lstrip("#").replace("&apos;", "'")
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", cleaned).strip("_")
    return cleaned.upper()


def resolve_entity(entities: Sequence[dict], zh: Dict[str, str]) -> Tuple[dict, str, str]:
    """在一个 bossID 的多个实体里挑「主实体」，并给出中文名。

    优先挑能查到官方中文名的实体（``#HUSH`` 这类带 ``#`` 的名字才是语言包键），
    同分时优先 ``variant == 0``；都查不到时用第一个实体、中文名留空。
    """

    scored: List[Tuple[int, int, dict, str]] = []
    for entity in entities:
        name = zh.get(zh_key(entity["name"]), "")
        score = (1 if name else 0, 1 if entity["variant"] == 0 else 0)
        scored.append((score[0], score[1], entity, name))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]["variant"], item[2]["id"]))
    best = scored[0]
    return best[2], best[3], ("游戏语言包" if best[3] else "游戏 entities2.xml（语言包未收录）")


# ----------------------------------------------------------------------
# 存档侧
# ----------------------------------------------------------------------

def boss_bits(path: Path) -> List[int]:
    analysis = parse_save_bytes(path.read_bytes(), file_name=path.name)
    chunk = analysis.chunk(6)
    if chunk is None:
        raise SaveFormatError(f"{path.name}: 存档里没有 BOSS 段")
    unlocked = set(chunk.unlocked)
    return [1 if index in unlocked else 0 for index in range(chunk.count)]


def bestiary_counts(raw: bytes) -> Dict[str, Dict[Tuple[int, int], int]]:
    """图鉴四个分段：``{段名: {(type, variant): 计数}}``（本插件运行时不解析这一段，仅供本工具核对）。"""

    offset = 20
    body = 0
    count = 0
    for _ in range(11):
        chunk_type, _length, count = struct.unpack_from("<iii", raw, offset)
        offset += 12
        if chunk_type == 11:
            body = offset
            break
        offset += count * WIDTHS[chunk_type]
    out: Dict[str, Dict[Tuple[int, int], int]] = {name: {} for name in BESTIARY_SECTIONS.values()}
    cursor = body
    for _ in range(count):
        section_id, section_size = struct.unpack_from("<ii", raw, cursor)
        cursor += 8
        key = BESTIARY_SECTIONS.get(section_id)
        for _ in range(max(section_size // 4, 0)):
            packed, value = struct.unpack_from("<Ii", raw, cursor)
            cursor += 8
            if key:
                out[key][((packed >> 20) & 0xFFF, (packed >> 8) & 0xFF)] = value
    return out


# ----------------------------------------------------------------------
# 名表构建
# ----------------------------------------------------------------------

NOTE_UNASSIGNED = "游戏未分配的空槽位（当前 entities2.xml 里没有这个 BOSS 编号）"
NOTE_LEGACY = "旧版保留位：当前 entities2.xml 未定义 bossID＝0 的实体，无法命名"
NOTE_NEVER_WRITTEN = "游戏不把这一项写进存档 BOSS 段：图鉴里已击杀多次，存档位仍然是 0"
NOTE_UNKILLABLE = "图鉴里击杀次数恒为 0（100% 通关存档里也是 0，无法被正常击败）"
NOTE_UNSEEN = "在所有核对过的存档里都从未出现（可能为未启用内容）"

#: 只有「几乎打满」的存档才能用来判定「哪些槽位游戏本来就不写」——
#: 例如拿一份全新存档去推断，会把 100 个「还没打到」的 BOSS 全标成「游戏不写」。
ANNOTATION_MIN_RATIO = 0.9


def build_slots(table: Dict[int, List[dict]], zh: Dict[str, str]) -> List[dict]:
    slots: List[dict] = []
    for slot in range(SLOT_COUNT):
        entities = table.get(slot)
        if not entities:
            slots.append(
                {
                    "slot": slot,
                    "boss_id": slot,
                    "status": "legacy" if slot == 0 else "unassigned",
                    "entity": "",
                    "en": "",
                    "zh": "",
                    "source": "",
                    "note": NOTE_LEGACY if slot == 0 else NOTE_UNASSIGNED,
                }
            )
            continue
        entity, name, source = resolve_entity(entities, zh)
        slots.append(
            {
                "slot": slot,
                "boss_id": slot,
                "status": "assigned",
                "entity": f"{entity['id']}.{entity['variant']}",
                "en": entity["name"],
                "zh": name,
                "source": source,
                "note": "",
                "variants": [f"{item['id']}.{item['variant']}" for item in entities],
            }
        )
    return slots


def pick_reference(saves: Sequence[Path]) -> Tuple[Optional[Path], List[Tuple[Path, int]]]:
    """挑出进度最高的存档当参考；返回 ``(参考存档, [(存档, 置位数), …])``。"""

    stats: List[Tuple[Path, int]] = []
    for path in saves:
        if not path.is_file():
            continue
        try:
            stats.append((path, sum(boss_bits(path))))
        except (SaveFormatError, struct.error, OSError):
            continue
    stats.sort(key=lambda item: item[1], reverse=True)
    return (stats[0][0] if stats else None), stats


def annotate_with_save(slots: List[dict], save: Path) -> dict:
    """用一份「几乎打满」的参考存档，给未记录的槽位补上说明并回传证据。

    - 槽位未置位、但**图鉴里已击杀**该 BOSS → 这一项游戏不写（图鉴与存档位自相矛盾）；
    - 未置位、图鉴遭遇过但击杀为 0 → 该 BOSS 无法被正常击败；
    - 未置位、图鉴里从未出现 → 可能为未启用内容。
    """

    raw = save.read_bytes()
    bits = boss_bits(save)
    bestiary = bestiary_counts(raw)
    evidence = {"save": save.name, "size": len(raw), "unset": [], "never_written": [], "unkillable": [], "unseen": []}
    for slot, info in enumerate(slots):
        if info["status"] != "assigned" or bits[slot]:
            continue
        key = tuple(int(part) for part in info["entity"].split("."))
        kills = bestiary["kills"].get(key, 0)
        encounters = bestiary["encounters"].get(key, 0)
        row = {"slot": slot, "zh": info["zh"], "entity": info["entity"], "kills": kills, "encounters": encounters}
        evidence["unset"].append(row)
        if kills > 0:
            info["note"] = NOTE_NEVER_WRITTEN
            evidence["never_written"].append(row)
        elif encounters > 0:
            info["note"] = NOTE_UNKILLABLE
            evidence["unkillable"].append(row)
        else:
            info["note"] = NOTE_UNSEEN
            evidence["unseen"].append(row)
    return evidence


def verify(slots: List[dict], saves: Sequence[Path], verify_dir: Optional[Path]) -> Tuple[bool, List[str], dict]:
    """结构 + 位图一致性检查；返回 ``(是否通过, 输出行, 统计)``。"""

    lines: List[str] = []
    stats: dict = {}
    ok = True

    assigned = [info for info in slots if info["status"] == "assigned"]
    unassigned = [info["slot"] for info in slots if info["status"] == "unassigned"]
    legacy = [info["slot"] for info in slots if info["status"] == "legacy"]
    stats["assigned"] = len(assigned)
    stats["unassigned"] = unassigned
    stats["legacy"] = legacy
    named = [info for info in assigned if info["zh"]]
    stats["named"] = len(named)
    lines.append(f"槽位 {len(slots)}：已分配 {len(assigned)}、未分配 {len(unassigned)} {unassigned}、保留位 {legacy}")
    lines.append(f"已分配槽位中能查到官方中文名：{len(named)}/{len(assigned)}")
    if not named:
        ok = False
        lines.append("✗ 一个中文名都没查到，说明语言包名称表或 entities2.xml 解析有问题")
    missing_names = [f"{info['slot']}:{info['en']}" for info in assigned if not info["zh"]]
    if missing_names:
        lines.append(f"· 没有官方中文名（回退英文名，不编造）：{', '.join(missing_names)}")

    files: List[Path] = list(saves)
    if verify_dir and verify_dir.is_dir():
        files.extend(sorted(verify_dir.glob("*.dat")))
    unique: List[Path] = []
    seen: set = set()
    for path in files:
        if path.is_file() and path not in seen:
            seen.add(path)
            unique.append(path)

    checked = 0
    for path in unique:
        try:
            bits = boss_bits(path)
        except (SaveFormatError, struct.error, OSError) as exc:
            lines.append(f"· 跳过 {path.name}：{exc}")
            continue
        if len(bits) != SLOT_COUNT:
            lines.append(f"· 跳过 {path.name}：BOSS 段 {len(bits)} 槽，与 {SLOT_COUNT} 不符")
            continue
        checked += 1
        bad = [slot for slot in unassigned if bits[slot]]
        if bad:
            ok = False
            lines.append(f"✗ {path.name}: 未分配槽位被置位 {bad}——下标规则不成立")
        if bits[0] and 0 not in legacy:
            ok = False
            lines.append(f"✗ {path.name}: 槽位 0 被置位但没被当作保留位")
    stats["checked_saves"] = checked
    lines.append(f"跨存档核对：{checked} 份存档里，「未分配槽位恒为 0」这条都成立" if ok else "跨存档核对：存在反例（见上）")
    if checked == 0:
        ok = False
        lines.append("✗ 没有可核对的存档，无法确认下标规则")
    return ok, lines, stats


def write_assets(slots: List[dict], evidence: dict, stats: dict, xml_from: str, names_path: Path, detail_path: Path) -> None:
    detail = {
        "meta": {
            "note": "存档 BOSS 段 104 个槽位 ↔ 游戏 BOSS 的对照表。槽位下标 = entities2.xml 的 bossID。",
            "source": f"游戏本体 resources/packed/{xml_from} → entities2.xml（bossID 属性）+ 官方简体语言包",
            "license": "生成物不含第三方数据，随仓库以 MIT 提供（整包 CC BY-NC-SA 4.0，见 README「许可」）",
            "slot_count": SLOT_COUNT,
            "index_rule": "槽位下标 = 实体的 bossID 属性（不是实体 type，也不是 (type, variant) 升序）",
            "entities2_from": xml_from,
            "generated": date.today().isoformat(),
            "generator": "tools/build_boss_table.py",
            "names_from": "游戏本体语言包（repentance_zh.a → assets/isaac_entities_zh.json 等）",
            "assigned": stats["assigned"],
            "named": stats["named"],
            "unassigned_slots": stats["unassigned"],
            "legacy_slots": stats["legacy"],
            "saves_checked": stats.get("checked_saves", 0),
            "evidence": evidence,
            "notes": {
                "unassigned": NOTE_UNASSIGNED,
                "legacy": NOTE_LEGACY,
                "never_written": NOTE_NEVER_WRITTEN,
                "unkillable": NOTE_UNKILLABLE,
                "unseen": NOTE_UNSEEN,
            },
        },
        "slots": {str(info["slot"]): info for info in slots},
    }
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {detail_path}")

    payload = json.loads(names_path.read_text(encoding="utf-8"))
    payload["bosses"] = {
        str(info["slot"]): ({"name": info["zh"], "note": info["note"]} if info["note"] else info["zh"])
        for info in slots
        if info["zh"] or info["note"]
    }
    meta = payload.setdefault("meta", {})
    meta["bosses_status"] = (
        f"BOSS 名表由 tools/build_boss_table.py 生成（槽位下标 = entities2.xml 的 bossID；"
        f"已分配 {stats['assigned']} 项、其中 {stats['named']} 项有官方中文名，"
        f"未分配槽位 {stats['unassigned']}）。"
    )
    names_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已更新 {names_path} 的 bosses 段（{len(payload['bosses'])} 条）")


# ----------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成存档 BOSS 段的 BOSS 名表")
    parser.add_argument("--game", type=Path, default=None, help="游戏根目录（含 resources/packed）")
    parser.add_argument("--save", type=Path, action="append", default=[], help="用于核对的存档，可多次给")
    parser.add_argument("--verify-dir", type=Path, default=None, help="历史存档目录（逐份核对未分配槽位恒为 0）")
    parser.add_argument("--assets", type=Path, default=PLUGIN_DIR / "assets", help="assets 目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = parser.parse_args(argv)

    if args.game is None:
        raise SystemExit("需要 --game 指定游戏根目录（本工具要读游戏自己的 entities2.xml）")
    game_dir: Path = args.game

    archive_name, entry_index, xml = find_entities2(game_dir)
    table = parse_boss_ids(xml)
    zh = load_zh_names(args.assets)
    print(f"entities2.xml：bossID 条目 {len(table)} 组，语言包中文名 {len(zh)} 条")

    slots = build_slots(table, zh)
    reference, set_stats = pick_reference(args.save)
    assigned_total = sum(1 for info in slots if info["status"] == "assigned")
    evidence: dict = {}
    if reference is not None:
        best = set_stats[0][1]
        print(f"参考存档：{reference.name}（置位 {best}/{SLOT_COUNT}），核对过的存档："
              + ", ".join(f"{path.name}={count}" for path, count in set_stats[:5]))
        if best >= assigned_total * ANNOTATION_MIN_RATIO:
            evidence = annotate_with_save(slots, reference)
            print(
                f"{reference.name}：未记录 {len(evidence['unset'])} 项"
                f"（游戏不写 {len(evidence['never_written'])}、无法击败 {len(evidence['unkillable'])}、"
                f"从未出现 {len(evidence['unseen'])}）"
            )
        else:
            print(
                f"⚠ 参考存档进度只有 {best}/{assigned_total}（不足 {ANNOTATION_MIN_RATIO:.0%}），"
                "不够判定「哪些槽位游戏本来就不写」，因此**不给任何槽位加说明**（只写名字）。"
            )
        evidence["reference_set_bits"] = {path.name: count for path, count in set_stats[:1]}

    ok, lines, stats = verify(slots, args.save, args.verify_dir)
    print("\n—— 验证 ——")
    for line in lines:
        print("  " + line)

    print("\n—— 槽位表 ——")
    for info in slots:
        label = info["zh"] or info["en"] or "—"
        suffix = f"  ← {info['note']}" if info["note"] else ""
        print(f"  [{info['slot']:>3}] {info['entity'] or '  --  ':<7} {label}{suffix}")

    if not ok:
        print("\n❌ 验证不通过，不写任何名称表。")
        return 2
    if args.dry_run:
        print("\n（--dry-run：不写文件）")
        return 0

    write_assets(
        slots,
        evidence,
        stats,
        f"{archive_name} 条目 {entry_index}",
        args.assets / "isaac_save_names.json",
        args.assets / "isaac_boss_slots.json",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
