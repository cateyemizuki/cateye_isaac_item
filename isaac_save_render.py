"""存档解析图的本地绘制（Pillow）。

**只走本地绘制**：宿主自带的 ``render.html2png`` 在实测中经常超时失败（随后还要回退到本地），
所以插件不再调用它，也不声明该能力——解析图始终由本模块用 Pillow 直接画出来，
结果稳定、可离线复现、不依赖宿主的浏览器环境。

字体：随插件打包了一份 **Noto Sans SC 子集**（SIL OFL 1.1，见 ``assets/fonts/OFL.txt``），
由 ``tools/build_fonts.py`` 生成；找不到内置字体时才退回系统字体候选
（Windows 的微软雅黑等），再不行用 Pillow 默认字体。

布局：宽度 ``width``（CSS 像素）与倍率 ``scale`` 共同决定输出：
``output = (width, height) × scale``；宽度变小时排版会真收窄（统计卡自动 4/3/2 列、
进度条与标签同步压缩），因此**长宽比可变**，而不是整体等比缩放。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # 插件作为包被导入时走相对导入
    from .isaac_save import ProgressTrack, SaveReport
except ImportError:  # pragma: no cover - 插件目录被直接加入 sys.path 时
    from isaac_save import ProgressTrack, SaveReport  # type: ignore[no-redef]

#: 卡片默认宽度（CSS 像素）；插件配置项 ``[save] image_width`` 会覆盖它
CARD_WIDTH = 700

_PLUGIN_DIR = Path(__file__).resolve().parent
_BUNDLED_FONT_DIR = _PLUGIN_DIR / "assets" / "fonts"

#: 系统字体兜底候选（内置字体缺失时使用）
_FALLBACK_FONTS: Tuple[str, ...] = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/PingFang.ttc",
)
_FALLBACK_BOLD_FONTS: Tuple[str, ...] = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/Dengb.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
) + _FALLBACK_FONTS

#: 名称类清单的标题（渲染图里按顺序展示）
TRACK_CHIP_LABELS: Tuple[Tuple[str, str], ...] = (
    ("challenges", "还没完成的挑战"),
    ("achievements", "还没解锁的成就"),
    ("minibosses", "还没遇到的小 BOSS"),
    ("bosses", "还没记录的 BOSS"),
)

# 配色
_BG = (18, 16, 24)
_CARD = (30, 28, 38)
_CHIP = (35, 33, 46)
_CHIP_TEXT = (230, 226, 240)
_CHIP_MUTED = (140, 135, 158)
_TEXT = (240, 236, 246)
_TEXT_DIM = (184, 178, 199)
_TEXT_FAINT = (141, 135, 158)
_ACCENT = (224, 92, 74)
_GOLD = (247, 201, 72)
_TRACK_BG = (38, 36, 48)


def _stat_columns(width: int) -> int:
    """统计卡的列数：卡片越窄列数越少，避免数字被挤成一团。"""

    if width >= 860:
        return 4
    if width >= 700:
        return 3
    return 2


def bundled_font_paths() -> Tuple[Path, Path]:
    """返回内置字体路径 ``(regular, bold)``。"""

    return (
        _BUNDLED_FONT_DIR / "NotoSansSC-Regular.ttf",
        _BUNDLED_FONT_DIR / "NotoSansSC-Bold.ttf",
    )


def _load_font(size: int, *, bold: bool = False) -> Any:
    """加载字体：内置 Noto Sans SC 子集优先，其次系统字体，最后 Pillow 默认。"""

    from PIL import ImageFont

    regular, bold_path = bundled_font_paths()
    prefer = [bold_path if bold else regular, regular if bold else bold_path]
    candidates = [str(path) for path in prefer if path.is_file()]
    candidates += list(_FALLBACK_BOLD_FONTS if bold else _FALLBACK_FONTS)
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size=size, index=0)
        except Exception:  # noqa: BLE001 - 字体损坏/不支持时继续找下一个
            continue
    return ImageFont.load_default()


def using_bundled_font() -> bool:
    """内置字体是否可用（供插件在日志里说明渲染是否自足）。"""

    regular, bold = bundled_font_paths()
    return regular.is_file() and bold.is_file()


def canvas_available() -> bool:
    """判断当前环境能否用 Pillow 绘制解析图。"""

    try:
        import PIL  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _bar_rgb(percent: float) -> Tuple[int, int, int]:
    if percent >= 90:
        return (247, 201, 72)
    if percent >= 60:
        return (126, 217, 87)
    if percent >= 30:
        return (91, 192, 235)
    return (224, 92, 74)


def build_card_png(
    report: SaveReport,
    *,
    width: int = CARD_WIDTH,
    scale: float = 1.4,
    nickname: str = "",
    user_id: str = "",
    max_chips: int = 24,
    max_track_chips: int = 12,
    source_note: str = "",
    bound_at: str = "",
) -> bytes:
    """把解析报告画成 PNG，返回图片字节。

    ``width`` 是排版宽度（CSS 像素），``scale`` 是输出倍率：文字按 ``× scale`` 的像素尺寸
    重新排版绘制（不是先画小再放大），因此任何倍率下都清晰。
    """

    from PIL import Image, ImageDraw

    analysis = report.analysis
    width = max(int(width), 520)
    scale = max(min(float(scale), 4.0), 0.8)

    def S(value: float) -> int:
        """把 CSS 像素换算成输出像素。"""

        return max(int(round(value * scale)), 1)

    wide = width >= 860
    compact = width >= 700
    pad = 28 if wide else (22 if compact else 18)
    gap = 10 if wide else (9 if compact else 8)
    label_w = 124 if wide else (112 if compact else 100)
    count_w = 104 if wide else (92 if compact else 84)
    percent_w = 54 if wide else (48 if compact else 44)
    stat_columns = _stat_columns(width)
    chip_h = 30 if wide else (28 if compact else 27)
    chip_pad = 12 if wide else 11

    font_title = _load_font(S(26 if wide else (25 if compact else 23)), bold=True)
    font_section = _load_font(S(15 if wide else 14.5), bold=True)
    font_label = _load_font(S(13.5 if wide else 13))
    font_small = _load_font(S(12.5 if wide else 12))
    font_value = _load_font(S(22 if wide else 21), bold=True)
    font_chip = _load_font(S(12.5 if wide else 12))
    font_footer = _load_font(S(11.5))

    tracks = list(report.tracks)
    counters = list(report.counters)
    chips = report.undiscovered[: max(int(max_chips), 1)]
    named_groups: List[Tuple[str, int, List[str]]] = []
    if max_track_chips > 0:
        track_map = {track.key: track for track in tracks}
        for key, title in TRACK_CHIP_LABELS:
            entries = list(report.missing_named.get(key) or ())
            if not entries:
                continue
            track = track_map.get(key)
            total = track.remaining if track is not None else len(entries)
            labels = [entry.name for entry in entries if entry.name][:max_track_chips]
            if not labels:
                continue
            named_groups.append((title, total, labels))

    row_h = S(24 + gap)
    stat_h = S(72)
    stat_row_h = stat_h + S(gap)
    chip_row_h = S(chip_h + 8)
    # 标签行数按每行最多 4 个估算，最终高度会按实际用到的位置裁剪
    chip_rows = (len(chips) + 3) // 4
    named_rows = sum((len(names) + 3) // 4 for _title, _total, names in named_groups)

    height = S(pad) + S(46 + 34 + 20)  # 头部
    height += S(40) + len(tracks) * row_h
    height += S(40) + ((len(counters) + stat_columns - 1) // stat_columns) * stat_row_h
    height += S(40) + chip_rows * chip_row_h + S(24)
    height += named_rows * chip_row_h + len(named_groups) * S(40) + S(24)
    height += S(46) + S(pad)

    image = Image.new("RGB", (S(width), height), _BG)
    draw = ImageDraw.Draw(image)
    for y in range(min(S(240), height)):
        ratio = 1 - y / max(S(240), 1)
        draw.line(
            [(0, y), (S(width), y)],
            fill=(int(18 + 42 * ratio * 0.55), int(16 + 16 * ratio * 0.5), int(24 + 14 * ratio * 0.5)),
        )

    pad_px = S(pad)
    y = pad_px
    draw.text((pad_px, y), "以撒的结合 · 存档解析", font=font_title, fill=_TEXT)
    y += S(46)
    badge = analysis.version_label + (f" · 存档位 {analysis.slot}" if analysis.slot else "")
    badge_w = int(draw.textlength(badge, font=font_small)) + S(24)
    draw.rounded_rectangle(
        [(S(width) - pad_px - badge_w, pad_px + S(2)), (S(width) - pad_px, pad_px + S(30))],
        radius=S(14),
        fill=(58, 32, 34),
    )
    draw.text((S(width) - pad_px - badge_w + S(12), pad_px + S(8)), badge, font=font_small, fill=(255, 157, 140))
    sub_parts = [part for part in (nickname, f"QQ {user_id}" if user_id else "", analysis.file_name) if part]
    subtitle_w = S(width) - pad_px * 2 - badge_w - S(16)
    draw.text(
        (pad_px, y),
        _fit_text(draw, " · ".join(sub_parts), font_small, subtitle_w),
        font=font_small,
        fill=(150, 145, 168),
    )
    y = pad_px + S(46 + 34)

    # 进度总览
    y = _draw_section(draw, pad_px, y, S(width), "进度总览", font_section, S(40))
    for track in tracks:
        draw.text((pad_px, y + S(2)), _fit_text(draw, track.label, font_label, S(label_w - 8)), font=font_label, fill=_TEXT_DIM)
        bar_x = pad_px + S(label_w)
        bar_w = S(width) - pad_px - bar_x - S(count_w + percent_w + gap * 2)
        draw.rounded_rectangle([(bar_x, y + S(6)), (bar_x + bar_w, y + S(18))], radius=S(6), fill=_TRACK_BG)
        fill_w = int(bar_w * max(min(track.percent, 100.0), 0.0) / 100)
        if fill_w > 0:
            draw.rounded_rectangle([(bar_x, y + S(6)), (bar_x + fill_w, y + S(18))], radius=S(6), fill=_bar_rgb(track.percent))
        draw.text((S(width) - pad_px - S(count_w + percent_w + gap), y), track.ratio_text, font=font_label, fill=(242, 238, 248))
        draw.text((S(width) - pad_px - S(percent_w), y + S(3)), track.percent_text, font=font_small, fill=_TEXT_FAINT)
        y += row_h

    # 全局统计（末行不满时居中）
    y = _draw_section(draw, pad_px, y, S(width), "全局统计", font_section, S(40))
    stat_gap_px = S(gap)
    card_w = (S(width) - pad_px * 2 - stat_gap_px * (stat_columns - 1)) // stat_columns
    for row_start in range(0, len(counters), stat_columns):
        row_items = counters[row_start : row_start + stat_columns]
        offset = (stat_columns - len(row_items)) * (card_w + stat_gap_px) // 2
        row_y = y + (row_start // stat_columns) * stat_row_h
        for column, stat in enumerate(row_items):
            x = pad_px + offset + column * (card_w + stat_gap_px)
            draw.rounded_rectangle([(x, row_y), (x + card_w, row_y + stat_h)], radius=S(12), fill=_CARD)
            draw.text((x + S(12), row_y + S(10)), f"{stat.value:,}", font=font_value, fill=_GOLD)
            draw.text((x + S(12), row_y + S(46)), stat.label, font=font_small, fill=(164, 159, 181))
    y += ((len(counters) + stat_columns - 1) // stat_columns) * stat_row_h + S(6)

    # 还没拿到的道具
    y = _draw_section(draw, pad_px, y, S(width), f"还没拿到的道具（{len(report.undiscovered)} 件）", font_section, S(40))
    if not report.undiscovered:
        draw.text((pad_px, y), "全部道具都已发现。", font=font_label, fill=(180, 175, 195))
        y += S(30)
    else:
        labels = [
            (f"{item.item_id} {item.name}", True) if item.known else (f"{item.item_id} 未收录", False)
            for item in chips
        ]
        y = _draw_chips(
            draw, pad_px, y, S(width), font_chip, labels,
            chip_h=S(chip_h), chip_pad=S(chip_pad), row_h=chip_row_h, fill=_CHIP, color=_CHIP_TEXT, muted=_CHIP_MUTED,
        )

    # 名称类清单（挑战 / 成就 / 小 BOSS）
    for title, total, names in named_groups:
        y = _draw_section(draw, pad_px, y, S(width), f"{title}（{total} 个）", font_section, S(40))
        y = _draw_chips(
            draw, pad_px, y, S(width), font_chip, [(name, True) for name in names],
            chip_h=S(chip_h), chip_pad=S(chip_pad), row_h=chip_row_h, fill=(32, 36, 44), color=(206, 226, 240),
        )

    footer = "只读解析，不会修改存档"
    if bound_at:
        footer = f"绑定于 {bound_at} ｜ " + footer
    if analysis.bestiary_walked and analysis.trailing_bytes is not None:
        footer += f" ｜ 11 段数据块全部对齐（尾部保留 {analysis.trailing_bytes} 字节）"
    footer += f" ｜ 文件 {analysis.file_size / 1024:.1f} KB"
    if source_note:
        footer += f" ｜ {source_note}"
    footer_y = y + S(8)
    for index, line in enumerate(_wrap_text(draw, footer, font_footer, S(width) - pad_px * 2)):
        draw.text((pad_px, footer_y + index * S(18)), line, font=font_footer, fill=(115, 110, 131))
    footer_lines = max(len(_wrap_text(draw, footer, font_footer, S(width) - pad_px * 2)), 1)

    # 按实际用到的位置裁剪掉多余留白
    final_height = min(height, footer_y + footer_lines * S(18) + S(14))
    if final_height < height:
        image = image.crop((0, 0, S(width), final_height))
    elif final_height > height:
        canvas = Image.new("RGB", (S(width), final_height), _BG)
        canvas.paste(image, (0, 0))
        image = canvas

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _wrap_text(draw: Any, text: str, font: Any, max_width: int) -> List[str]:
    """按像素宽度折行（优先在分隔符处断开）。"""

    if max_width <= 0:
        return [text]
    lines: List[str] = []
    current = ""
    for piece in str(text).split(" ｜ "):
        candidate = f"{current} ｜ {piece}" if current else piece
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        if draw.textlength(piece, font=font) <= max_width:
            current = piece
            continue
        # 单段太长：按字符硬折
        buffer = ""
        for char in piece:
            if draw.textlength(buffer + char, font=font) > max_width and buffer:
                lines.append(buffer)
                buffer = char
            else:
                buffer += char
        current = buffer
    if current:
        lines.append(current)
    return lines or [""]


def _draw_section(draw: Any, pad: int, y: int, width: int, title: str, font: Any, advance: int) -> int:
    """画一条分区标题，返回下一行的 y。"""

    draw.rounded_rectangle([(pad, y + int(advance * 0.08)), (pad + max(int(advance * 0.1), 3), y + int(advance * 0.5))], radius=2, fill=_ACCENT)
    draw.text((pad + max(int(advance * 0.35), 10), y), title, font=font, fill=(207, 201, 221))
    return y + advance


def _draw_chips(
    draw: Any,
    pad: int,
    y: int,
    width: int,
    font: Any,
    labels: Sequence[Tuple[str, bool]],
    *,
    chip_h: int,
    chip_pad: int,
    row_h: int,
    fill: Tuple[int, int, int],
    color: Tuple[int, int, int],
    muted: Optional[Tuple[int, int, int]] = None,
) -> int:
    """按行排布标签（``(文本, 是否已知)``），返回下一行的 y。"""

    muted_color = muted or color
    x = pad
    for text, known in labels:
        chip_w = int(draw.textlength(text, font=font)) + chip_pad * 2
        if x + chip_w > width - pad:
            x = pad
            y += row_h
        draw.rounded_rectangle([(x, y), (x + chip_w, y + chip_h)], radius=chip_h // 2, fill=fill)
        draw.text((x + chip_pad, y + (chip_h - font.size) // 2 - 1), text, font=font, fill=color if known else muted_color)
        x += chip_w + 8
    return y + row_h


def _fit_text(draw: Any, text: str, font: Any, max_width: int) -> str:
    """把文本裁到指定像素宽度内（超出用省略号），避免与右侧内容重叠。"""

    if max_width <= 0 or draw.textlength(text, font=font) <= max_width:
        return text
    trimmed = str(text)
    while trimmed and draw.textlength(trimmed + "…", font=font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else str(text)
