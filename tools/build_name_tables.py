#!/usr/bin/env python
"""生成 / 合并存档用的中文名称表（成就、挑战、小 BOSS）。

许可说明（重要）
----------------
* **成就**：来自 ``assets/isaac_achievements.json`` —— 由 ``tools/build_achievement_table.py``
  从**玩家自装游戏**的 ``achievements.xml`` 与官方简体语言包生成，**不含任何第三方数据**。
  早期版本这里读的是第三方 GPL-3.0 中文整理（aprisyourlie/IsaacAchievementGuide），
  会与插件的 MIT 许可冲突，已彻底移除。
* **挑战**：``_CHALLENGES`` 取自灰机 wiki「挑战」页面（通常 CC BY-NC-SA 系），
  与插件的道具图鉴数据同属一类，**代码仍是 MIT，数据许可见文件内 ``meta``**。
  游戏本体只有英文挑战名（``challenges.xml``），没有中文，故这里沿用 wiki 译名。
* **小 BOSS**：取游戏语言包的 Minibosses 分类（七宗罪，第 0 位即懒惰）。

用法::

    python tools/build_achievement_table.py --game <游戏目录>     # 先生成成就表
    python tools/build_name_tables.py --out assets               # 再合并名称表
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

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

_CHALLENGE_SOURCE = "https://isaac.huijiwiki.com/wiki/%E6%8C%91%E6%88%98"
_CHALLENGE_LICENSE = "灰机 wiki 内容通常为 CC BY-NC-SA 系许可，商用前需自行确认"

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _load_achievements(out: Path) -> Dict[str, Any]:
    """读入由游戏本体生成的成就表（``tools/build_achievement_table.py`` 的产物）。"""

    source = out / "isaac_achievements.json"
    if not source.is_file():
        raise FileNotFoundError(
            f"缺少 {source}：请先运行 "
            f"`python tools/build_achievement_table.py --game <游戏目录>` 生成（只读游戏本体，无需联网）"
        )
    payload = json.loads(source.read_text(encoding="utf-8"))
    entries = payload.get("entries") or {}
    merged: Dict[str, Any] = {}
    for key, row in entries.items():
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        merged[str(key)] = {
            "name": name,
            "en": str(row.get("en") or "").strip(),
            "cond": str(row.get("cond") or "").strip(),
            "reward": str(row.get("reward") or "").strip(),
            "type": "",
        }
    return merged


def _fill_save_names(out: Path, achievements: Dict[str, Any]) -> None:
    """把已确认的名称合并进 ``isaac_save_names.json``（插件读取的编号→名称表）。

    该文件同时可能被手工编辑，所以**保留原有 meta 与未知分组**，只覆盖有数据的部分。
    ``bosses`` 段由 ``tools/build_boss_table.py`` 生成，这里只保留、不重建。
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
            "note": "成就 / 道具 / BOSS / 小 BOSS / 挑战的「编号 → 中文名」对照表。",
        },
        "achievements": {k: v["name"] for k, v in achievements.items()},
        "bosses": existing.get("bosses") or {},
        "minibosses": existing.get("minibosses") or _load_minibosses(out),
        "challenges": {k: v for k, v in _CHALLENGES.items() if v},
    }
    meta = payload["meta"]
    meta["source"] = (
        "成就名称取自游戏本体 achievements.xml + 官方简体语言包（tools/build_achievement_table.py 生成，无第三方数据）；"
        "BOSS 与小 BOSS 名称取自游戏本体语言包（isaac_entities_zh.json、isaac_minibosses_zh.json），"
        "BOSS 段 104 个槽位的下标取自游戏本体 entities2.xml 的 bossID 属性（tools/build_boss_table.py）；"
        "挑战名为灰机 wiki「挑战」页面整理（通常 CC BY-NC-SA 系，与本插件道具图鉴数据同类，代码仍为 MIT）"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 / 合并存档用的中文名称表")
    parser.add_argument("--out", default=str(PLUGIN_DIR / "assets"), help="assets 目录")
    args = parser.parse_args(argv)

    out = Path(args.out)
    achievements = _load_achievements(out)
    print(f"读入游戏本体成就表 {len(achievements)} 条")

    (out / "isaac_challenges_zh.json").write_text(
        json.dumps(
            {
                "meta": {
                    "source": _CHALLENGE_SOURCE,
                    "license": _CHALLENGE_LICENSE,
                    "note": (
                        "第 45 号挑战在游戏内即无名称（Steam 成就名为 DELETE THIS），此处留空。"
                        "游戏本体 challenges.xml 只有英文名，中文名为 wiki 整理。"
                    ),
                    "count": len(_CHALLENGES),
                },
                "entries": {k: {"name": v} for k, v in _CHALLENGES.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"已写入 {out / 'isaac_challenges_zh.json'}")

    _fill_save_names(out, achievements)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
