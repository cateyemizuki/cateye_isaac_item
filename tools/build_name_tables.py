#!/usr/bin/env python
"""生成成就 / 挑战的中文名称表。

与 ``extract_game_strings.py`` 不同：成就与挑战的名称**不在游戏的语言包里**
（游戏把它们放在 resources 的 achievements.xml / challenges.xml，未做本地化），
所以这两张表只能取自第三方整理，来源与许可见每个文件内的 ``meta`` 字段。

用法::

    python tools/build_name_tables.py --src <第三方数据目录> --out assets

``--src`` 目录需包含:
    achievements-reference-zh.js   1–637 号成就（中文）
    achievements-zh.js             638–641 号成就补录

挑战表直接内置在 ``_CHALLENGES``，取自灰机 wiki 的「挑战」页面。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

# 灰机 wiki「挑战」页面（https://isaac.huijiwiki.com/wiki/挑战）的 45 个挑战名称。
# 第 45 号在游戏里本就没有名称，wiki 亦如此标注。
_CHALLENGES: Dict[str, str] = {
    "1": "漆黑一片", "2": "格调高雅", "3": "头部创伤", "4": "黑暗降临", "5": "坦克",
    "6": "太阳系", "7": "自杀之王", "8": "好奇害死猫", "9": "拆迁办", "10": "诅咒！",
    "11": "玻璃大炮", "12": "当生活充满酸意", "13": "豆子！", "14": "尽在卡牌中", "15": "慢吞吞",
    "16": "技术宅", "17": "吐豆人", "18": "宿主", "19": "顾家男人", "20": "返璞归真",
    "21": "超超超超超大层", "22": "快马加鞭！", "23": "蓝色炸弹人", "24": "充钱游戏", "25": "没心没肺",
    "26": "以撒传说！", "27": "脑子！", "28": "彩虹日！", "29": "俄南连击", "30": "守护者",
    "31": "本末倒置", "32": "愚人节", "33": "宝可萌", "34": "终极困难", "35": "乒乓",
    "36": "掏粪男孩", "37": "血腥玛丽", "38": "圣火洗礼", "39": "以撒织梦岛", "40": "重影幻视",
    "41": "异食游戏", "42": "烫手山芋", "43": "大量过牌！", "44": "赤键救赎", "45": "",
}

_ACHIEVEMENT_SOURCE = "https://github.com/aprisyourlie/IsaacAchievementGuide"
_ACHIEVEMENT_LICENSE = "GPL-3.0（该仓库为与 Zamiell/isaac-save-viewer 保持一致而采用 GPL-3.0）"
_CHALLENGE_SOURCE = "https://isaac.huijiwiki.com/wiki/%E6%8C%91%E6%88%98"
_CHALLENGE_LICENSE = "灰机 wiki 内容通常为 CC BY-NC-SA 系许可，商用前需自行确认"


def _extract_object(text: str, marker: str) -> Dict[str, Any]:
    """从 JS 文本里取出 marker 之后的那个对象字面量并解析为 dict。"""
    idx = text.find(marker)
    if idx < 0:
        raise ValueError(f"未找到标记 {marker!r}")
    start = text.find("{", idx)
    if start < 0:
        raise ValueError(f"{marker!r} 后没有对象字面量")

    depth = 0
    quote: str = ""          # 当前所处的字符串定界符（'' 表示不在字符串里）
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = text[start:i + 1]
                break
    else:
        raise ValueError("对象字面量未闭合")

    return json.loads(_js_object_to_json(raw))


def _js_object_to_json(text: str) -> str:
    """把 JS 对象字面量转成合法 JSON。

    第三方文件混用多种写法，不能直接做字符替换：
      * ``achievements-reference-zh.js`` 用双引号，但把撇号写成 ``\\'``（JS 合法、JSON 非法）；
      * ``achievements-zh.js`` 用单引号，键名不加引号（``638:``、``title:``）。

    做法：先把所有字符串抽出来换成占位符，在剩下的「结构部分」补键名引号，
    最后再把字符串填回去——这样字符串内容不会被误改。
    """
    strings: List[str] = []

    def _keep(s: str) -> str:
        strings.append(s)
        return f"\x00{len(strings) - 1}\x00"

    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if ch in ("'", '"'):              # 字符串：解析出内容后存起来
            delim = ch
            i += 1
            buf: List[str] = []
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt == delim:
                        buf.append(delim)
                    elif nxt == "\\":
                        buf.append("\\")
                    elif nxt == "n":
                        buf.append("\n")
                    elif nxt == '"':
                        buf.append('"')
                    else:
                        buf.append(nxt)
                    i += 2
                    continue
                if c == delim:
                    i += 1
                    break
                buf.append(c)
                i += 1
            out.append(_keep(json.dumps("".join(buf), ensure_ascii=False)))
            continue

        out.append(ch)
        i += 1

    structural = "".join(out)
    # 去掉尾随逗号（JS 允许、JSON 不允许）；此时字符串已变成占位符，不会误伤
    structural = re.sub(r",(\s*[}\]])", r"\1", structural)
    # 给裸键名补引号：数字键、标识符键（此时字符串已全部变成占位符）
    structural = re.sub(
        r'([{,]\s*)(\d+|[A-Za-z_$][A-Za-z0-9_$]*)(\s*:)', r'\1"\2"\3', structural
    )
    # 占位符还原（占位符形如 \x00N\x00，补引号时可能被当成键名，这里一并还原）
    return re.sub(
        r'"\x00(\d+)\x00"|"\x00(\d+)\x00|\x00(\d+)\x00',
        lambda m: strings[int(next(g for g in m.groups() if g is not None))],
        structural,
    )


def _load_achievements(src: Path) -> Dict[str, Any]:
    ref = src / "achievements-reference-zh.js"
    extra_file = src / "achievements-zh.js"
    if not ref.exists():
        raise FileNotFoundError(f"缺少 {ref}")

    main = _extract_object(ref.read_text(encoding="utf-8"), "ISAAC_REFERENCE_ZH")
    extra: Dict[str, Any] = {}
    if extra_file.exists():
        extra = _extract_object(extra_file.read_text(encoding="utf-8"), "const extra")

    merged: Dict[str, Any] = {}
    for aid, row in {**main, **extra}.items():
        title = (row.get("title") or "").strip()
        if not title:
            continue
        merged[str(aid)] = {
            "name": title,
            "en": (row.get("en") or "").strip(),
            "cond": (row.get("cond") or "").strip(),
            "reward": (row.get("reward") or "").strip(),
            "type": (row.get("type") or "").strip(),
        }
    return merged


def _write(path: Path, meta: Dict[str, Any], entries: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": {**meta, "count": len(entries)}, "entries": entries}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写入 {path}（{len(entries)} 条）")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成成就 / 挑战中文名称表")
    parser.add_argument("--src", required=True, help="含第三方 js 数据文件的目录")
    parser.add_argument("--out", default="assets", help="输出目录")
    args = parser.parse_args()

    out = Path(args.out)

    achievements = _load_achievements(Path(args.src))
    _write(
        out / "isaac_achievements_zh.json",
        {
            "source": _ACHIEVEMENT_SOURCE,
            "license": _ACHIEVEMENT_LICENSE,
            "note": "成就名与解锁条件为第三方中文整理，非游戏本体自带；游戏语言包内不含成就文本。",
            "ids": "1–637 为忏悔成就，638–641 为忏悔+新增",
        },
        achievements,
    )

    _write(
        out / "isaac_challenges_zh.json",
        {
            "source": _CHALLENGE_SOURCE,
            "license": _CHALLENGE_LICENSE,
            "note": "第 45 号挑战在游戏内即无名称（Steam 成就名为 DELETE THIS），此处留空。",
        },
        {k: {"name": v} for k, v in _CHALLENGES.items()},
    )

    _fill_save_names(out, achievements)
    return 0


def _fill_save_names(out: Path, achievements: Dict[str, Any]) -> None:
    """把已确认的名称合并进 ``isaac_save_names.json``（插件读取的编号→名称表）。

    该文件同时可能被手工编辑，所以**保留原有 meta 与未知分组**，只覆盖有数据的部分。
    ``bosses`` 留空：存档里 104 个 BOSS 槽位用的是游戏内部的 BOSS id 顺序，
    该顺序没有可靠的公开对照表，故不猜测；``minibosses`` 段固定 7 项 = 七宗罪，
    名称取自游戏语言包的 Minibosses 分类（顺序即该分类顺序，第 0 位为懒惰）。
    """
    target = out / "isaac_save_names.json"
    existing: Dict[str, Any] = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"警告：{target} 不是合法 JSON，将重建", file=sys.stderr)

    payload: Dict[str, Any] = {
        "meta": existing.get("meta") or {
            "note": "成就 / BOSS / 小 BOSS / 挑战的「编号 → 中文名」对照表。",
        },
        "achievements": {k: v["name"] for k, v in achievements.items()},
        "bosses": existing.get("bosses") or {},
        "minibosses": existing.get("minibosses") or _load_minibosses(out),
        "challenges": {k: v for k, v in _CHALLENGES.items() if v},
    }
    meta = payload["meta"]
    meta["source"] = (
        "成就 / 挑战取自第三方中文整理（见 isaac_achievements_zh.json、isaac_challenges_zh.json 的 meta）；"
        "小 BOSS 与 BOSS 名称取自游戏本体语言包（repentance_zh.a → isaac_minibosses_zh.json、"
        "isaac_entities_zh.json），非第三方整理；BOSS 段 104 个槽位的下标取自游戏本体 entities2.xml 的 "
        "bossID 属性，由 tools/build_boss_table.py 生成（本脚本只保留其 bosses 段，不重建）"
    )
    meta.setdefault("reviewed", False)

    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"已合并 {target}（成就 {len(payload['achievements'])}、"
        f"挑战 {len(payload['challenges'])}、"
        f"BOSS {len(payload['bosses'])}、小BOSS {len(payload['minibosses'])}）"
    )


def _load_minibosses(out: Path) -> Dict[str, str]:
    """从语言包提取的小 BOSS 表里取七宗罪（前 7 条 ``*_NAME``，排除超级 / 究极形态）。

    存档的小 BOSS 段固定 7 项，按下标 0~6 对应七宗罪（第 0 位即懒惰）。
    """

    source = out / "isaac_minibosses_zh.json"
    if not source.exists():
        return {}
    try:
        entries = json.loads(source.read_text(encoding="utf-8")).get("entries") or {}
    except (OSError, json.JSONDecodeError):
        return {}
    sins: Dict[str, str] = {}
    for key, value in entries.items():
        if not key.endswith("_NAME") or key.startswith(("SUPER_", "ULTRA_")):
            continue
        name = str(value or "").strip()
        if not name:
            continue
        sins[str(len(sins))] = name
        if len(sins) == 7:
            break
    return sins


if __name__ == "__main__":
    raise SystemExit(main())
