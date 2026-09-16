"""生成插件内置的渲染字体（Noto Sans SC 子集，SIL OFL 1.1）。

为什么要内置字体：解析图改成本地 Pillow 绘制后，不能依赖系统是否装了中文字体
（Linux 服务器常常没有），所以随插件打包一份可再分发的开源字体，并按插件实际用到的
字符集做子集化，把体积压到可用范围。

字符集来源：
1. ``assets/*.json`` 里所有中文文本（道具名、成就名与解锁条件、挑战名、怪物名、楼层名……）；
2. GB2312 一级字库（3755 个常用简体汉字，用 ``bytes`` + ``gb2312`` 解码枚举，离线可得）；
3. ASCII 可打印字符、常用中英文标点与符号。

用法::

    python tools/build_fonts.py --source <NotoSansSC-VF.ttf> \
        --license <OFL.txt> --out assets/fonts

``--source`` 可用任意 Noto Sans SC 可变字体（官方仓库或 Google Fonts 均可）：
    https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf
    https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, Iterable, Set

PLUGIN_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = PLUGIN_DIR / "assets"

#: 需要生成的字重
WEIGHTS = {"NotoSansSC-Regular.ttf": 400, "NotoSansSC-Bold.ttf": 700}

#: 手工补充的常用字符（标点、符号、数字单位等）
EXTRA_CHARS = (
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    " !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    "，。、；：？！“”‘’（）【】《》〈〉「」『』…—～·×÷±≥≤≠→←↑↓★☆√∞"
    "％＋－／＝＃＠＆＊：；＜＞［］｛｝｜～　"
    "①②③④⑤⑥⑦⑧⑨⑩"
)


def gb2312_chars() -> Set[str]:
    """枚举 GB2312 全部汉字（一级 3755 + 二级 3008），离线可得。"""

    chars: Set[str] = set()
    for high in range(0xB0, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                chars.add(bytes([high, low]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return chars


def asset_chars() -> Set[str]:
    """收集 assets 下所有 JSON 里的字符。"""

    chars: Set[str] = set()
    for path in sorted(ASSETS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, str):
                chars.update(node)
            elif isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return chars


def level_one_chars() -> Set[str]:
    """只取 GB2312 一级（3755 常用字），够用且更省体积。"""

    chars: Set[str] = set()
    for high in range(0xB0, 0xD8):
        for low in range(0xA1, 0xFF):
            try:
                chars.add(bytes([high, low]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return chars


def build_charset(level: str) -> str:
    chars = set(EXTRA_CHARS) | asset_chars()
    if level == "full":
        chars |= gb2312_chars()
    elif level == "common":
        chars |= level_one_chars()
    chars = {c for c in chars if c.strip() or c == " "}
    return "".join(sorted(chars))


def build_one(source: Path, out: Path, weight: int, text: str) -> Path:
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    from fontTools.subset import Options, Subsetter

    font = TTFont(str(source), lazy=False)
    if "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": weight}, inplace=False, updateFontNames=True)

    options = Options()
    options.glyph_names = False
    options.notdef_outline = True
    options.recalc_bounds = True
    options.name_IDs = ["*"]
    options.drop_tables += ["DSIG"]
    options.layout_features = ["kern", "liga", "locl", "mark", "mkmk", "ccmp", "vert", "vrt2"]

    subsetter = Subsetter(options=options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    font.save(str(out))
    font.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="生成插件内置子集字体")
    parser.add_argument("--source", type=Path, required=True, help="Noto Sans SC 可变字体路径")
    parser.add_argument("--license", type=Path, default=None, help="OFL.txt 路径（会复制到输出目录）")
    parser.add_argument("--out", type=Path, default=ASSETS_DIR / "fonts", help="输出目录")
    parser.add_argument("--level", choices=("minimal", "common", "full"), default="common",
                        help="字符集规模：minimal=仅插件数据，common=+GB2312 一级，full=+GB2312 全部")
    parser.add_argument("--report", action="store_true", help="只统计字符数不写文件")
    args = parser.parse_args()

    text = build_charset(args.level)
    print(f"字符集（{args.level}）：{len(text)} 个字符")
    if args.report:
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, weight in WEIGHTS.items():
        target = args.out / name
        build_one(args.source, target, weight, text)
        size = target.stat().st_size
        total += size
        print(f"  {name}：{size / 1024:.0f} KB（wght={weight}）")
    print(f"合计 {total / 1024:.0f} KB")

    if args.license and args.license.is_file():
        shutil.copyfile(args.license, args.out / "OFL.txt")
        print(f"已复制许可：{args.out / 'OFL.txt'}")
    else:
        print("提示：未提供 OFL.txt，请记得把 SIL OFL 1.1 许可文本一并放进 assets/fonts/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
