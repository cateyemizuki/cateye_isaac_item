#!/usr/bin/env python
"""许可合规审计：确认插件产物里没有 GPL 等传染性许可内容，并打印来源清单。

背景：本插件以 **MIT** 发布。MIT 与 GPL-3.0 不兼容——哪怕只是把一份 GPL 数据文件
随包分发，整个发行包也会带上 GPL 义务（对插件市场与下游用户都是麻烦）。
``assets/isaac_achievements_zh.json`` 曾是唯一的 GPL-3.0 内容（整表取自
aprisyourlie/IsaacAchievementGuide），已删除并改为 ``tools/build_achievement_table.py``
从游戏本体 ``achievements.xml`` + 官方语言包生成。本脚本用来**防止它再回来**。

检查项
------
1. 产物（``assets/`` ``*.py`` ``*.md``）中不出现 GPL / 传染性许可标记（白名单：说明"已移除 GPL"这类
   历史说明文本可出现在 CHANGELOG、可行性评估与工具文档里，见 ``ALLOWED_GPL_MENTIONS``）；
2. ``LICENSE`` 是 MIT，且 ``_manifest.json`` 的 ``license`` 与之致；
3. 每个 ``assets/*.json`` 都声明了 ``meta.source``（来源可追溯）；
4. 逐文件打印「来源 → 许可类别 → 是否 MIT 兼容」清单。

用法::

    python tools/check_licenses.py            # 在插件目录内运行
    python tools/check_licenses.py --root <插件目录>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

PLUGIN_DIR = Path(__file__).resolve().parent.parent

#: 命中这些词就认为文件里出现了传染性许可内容（数据/代码），需人工确认
COPYLEFT_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"GNU General Public License", "GPL 全文"),
    (r"\bGPL-?[23](\.0)?\b", "GPL 版本标记"),
    (r"\bAGPL\b", "AGPL"),
    (r"\bLGPL\b", "LGPL"),
    (r"\bcopyleft\b", "copyleft"),
)

#: 允许出现「GPL」字样的文件（这些文件是在**说明历史**：GPL 来源已移除，
#: 或记录「只参考格式事实、未复制代码」）。产物数据文件一律不允许出现。
ALLOWED_GPL_MENTIONS = {
    "CHANGELOG.md",
    "ISSUE_SUBMISSION.md",
    "README.md",
    "存档分析-可行性评估.md",
    "tools/build_achievement_table.py",
    "tools/build_name_tables.py",
    "tools/check_licenses.py",
    "tools/archive_reader.py",
}

#: 每个数据文件的来源 → 许可类别（人工维护，改数据时同步改这里）
SOURCE_LICENSE_MAP: Tuple[Tuple[str, str, str], ...] = (
    ("isaac_items.json", "ChenDekang617/isaac-item-recommender（内容源自灰机 wiki）", "CC BY-NC-SA 系（数据，非代码）"),
    ("isaac_extra.json", "灰机 wiki 页面原文人工补录", "CC BY-NC-SA 系（数据）"),
    ("isaac_items_zh_names.json", "游戏本体语言包 repentance_zh.a", "玩家自装游戏数据（MIT 兼容）"),
    ("isaac_entities_zh.json", "游戏本体语言包 repentance_zh.a", "玩家自装游戏数据（MIT 兼容）"),
    ("isaac_minibosses_zh.json", "游戏本体语言包 repentance_zh.a", "玩家自装游戏数据（MIT 兼容）"),
    ("isaac_players_zh.json", "游戏本体语言包 repentance_zh.a", "玩家自装游戏数据（MIT 兼容）"),
    ("isaac_stages_zh.json", "游戏本体语言包 repentance_zh.a", "玩家自装游戏数据（MIT 兼容）"),
    ("isaac_curses_zh.json", "游戏本体语言包 repentance_zh.a", "玩家自装游戏数据（MIT 兼容）"),
    ("isaac_achievements.json", "游戏本体 achievements.xml + 官方语言包", "玩家自装游戏数据（MIT 兼容）"),
    ("isaac_boss_slots.json", "游戏本体 entities2.xml（bossID）+ 官方语言包", "玩家自装游戏数据（MIT 兼容）"),
    ("isaac_challenges_zh.json", "灰机 wiki「挑战」页面", "CC BY-NC-SA 系（数据）"),
    ("isaac_save_names.json", "汇总表：成就 / BOSS / 小 BOSS 来自游戏本体，挑战 / 道具来自 wiki", "混合（代码仍 MIT）"),
    ("fonts/NotoSansSC-Regular.ttf", "Noto Sans SC 子集", "SIL OFL 1.1（MIT 兼容）"),
    ("fonts/NotoSansSC-Bold.ttf", "Noto Sans SC 子集", "SIL OFL 1.1（MIT 兼容）"),
    ("fonts/OFL.txt", "Noto 字体许可全文", "SIL OFL 1.1"),
)

#: 明确禁止再次出现的文件（历史 GPL 数据）
FORBIDDEN_FILES = ("isaac_achievements_zh.json",)


def scan_text_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() in {".py", ".md", ".json", ".txt", ".toml"}:
            files.append(path)
    return files


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="许可合规审计（GPL 传染性检查）")
    parser.add_argument("--root", type=Path, default=PLUGIN_DIR, help="插件根目录")
    args = parser.parse_args(argv)
    root: Path = args.root

    problems: List[str] = []
    print("=== 1. 传染性许可标记扫描 ===")
    for path in scan_text_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label in COPYLEFT_PATTERNS:
            for match in re.finditer(pattern, text):
                line = text.count("\n", 0, match.start()) + 1
                allowed = rel in ALLOWED_GPL_MENTIONS
                mark = "·" if allowed else "✗"
                if not allowed:
                    problems.append(f"{rel}:{line} 出现 {label}（{match.group(0)}）")
                print(f"  {mark} {rel}:{line} {label}：{match.group(0)}" + ("（已说明历史，允许）" if allowed else ""))

    print("\n=== 2. 许可证一致性 ===")
    license_text = (root / "LICENSE").read_text(encoding="utf-8", errors="replace") if (root / "LICENSE").is_file() else ""
    is_mit = license_text.strip().startswith("MIT License")
    print(f"  {'✓' if is_mit else '✗'} LICENSE：{'MIT' if is_mit else '不是 MIT'}")
    if not is_mit:
        problems.append("LICENSE 不是 MIT")

    manifest_path = root / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    manifest_license = str(manifest.get("license") or "")
    print(f"  {'✓' if manifest_license == 'MIT' else '✗'} manifest.license：{manifest_license or '（缺失）'}")
    if manifest_license != "MIT":
        problems.append(f"manifest.license 应为 MIT，实际 {manifest_license!r}")

    print("\n=== 3. 禁用文件 ===")
    for name in FORBIDDEN_FILES:
        exists = (root / "assets" / name).is_file()
        print(f"  {'✗' if exists else '✓'} assets/{name}：{'仍存在（GPL-3.0，必须删除）' if exists else '已移除'}")
        if exists:
            problems.append(f"assets/{name} 仍存在（GPL-3.0 内容）")

    print("\n=== 4. 数据文件来源清单 ===")
    declared = {name: (source, license_class) for name, source, license_class in SOURCE_LICENSE_MAP}
    assets = sorted((root / "assets").rglob("*"))
    for path in assets:
        if not path.is_file():
            continue
        rel = path.relative_to(root / "assets").as_posix()
        source, license_class = declared.get(rel, ("（未登记）", "（未登记）"))
        flag = "✗" if source == "（未登记）" else "✓"
        if source == "（未登记）":
            problems.append(f"assets/{rel} 未在 SOURCE_LICENSE_MAP 登记来源")
        print(f"  {flag} {rel:<34} {source} → {license_class}")

    print("\n=== 5. 数据文件必须声明 meta.source ===")
    for path in sorted((root / "assets").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name} JSON 非法：{exc}")
            print(f"  ✗ {path.name}: JSON 非法")
            continue
        meta = payload.get("meta") if isinstance(payload, dict) else None
        has_source = isinstance(meta, dict) and bool(str(meta.get("source") or "").strip())
        print(f"  {'✓' if has_source else '✗'} {path.name}: meta.source {'已声明' if has_source else '缺失'}")
        if not has_source:
            problems.append(f"{path.name} 缺少 meta.source")

    print("\n=== 结论 ===")
    if problems:
        print(f"❌ 发现 {len(problems)} 个问题：")
        for item in problems:
            print(f"   - {item}")
        return 1
    print("✅ 未发现传染性许可内容；LICENSE 与 manifest 均为 MIT；所有数据文件来源已登记。")
    print("   说明：灰机 wiki 数据（CC BY-NC-SA 系）是**数据**，不影响代码的 MIT 许可，")
    print("   但商用前需自行确认授权——README「许可说明」已如实标注。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
