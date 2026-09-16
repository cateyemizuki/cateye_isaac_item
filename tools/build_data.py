"""把上游道具数据库转换为插件内置的精简数据集。

用法（在插件目录下执行）：

    python tools/build_data.py                      # 自动下载上游数据
    python tools/build_data.py --raw path/to/items.db.json   # 用本地已下载的原始文件

上游数据源：
    ChenDekang617/isaac-item-recommender
    https://github.com/ChenDekang617/isaac-item-recommender
    （knowledge/items.db.json，爬取自以撒 Wiki / 灰机 wiki）

转换内容：
    1. 丢弃上游 ``description`` 字段（对道具是整页 dump、对饰品/卡牌/药丸是错位内容）。
    2. 清洗 ``effects.text`` 的 LaTeX 与 wiki 排版残留（``\\dfrac{1}{6}`` → ``1/6``、
       ``\\times`` → ``×``、下标 ``P_{幸运}`` → ``P幸运``、中文之间多余空格等）。
    3. 合并 ``assets/isaac_extra.json`` 里人工补录的表格/列表（见下方「表格缺失」说明）。
    4. 只保留检索与展示需要的字段，输出 ``assets/isaac_items.json``。

表格缺失说明
------------
上游爬虫（``tools/crawl_wiki_v2.py``）只提取页面里的 ``<li>`` 列表项，**从不解析 ``<table>``**，
所以 wiki 里用表格描述效果的道具（潘多拉魔盒、透明符文、安慰剂……）在上游数据里会以
「……触发以下效果：」这样的断句结尾，表格内容整段丢失；``description`` 字段又被统一截断在
200 字符，也救不回来。这类条目用 ``assets/isaac_extra.json`` 补录，格式见该文件内的 ``meta``。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAW_URL = (
    "https://raw.githubusercontent.com/ChenDekang617/isaac-item-recommender/"
    "HEAD/knowledge/items.db.json"
)
SOURCE_NAME = "ChenDekang617/isaac-item-recommender"
SOURCE_URL = "https://github.com/ChenDekang617/isaac-item-recommender"
SOURCE_DATA_PATH = "knowledge/items.db.json"
DATA_NOTE = "数据爬取自以撒 Wiki（灰机 wiki）；上游仓库未声明 LICENSE"

DEFAULT_EXTRA_PATH = Path(__file__).resolve().parent.parent / "assets" / "isaac_extra.json"

KIND_BY_TYPE = {
    "passive": "道具",
    "active": "道具",
    "trinket": "饰品",
    "card": "卡牌",
    "pill": "药丸",
}

MAX_SYNERGIES = 200
MAX_TRANSFORMATIONS = 6

# 前置替换：先去掉不影响花括号结构的 LaTeX 命令，这样后续分数展开才能命中嵌套分数
LATEX_PRE_TOKENS = [
    (r"\\left\b", ""),
    (r"\\right\b", ""),
    (r"\\lfloor\b", ""),
    (r"\\rfloor\b", ""),
    (r"\\lceil\b", ""),
    (r"\\rceil\b", ""),
    (r"\\times(?![a-zA-Z])", "×"),
    (r"\\cdot(?![a-zA-Z])", "×"),
    (r"\\div(?![a-zA-Z])", "÷"),
    (r"\\geq?(?![a-zA-Z])", "≥"),
    (r"\\leq?(?![a-zA-Z])", "≤"),
    (r"\\neq(?![a-zA-Z])", "≠"),
    (r"\\pm(?![a-zA-Z])", "±"),
    (r"\\max(?![a-zA-Z])", "max"),
    (r"\\min(?![a-zA-Z])", "min"),
    (r"\\text\s*\{([^{}]*)\}", r"\1"),
    (r"\\mathrm\s*\{([^{}]*)\}", r"\1"),
    (r"\\,|\\;|\\!|\\quad(?![a-zA-Z])|\\qquad(?![a-zA-Z])", " "),
    (r"\\%", "%"),
    (r"\\\$", "$"),
]

_FRAC_RE = re.compile(r"\\(?:d|c|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SUB_RE = re.compile(r"[_^]\s*\{([^{}]*)\}")
_SUB_SINGLE_RE = re.compile(r"[_^]\s*([A-Za-z0-9])")
_LEFT_OVER_COMMAND_RE = re.compile(r"\\[a-zA-Z]+\s*")
_NUMERIC_ONLY_RE = re.compile(r"^[-\d.\s%×x*+/]+$")


def _expand_fractions(text: str) -> str:
    """把（可能嵌套的）LaTeX 分数展开成 a/b，由内向外反复替换。"""

    for _ in range(12):
        new_text = _FRAC_RE.sub(lambda m: f"{m.group(1).strip()}/{m.group(2).strip()}", text)
        if new_text == text:
            break
        text = new_text
    return text


def clean_effect(text: str) -> str:
    """清洗单条效果文本，保留换行结构。"""

    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    for pattern, replacement in LATEX_PRE_TOKENS:
        text = re.sub(pattern, replacement, text)
    # 上下标的花括号必须先去掉，否则分数展开的正则会被 {幸运} 挡住
    text = _SUB_RE.sub(lambda m: m.group(1).strip(), text)
    text = _SUB_SINGLE_RE.sub(lambda m: m.group(1), text)
    text = _expand_fractions(text)
    text = _LEFT_OVER_COMMAND_RE.sub("", text)
    text = text.replace("\\", "").replace("{", "").replace("}", "")
    # 中文字符之间的多余空格（wiki 链接排版残留）
    text = re.sub(r"(?<=[\u4e00-\u9fff\u3000-\u303f])[ \t]+(?=[\u4e00-\u9fff\u3000-\u303f])", "", text)
    # 标点前后的空格
    text = re.sub(r"\s+([，。；：、！？）】」])", r"\1", text)
    text = re.sub(r"([（【「])\s+", r"\1", text)
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    text = "\n".join(lines)
    # 上游对饰品/卡牌/药丸抓到的效果文本是错位的纯数字（如 "2"），一律视为缺失
    if _NUMERIC_ONLY_RE.fullmatch(text):
        return ""
    return text


def _clean_inline(text: str) -> str:
    """单行场景的清洗（标签、名称等）。"""

    return " ".join(clean_effect(text).split())


def _normalize_extra(entry: dict) -> dict:
    """规范化人工补录内容（表格 + 补充行）。"""

    result: dict = {}
    table = entry.get("table")
    if isinstance(table, dict) and table.get("rows"):
        columns = [str(column).strip() for column in (table.get("columns") or [])]
        rows = []
        for row in table["rows"]:
            if not isinstance(row, (list, tuple)):
                continue
            cells = [str(cell).strip() for cell in row]
            if any(cells):
                rows.append(cells)
        if rows:
            result["table"] = {
                "title": str(table.get("title") or "").strip(),
                "columns": columns,
                "rows": rows,
                "source": str(table.get("source") or "").strip(),
            }
    lines = [str(line).strip() for line in (entry.get("extra_lines") or []) if str(line).strip()]
    if lines:
        result["extra_lines"] = lines
    return result


def convert_record(record: dict, extra: dict | None = None) -> dict | None:
    """把一条上游记录转换为插件内置格式。

    上游只对「道具」抓到了对齐良好的详情（品质/标签/效果/协同）；饰品、卡牌、药丸的
    效果与协同是错位的（例如饰品「妈妈的脚趾甲」抓到了道具「魔法蘑菇」的效果），
    因此这些类型只保留 id / 名称 / 分类，详情一律留空，由插件在展示时如实说明。
    """

    name = _clean_inline(record.get("name") or "")
    if not name:
        return None

    raw_type = str(record.get("type") or "passive")
    kind = KIND_BY_TYPE.get(raw_type, "道具")
    trusted = kind == "道具"

    quality = record.get("quality")
    try:
        quality = int(quality)
    except (TypeError, ValueError):
        quality = -1
    if not trusted:
        quality = -1

    tags = [_clean_inline(tag) for tag in (record.get("tags") or []) if str(tag).strip()] if trusted else []
    effect = clean_effect((record.get("effects") or {}).get("text") or "") if trusted else ""

    synergies = []
    synergy_total = 0
    if trusted:
        raw_synergies = [entry for entry in (record.get("synergies") or []) if isinstance(entry, dict)]
        synergy_total = len(raw_synergies)
        for entry in raw_synergies:
            synergy_name = _clean_inline(entry.get("item") or "")
            if not synergy_name:
                continue
            synergies.append({"name": synergy_name, "effect": _clean_inline(entry.get("effect") or "")})
            if len(synergies) >= MAX_SYNERGIES:
                break

    transformations = []
    if trusted:
        for entry in record.get("transformations") or []:
            if not isinstance(entry, dict):
                continue
            set_name = _clean_inline(entry.get("set_name") or "")
            if not set_name:
                continue
            try:
                required = int(entry.get("required_count") or 0)
            except (TypeError, ValueError):
                required = 0
            transformations.append(
                {
                    "name": set_name,
                    "tag": _clean_inline(entry.get("set_tag") or ""),
                    "count": required,
                    "effect": _clean_inline(entry.get("set_effect") or ""),
                }
            )
            if len(transformations) >= MAX_TRANSFORMATIONS:
                break

    item = {
        "id": str(record.get("id") or "").strip(),
        "name": name,
        "en": _clean_inline(record.get("english_name") or "") if trusted else "",
        "quality": quality,
        "kind": kind,
        "tags": tags,
        "effect": effect,
        "synergies": synergies,
        "synergy_total": synergy_total,
        "transformations": transformations,
    }
    if extra:
        normalized_extra = _normalize_extra(extra)
        if normalized_extra:
            item["extra"] = normalized_extra
    return item


def load_extra(extra_path: Path | None) -> tuple[dict, dict]:
    """读取人工补录文件，返回 ``(按 id 索引的补录内容, 补录文件 meta)``。"""

    if extra_path is None:
        return {}, {}
    path = Path(extra_path)
    if not path.exists():
        return {}, {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return {}, {}
    items = data.get("items")
    if not isinstance(items, dict):
        return {}, data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return {str(key): value for key, value in items.items() if isinstance(value, dict)}, (
        data.get("meta") if isinstance(data.get("meta"), dict) else {}
    )


def build(raw: dict, extra: dict | None = None, extra_meta: dict | None = None) -> dict:
    """构建完整数据集（含 meta 头）。"""

    extra = extra or {}
    items = []
    extra_hits = 0
    for key in sorted(raw.keys(), key=lambda k: (len(str(k)), str(k))):
        record = raw[key]
        if not isinstance(record, dict):
            continue
        item_id = str(record.get("id") or key).strip()
        entry = extra.get(item_id)
        converted = convert_record(record, entry)
        if converted is None:
            continue
        if not converted["id"]:
            converted["id"] = str(key)
        if "extra" in converted:
            extra_hits += 1
        items.append(converted)

    meta = {
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "source_data_path": SOURCE_DATA_PATH,
        "note": DATA_NOTE,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(items),
        "with_effect": sum(1 for item in items if item["effect"]),
        "extra_count": extra_hits,
    }
    if extra_hits and extra_meta:
        meta["extra_note"] = str(extra_meta.get("note") or "")
        meta["extra_source"] = str(extra_meta.get("source") or "")
    return {"meta": meta, "items": items}


def load_raw(raw_path: Path | None) -> dict:
    """读取原始数据；未指定路径时从上游下载。"""

    if raw_path is not None:
        with raw_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    with urllib.request.urlopen(RAW_URL, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="生成插件内置的以撒道具数据集")
    parser.add_argument("--raw", type=Path, default=None, help="本地原始 items.db.json 路径")
    parser.add_argument(
        "--extra",
        type=Path,
        default=DEFAULT_EXTRA_PATH,
        help="人工补录的表格/列表文件（默认 assets/isaac_extra.json，不存在则跳过）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets" / "isaac_items.json",
        help="输出文件路径",
    )
    args = parser.parse_args()

    raw = load_raw(args.raw)
    extra, extra_meta = load_extra(args.extra)
    dataset = build(raw, extra, extra_meta)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(dataset, handle, ensure_ascii=False, separators=(",", ":"))

    size_kb = args.out.stat().st_size / 1024
    meta = dataset["meta"]
    print(f"已写入 {args.out}")
    print(f"条目 {meta['count']} 条，其中带效果文本 {meta['with_effect']} 条，文件 {size_kb:.0f} KB")
    if meta["extra_count"]:
        print(f"人工补录生效 {meta['extra_count']} 条（{args.extra}）")
    by_kind: dict[str, int] = {}
    for item in dataset["items"]:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    print("分类：" + "，".join(f"{kind} {count}" for kind, count in sorted(by_kind.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
