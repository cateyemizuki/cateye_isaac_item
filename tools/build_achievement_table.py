#!/usr/bin/env python
"""从**游戏本体**生成成就表（替换掉原先那份 GPL-3.0 的第三方中文化数据）。

为什么必须换
------------
``assets/isaac_achievements_zh.json`` 原先整表取自
https://github.com/aprisyourlie/IsaacAchievementGuide（**GPL-3.0**）。
GPL-3.0 是传染性协议：把它随 MIT 插件一起分发，会让整个发行包带上 GPL 义务。
本工具改为**只用玩家自己游戏里的数据**，因此产物与本插件一致按 MIT 发布。

数据来源（全部来自玩家自装游戏）
--------------------------------
* ``resources/packed/afterbirthp.a`` 里的 ``achievements.xml``
  （忏悔+ 版本，641 条，id 1..641 与存档成就段一一对应）：每条自带
  - ``id``：成就编号（= 存档成就段下标）
  - 紧贴在条目上方 的 ``<!-- … -->`` 注释：**游戏自己写的解锁条件**（英文，284/641 条有）
  - ``text``：游戏内弹窗文案（``"X" has appeared in the basement`` / ``You unlocked "X"`` …）
  - ``gfx``：成就图标文件名（拿不到标题时的兜底名字来源）
* ``resources/packed/repentance_zh.a`` 语言包：把标题/奖励名换成**官方简体译名**（能对上就对，
  对不上留空 —— 游戏本体没有成就名的本地化，中文成就名只存在于 Steam 端，本工具不编造）。

输出 ``assets/isaac_achievements.json``：``entries`` 下每条含
``name``（中文名，查不到则用英文标题）、``en``（英文标题）、``cond``（游戏内条件原文）、
``gfx``；``meta`` 里写明来源与统计。

用法::

    python tools/build_achievement_table.py --game "D:\\Steam\\steamapps\\common\\The Binding of Isaac Rebirth"
    python tools/build_achievement_table.py --game … --report      # 只统计，不写文件
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_reader import Archive, ArchiveError  # noqa: E402
from extract_game_strings import _find_stringtable, _parse_stringtable, read_archive  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parent.parent

ARCHIVE_CANDIDATES = ("afterbirthp.a", "repentance.a")
GAME_DIRS = [
    Path(r"D:\Steam\steamapps\common\The Binding of Isaac Rebirth"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\The Binding of Isaac Rebirth"),
]
ZH_PACK = "repentance_zh.a"
OUT_NAME = "isaac_achievements.json"

ATTR_RE = re.compile(r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
ACH_TAG_RE = re.compile(r"<achievement\b([^>]*?)/?>")
COMMENT_RE = re.compile(r"<!--(.*?)-->", re.S)
ARTICLES = ("a ", "an ", "the ")

#: 弹窗文案里的「奖励/标题」一般在引号里；这几类句式直接取引号内容
QUOTED_FORMS = ("has appeared in the", "You unlocked", "achieved")

NOTE = (
    "成就编号 → 「中文名 / 英文标题 / 游戏内解锁条件」对照表，"
    "全部由玩家自装游戏的 achievements.xml 与官方简体语言包生成（无第三方数据）。"
    "游戏本体只提供英文条件，中文名仅在语言包里能查到对应译名时给出，查不到时退回英文标题、绝不编造。"
)


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------

def find_game_dir(explicit: Optional[Path]) -> Path:
    if explicit:
        return explicit
    for candidate in GAME_DIRS:
        if candidate.is_dir():
            return candidate
    raise SystemExit("找不到游戏目录，请用 --game 指定（需含 resources/packed）")


def load_english_zh_index(pack: Path) -> Dict[str, str]:
    """语言包全 14 个分类的「英文 → 官方简体」索引。

    另按**键名**补一层索引（``THE_CHARIOT_NAME`` → ``chariot``）：语言包里的英文串常带
    罗马数字等前缀（``VII - The Chariot``），只按英文串查会漏。但键名并不可靠
    （``HEART_NAME`` 的英文是道具名 ``<3``），所以**只有键名出现在英文串里**时才登记，
    否则会张冠李戴（曾把「金心」查成道具 ``<3`` 的译名）。
    """

    ftype, records, raw = read_archive(pack)
    xml = _find_stringtable(raw, ftype, records) or ""
    index: Dict[str, str] = {}
    for cat_match in re.finditer(r'<category name="([^"]+)">', xml):
        end = xml.find("</category>", cat_match.end())
        body = xml[cat_match.end() : end if end > 0 else len(xml)]
        for key_match in re.finditer(r'<key name="([^"]+)">(.*?)</key>', body, re.S):
            key = key_match.group(1)
            strings = [s.strip() for s in re.findall(r"<string>(.*?)</string>", key_match.group(2), re.S)]
            if len(strings) <= 3 or not strings[3]:
                continue
            zh = strings[3]
            english_words = set(norm(strings[0]).split())
            if strings[0]:
                index.setdefault(norm(strings[0]), zh)
            if key.upper().endswith("_NAME"):
                derived = norm(key[:-5].replace("_", " "))
                derived_words = [word for word in derived.split() if word]
                # 键名的每个词都要能在英文串里找到「同一个词」或「它的前缀」
                # （TECH_5 → tech 是 technology 的前缀 ✓；HEART → 英文是 <3 ✗ 拒绝）
                if derived_words and all(
                    any(word == candidate or (len(word) >= 4 and candidate.startswith(word)) for candidate in english_words)
                    for word in derived_words
                ):
                    index.setdefault(derived, zh)
    return index


def unescape(text: str) -> str:
    """解掉 XML 实体（``&lt;3`` → ``<3``）。"""

    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(entity, char)
    return text


def norm(text: str) -> str:
    """归一化：小写、去标点、去冠词（"A Noose" / "The Noose" → "noose"）。"""

    text = unescape(text).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    for article in ARTICLES:
        if text.startswith(article):
            return text[len(article) :]
    return text


def camel_words(name: str) -> str:
    """``Achievement_BlueCandle`` → ``Blue Candle``；顺手去掉编号前缀与尾部的去重数字。"""

    cleaned = re.sub(r"^\d+_", "", name)
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", cleaned).replace("_", " ").strip()
    words = re.sub(r"(?<=[A-Za-z])\d$", "", words)   # "2 New Pills1" -> "2 New Pills"
    return words.strip()


def load_achievement_xml(game_dir: Path) -> Tuple[str, bytes]:
    packed = game_dir / "resources" / "packed"
    runs: List[Tuple[int, str, bytes]] = []
    for name in ARCHIVE_CANDIDATES:
        path = packed / name
        if not path.is_file():
            continue
        with Archive(path) as archive:
            for index, data in archive.find(b"<achievement"):
                text = data.decode("utf-8", "replace").lstrip()
                if text.startswith("<achievement"):
                    runs.append((len(data), name, data))
    if not runs:
        raise SystemExit("在游戏归档里没找到 achievements.xml")
    runs.sort(key=lambda item: -item[0])
    size, name, data = runs[0]
    print(f"achievements.xml ← {name}（{size} 字节）")
    return name, data


def parse_entries(xml_text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    comments = [(m.start(), m.end(), m.group(1)) for m in COMMENT_RE.finditer(xml_text)]
    for match in ACH_TAG_RE.finditer(xml_text):
        attrs: Dict[str, str] = {}
        for attr in ATTR_RE.finditer(match.group(1)):
            attrs[attr.group(1)] = attr.group(2) if attr.group(2) is not None else attr.group(3)
        if "id" not in attrs:
            continue
        cond = ""
        for start, end, body in comments:
            if start < match.start() and "<achievement" not in xml_text[end : match.start()]:
                cond = " ".join(body.split())
        text = unescape(attrs.get("text", ""))
        gfx = attrs.get("gfx", "")
        gfx_name = camel_words(re.sub(r"^Achievement_", "", gfx).rsplit(".", 1)[0]) if gfx else ""
        quoted = re.findall(r'"([^"]+)"', text)
        title_en = unescape(quoted[0]).strip() if quoted else ""
        if not title_en and text:
            # 弹窗文案没有引号时：短句本身就是标题（Dead God / Item Info），长句改用图标名
            first = re.split(r"(?<=[.!?])\s+|(?<=!!!)", text.strip())[0].strip()
            title_en = first if len(first.split()) <= 4 else gfx_name
        if not title_en:
            title_en = gfx_name
        reward_en = unescape(quoted[0]).strip() if quoted else ""
        rows.append(
            {
                "id": int(attrs["id"]),
                "title_en": title_en,
                "reward_en": reward_en,
                "cond": cond,
                "text": text.strip(),
                "gfx": gfx,
            }
        )
    rows.sort(key=lambda row: row["id"])
    return rows


def resolve_zh(row: Dict[str, Any], index: Dict[str, str]) -> Tuple[str, str]:
    """返回 ``(中文名, 名字来源)``；查不到时返回空串（不编造）。

    依次尝试：解锁内容名 → 成就标题 → 标题去掉前缀修饰后的尾词（``Rune of Berkano`` → ``Berkano``）
    → 成就图标名反查。语言包里查不到就留空，显示英文标题。
    """

    candidates: List[Tuple[str, str]] = []
    if row["reward_en"]:
        candidates.append((row["reward_en"], "语言包（解锁内容译名）"))
    if row["title_en"]:
        candidates.append((row["title_en"], "语言包（成就标题译名）"))
        words = norm(row["title_en"]).split()
        for start in range(1, len(words)):
            candidates.append((" ".join(words[start:]), "语言包（标题尾词匹配）"))
    gfx_name = camel_words(re.sub(r"^Achievement_", "", row.get("gfx") or "").rsplit(".", 1)[0])
    if gfx_name:
        candidates.append((gfx_name, "语言包（按成就图标名反查）"))
    for raw, source in candidates:
        for probe in (norm(raw), norm(re.sub(r"[!?.]+$", "", raw))):
            if probe and probe in index:
                return index[probe], source
    return "", ""


# ----------------------------------------------------------------------

def build(game_dir: Path) -> Tuple[Dict[str, Any], Dict[str, int]]:
    archive_name, xml_bytes = load_achievement_xml(game_dir)
    index = load_english_zh_index(game_dir / "resources" / "packed" / ZH_PACK)
    rows = parse_entries(xml_bytes.decode("utf-8", "replace"))

    entries: Dict[str, Any] = {}
    stats = Counter()
    for row in rows:
        zh, source = resolve_zh(row, index)
        stats["total"] += 1
        stats["with_cond"] += 1 if row["cond"] else 0
        stats["with_zh"] += 1 if zh else 0
        stats["with_title"] += 1 if row["title_en"] else 0
        entries[str(row["id"])] = {
            "name": zh or row["title_en"],
            "en": row["title_en"],
            "cond": row["cond"],
            "reward": row["reward_en"],
            "gfx": row["gfx"],
            "zh_source": source,
        }

    meta = {
        "note": NOTE,
        "source": f"游戏本体 resources/packed/{archive_name} → achievements.xml（玩家自装游戏，非第三方整理）",
        "license": "随本插件按 MIT 发布（生成物不含第三方数据）",
        "names_from": f"游戏本体 resources/packed/{ZH_PACK} 官方简体语言包（能查到译名才用）",
        "count": len(entries),
        "with_cond": stats["with_cond"],
        "with_zh": stats["with_zh"],
        "generated": date.today().isoformat(),
        "generator": "tools/build_achievement_table.py",
        "caveats": [
            "游戏本体没有成就名的本地化：中文名只在语言包里能查到同名译名时给出，其余退回英文标题（不编造）。",
            "解锁条件是游戏 achievements.xml 里紧贴条目的英文注释原文（只有 284/641 条带注释）。",
            "成就 637~641 为忏悔+ 新增条目，其中部分在游戏内没有独立标题，会退回图标名或空。",
        ],
    }
    return {"meta": meta, "entries": entries}, {
        "total": stats["total"],
        "with_cond": stats["with_cond"],
        "with_zh": stats["with_zh"],
        "with_title": stats["with_title"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从游戏本体生成成就表（MIT 干净来源）")
    parser.add_argument("--game", type=Path, default=None, help="游戏根目录（含 resources/packed）")
    parser.add_argument("--out", type=Path, default=PLUGIN_DIR / "assets", help="输出目录")
    parser.add_argument("--report", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args(argv)

    game_dir = find_game_dir(args.game)
    payload, stats = build(game_dir)
    total = stats["total"]
    print(f"成就 {total} 条：中文名 {stats['with_zh']}（{stats['with_zh'] / total:.0%}）、"
          f"英文标题 {stats['with_title']}、带游戏内条件 {stats['with_cond']}")
    print("样例：")
    for key in list(payload["entries"])[:6]:
        row = payload["entries"][key]
        print(f"  [{key:>3}] 名字={row['name'] or '—':<14} en={row['en'][:26]!r:<30} 条件={row['cond'][:44]!r}")
    if args.report:
        return 0
    out = args.out / OUT_NAME
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
