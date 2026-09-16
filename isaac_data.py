"""以撒道具图鉴的数据加载、检索与排版。

本模块是纯逻辑实现，不依赖 ``maibot_sdk``，可以脱离插件 Runner 单独运行与单测：

    python -c "from isaac_data import ItemDatabase; db=ItemDatabase.load('assets/isaac_items.json'); print(db.stats())"

数据来源与字段可信度见 ``tools/build_data.py`` 与 ``README.md``。
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

WIKI_PAGE_URL_TEMPLATE = "https://isaac.huijiwiki.com/wiki/{}"

# 效果正文以这些形式结尾，说明 wiki 里后面本该是表格或列表，但上游爬虫没抓
_TRUNCATED_TAIL_RE = re.compile(r"(?:[：:]|见下(?:表|方|文)?|如下)\s*$")

KINDS: Tuple[str, ...] = ("道具", "饰品", "卡牌", "药丸")

KIND_ALIASES: Dict[str, str] = {
    "道具": "道具",
    "物品": "道具",
    "item": "道具",
    "items": "道具",
    "collectible": "道具",
    "饰品": "饰品",
    "trinket": "饰品",
    "trinkets": "饰品",
    "卡牌": "卡牌",
    "卡片": "卡牌",
    "card": "卡牌",
    "cards": "卡牌",
    "药丸": "药丸",
    "胶囊": "药丸",
    "pill": "药丸",
    "pills": "药丸",
}

QUALITY_LABEL: Dict[int, str] = {
    0: "0 垃圾",
    1: "1 普通",
    2: "2 不错",
    3: "3 好",
    4: "4 极品",
}

# 匹配原因 -> 面向用户的说明
MATCH_REASON_LABEL: Dict[str, str] = {
    "id": "编号命中",
    "name": "名称精确匹配",
    "name_prefix": "名称前缀匹配",
    "name_contains": "名称包含",
    "en": "英文名精确匹配",
    "en_prefix": "英文名前缀匹配",
    "en_contains": "英文名包含",
    "tag": "标签检索",
    "effect": "效果正文检索",
    "synergy": "协同检索",
    "fuzzy": "近似匹配",
}

_NORMALIZE_RE = re.compile(r"[\s\-_·・.,，。、;；:：!！?？'\"“”‘’()（）\[\]【】/\\|]+")


def normalize(text: Any) -> str:
    """归一化检索用文本：去空白与常见标点、统一小写。"""

    return _NORMALIZE_RE.sub("", str(text or "")).lower()


@dataclass(frozen=True)
class Item:
    """一条图鉴条目。"""

    id: str
    name: str
    en: str
    quality: int
    kind: str
    tags: Tuple[str, ...]
    effect: str
    synergies: Tuple[Tuple[str, str], ...]
    transformations: Tuple[Dict[str, Any], ...]
    synergy_total: int = 0
    extra_lines: Tuple[str, ...] = ()
    extra_table: Optional[Dict[str, Any]] = None
    name_norm: str = field(default="", compare=False)
    en_norm: str = field(default="", compare=False)

    @property
    def has_effect(self) -> bool:
        return bool(self.effect.strip())

    @property
    def quality_label(self) -> str:
        return QUALITY_LABEL.get(self.quality, f"{self.quality} 未知")

    @property
    def wiki_url(self) -> str:
        """灰机 wiki 对应页面链接（保留中文，便于阅读）。"""

        return WIKI_PAGE_URL_TEMPLATE.format(self.name)

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "Item":
        name = str(record.get("name") or "").strip()
        en = str(record.get("en") or "").strip()
        synergies: List[Tuple[str, str]] = []
        for entry in record.get("synergies") or []:
            if isinstance(entry, dict):
                synergy_name = str(entry.get("name") or "").strip()
                if synergy_name:
                    synergies.append((synergy_name, str(entry.get("effect") or "").strip()))
        transformations: List[Dict[str, Any]] = []
        for entry in record.get("transformations") or []:
            if isinstance(entry, dict) and str(entry.get("name") or "").strip():
                transformations.append(dict(entry))
        try:
            quality = int(record.get("quality", -1))
        except (TypeError, ValueError):
            quality = -1
        try:
            synergy_total = int(record.get("synergy_total") or len(synergies))
        except (TypeError, ValueError):
            synergy_total = len(synergies)
        extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
        extra_lines = tuple(str(line).strip() for line in (extra.get("extra_lines") or []) if str(line).strip())
        extra_table = extra.get("table") if isinstance(extra.get("table"), dict) else None
        if extra_table is not None and not extra_table.get("rows"):
            extra_table = None
        return cls(
            id=str(record.get("id") or "").strip(),
            name=name,
            en=en,
            quality=quality,
            kind=str(record.get("kind") or "道具").strip() or "道具",
            tags=tuple(str(tag).strip() for tag in (record.get("tags") or []) if str(tag).strip()),
            effect=str(record.get("effect") or "").strip(),
            synergies=tuple(synergies),
            transformations=tuple(transformations),
            synergy_total=max(synergy_total, len(synergies)),
            extra_lines=extra_lines,
            extra_table=extra_table,
            name_norm=normalize(name),
            en_norm=normalize(en),
        )


@dataclass(frozen=True)
class SearchHit:
    """一条检索结果。"""

    item: Item
    score: float
    reason: str

    @property
    def reason_label(self) -> str:
        return MATCH_REASON_LABEL.get(self.reason, self.reason)


@dataclass
class DisplayConfig:
    """展示相关配置（由插件配置节映射而来）。"""

    show_english: bool = True
    show_quality: bool = True
    show_tags: bool = True
    show_synergies: bool = True
    max_synergies: int = 5
    max_effect_chars: int = 0
    show_wiki_link: bool = True


@dataclass(frozen=True)
class DetailOutput:
    """详情排版结果。

    ``text`` 是直接发到聊天里的正文；``forward_text`` 非空时表示协同条数超过内联上限，
    需要额外用合并转发发送（正文里只留一行提示，不再折叠展示）。
    """

    text: str
    forward_text: str = ""


# 协同超出内联上限时的处理方式
SYNERGY_OVERFLOW_FORWARD = "forward"  # 全部协同改用合并转发（聊天路径）
SYNERGY_OVERFLOW_TRUNCATE = "truncate"  # 只列前 N 条并注明（工具路径，无法发合并转发）


class ItemDatabase:
    """图鉴数据库：负责加载、检索与排版。"""

    def __init__(self, items: Sequence[Item], meta: Optional[Dict[str, Any]] = None) -> None:
        self._items: List[Item] = list(items)
        self.meta: Dict[str, Any] = dict(meta or {})
        self._by_id: Dict[str, Item] = {item.id: item for item in self._items if item.id}

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> "ItemDatabase":
        """从 JSON 文件加载数据集。"""

        data_path = Path(path)
        with data_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            records = raw.get("items") or []
            meta = raw.get("meta") or {}
        else:
            records = raw
            meta = {}
        items = [Item.from_record(record) for record in records if isinstance(record, dict)]
        return cls(items, meta)

    def counts_line(self) -> str:
        """返回不含来源的规模描述，便于拼进帮助文本。"""

        by_kind: Dict[str, int] = {}
        for item in self._items:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        detail = "，".join(f"{kind} {by_kind.get(kind, 0)}" for kind in KINDS if by_kind.get(kind))
        with_effect = sum(1 for item in self._items if item.has_effect)
        return f"共 {len(self._items)} 条（{detail}），带效果文本 {with_effect} 条"

    def stats(self) -> str:
        """返回一行统计信息，便于日志与自检。"""

        source = str(self.meta.get("source") or "内置数据")
        return f"{self.counts_line()}，来源 {source}"

    # ------------------------------------------------------------------
    # 基础访问
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def get(self, item_id: str) -> Optional[Item]:
        return self._by_id.get(str(item_id).strip())

    def item_names(self, kind: Optional[str] = None) -> Dict[str, str]:
        """返回 ``编号 → 名称`` 映射（存档解析用它给未发现道具标中文名）。

        ``kind`` 非空时只导出该分类；没有编号的条目会被跳过。
        """

        names: Dict[str, str] = {}
        for item in self._items:
            if kind and item.kind != kind:
                continue
            if item.id and item.name:
                names[item.id] = item.name
        return names

    def random_item(self, kind: Optional[str] = None) -> Optional[Item]:
        """随机取一条；``kind`` 非空时只在该分类内随机。"""

        pool = self._items
        if kind:
            pool = [item for item in pool if item.kind == kind]
        if not pool:
            return None
        return random.choice(pool)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        fuzzy_threshold: float = 0.55,
        search_by_tag: bool = True,
        search_by_effect: bool = True,
        search_by_synergy: bool = True,
        kinds: Optional[Sequence[str]] = None,
    ) -> List[SearchHit]:
        """检索图鉴条目，按相关度排序返回。

        优先级：编号 → 名称精确/前缀/包含 → 英文名 → 标签 → 效果正文 → 协同正文 → 模糊近似。
        """

        normalized = normalize(query)
        if not normalized:
            return []

        pool = self._items
        if kinds:
            allowed = set(kinds)
            pool = [item for item in pool if item.kind in allowed]

        hits: List[SearchHit] = []
        wanted = max(int(limit), 1)

        if normalized.isdigit():
            item = self._by_id.get(normalized)
            if item is not None:
                hits.append(SearchHit(item, 1200.0, "id"))

        for item in pool:
            score, reason = self._score_item(item, normalized)
            if score > 0:
                hits.append(SearchHit(item, score, reason))

        # 名称匹配不足时补充标签检索（例如「射速」这类效果关键词）
        if search_by_tag and len(hits) < wanted:
            matched_ids = {hit.item.id for hit in hits}
            for item in pool:
                if item.id in matched_ids:
                    continue
                if not any(normalized in normalize(tag) for tag in item.tags):
                    continue
                score = 300.0 + min(item.quality, 4) * 10 - len(item.name) * 0.1
                hits.append(SearchHit(item, score, "tag"))

        # 再退一步：在效果正文里找关键词（例如「飞行」「中毒」）
        if search_by_effect and len(hits) < wanted and len(normalized) >= 2:
            matched_ids = {hit.item.id for hit in hits}
            for item in pool:
                if item.id in matched_ids or not item.effect:
                    continue
                if normalized not in normalize(item.effect):
                    continue
                score = 150.0 + min(item.quality, 4) * 5
                hits.append(SearchHit(item, score, "effect"))

        # 最后一步：在协同条目里找关键词（例如「和硫磺火有协同的道具」）
        if search_by_synergy and len(hits) < wanted and len(normalized) >= 2:
            matched_ids = {hit.item.id for hit in hits}
            for item in pool:
                if item.id in matched_ids or not item.synergies:
                    continue
                if not any(
                    normalized in normalize(synergy_name) or normalized in normalize(synergy_effect)
                    for synergy_name, synergy_effect in item.synergies
                ):
                    continue
                score = 120.0 + min(item.quality, 4) * 5
                hits.append(SearchHit(item, score, "synergy"))

        if not hits and fuzzy_threshold > 0:
            hits = self._fuzzy_search(pool, normalized, fuzzy_threshold)

        hits.sort(key=lambda hit: (-hit.score, -hit.item.quality, len(hit.item.name), hit.item.id))
        deduped: List[SearchHit] = []
        seen: set[str] = set()
        for hit in hits:
            if hit.item.id in seen:
                continue
            seen.add(hit.item.id)
            deduped.append(hit)
            if len(deduped) >= wanted:
                break
        return deduped

    @staticmethod
    def _score_item(item: Item, normalized: str) -> Tuple[float, str]:
        """给单条记录打分（0 表示不匹配）。"""

        name = item.name_norm
        en = item.en_norm
        if name and name == normalized:
            return 1000.0, "name"
        if en and en == normalized:
            return 980.0, "en"
        if name and name.startswith(normalized):
            return 900.0 - min(len(name) - len(normalized), 60) * 0.5, "name_prefix"
        if name and normalized in name:
            return 800.0 - min(name.index(normalized), 60) * 0.5, "name_contains"
        if en and en.startswith(normalized):
            return 700.0, "en_prefix"
        if en and normalized in en:
            return 600.0, "en_contains"
        return 0.0, ""

    @staticmethod
    def _fuzzy_search(pool: Sequence[Item], normalized: str, threshold: float) -> List[SearchHit]:
        """近似匹配：用于错别字或记不全名字的场景。"""

        hits: List[SearchHit] = []
        for item in pool:
            candidates = [item.name_norm] + ([item.en_norm] if item.en_norm else [])
            best = max((SequenceMatcher(None, normalized, candidate).ratio() for candidate in candidates), default=0.0)
            if best >= threshold:
                hits.append(SearchHit(item, 200.0 + best * 100, "fuzzy"))
        return hits


# ----------------------------------------------------------------------
# 排版
# ----------------------------------------------------------------------


def _truncate(text: str, limit: int, *, silent: bool = False) -> str:
    """按字数截断（``limit <= 0`` 表示不截断；``silent`` 不追加说明）。"""

    if limit <= 0 or len(text) <= limit:
        return text
    if silent:
        return text[:limit].rstrip() + "…"
    return text[:limit].rstrip() + f"…（已截断，共 {len(text)} 字）"


def looks_truncated(effect: str) -> bool:
    """判断效果正文是否像是「后面本该有表格/列表」但被截断了。"""

    text = (effect or "").strip()
    if not text:
        return False
    return bool(_TRUNCATED_TAIL_RE.search(text))


def format_table(table: Dict[str, Any], *, limit: int = 0) -> str:
    """把补录的表格排版成聊天可读的逐行文本（``limit > 0`` 时只列前若干行）。"""

    title = str(table.get("title") or "").strip()
    columns = [str(column).strip() for column in (table.get("columns") or []) if str(column).strip()]
    header = f"{title}：" if title else "表格："
    if columns:
        header = f"{title}（表：{' ｜ '.join(columns)}）：" if title else f"表格（{' ｜ '.join(columns)}）："
    lines = [header]
    rows = list(table.get("rows") or [])
    total = len(rows)
    if limit > 0:
        rows = rows[:limit]
    for row in rows:
        cells = [str(cell).strip() for cell in row]
        cells = [cell if cell and cell != "/" else "-" for cell in cells]
        lines.append("· " + " ｜ ".join(cells))
    if limit > 0 and total > len(rows):
        lines.append(f"（共 {total} 行，此处列出前 {len(rows)} 行）")
    return "\n".join(lines)


def format_synergy_lines(entries: Sequence[Tuple[str, str]]) -> str:
    """把协同条目排版成 ``· 名称：效果`` 的逐行文本。"""

    lines: List[str] = []
    for synergy_name, synergy_effect in entries:
        line = f"· {synergy_name}"
        if synergy_effect:
            line += f"：{synergy_effect}"
        lines.append(line)
    return "\n".join(lines)


def format_item_detail(
    item: Item,
    display: DisplayConfig,
    *,
    synergy_overflow: str = SYNERGY_OVERFLOW_FORWARD,
    extra_limit: int = 0,
) -> DetailOutput:
    """把一条图鉴条目排版成聊天可读的详情。

    ``synergy_overflow`` 决定协同条数超过 ``display.max_synergies`` 时的行为：
    ``forward`` 表示正文只留提示、全部协同由调用方用合并转发发送；``truncate`` 表示
    只列前 N 条（LLM 工具等无法发合并转发的场景）。

    ``extra_limit > 0`` 时，补录的表格行与补充说明只列前若干条（同样用于工具路径控长）。
    """

    title = f"【{item.name}】"
    if display.show_english and item.en:
        title += f" {item.en}"
    lines = [title]

    meta_parts: List[str] = []
    if display.show_quality and item.quality >= 0:
        meta_parts.append(f"品质 {item.quality_label}")
    meta_parts.append(item.kind)
    if item.id:
        meta_parts.append(f"编号 {item.id}")
    lines.append(" · ".join(meta_parts))

    if display.show_tags and item.tags:
        lines.append("标签：" + " / ".join(item.tags))

    if item.has_effect:
        lines.append("效果：")
        lines.append(_truncate(item.effect, display.max_effect_chars))
    elif item.kind == "道具":
        lines.append("效果：数据源没有抓到这条的效果文本。")
    else:
        lines.append(f"效果：数据源只收录了该{item.kind}的名称（上游只对「道具」抓到了完整效果文本）。")

    if item.extra_table:
        lines.append(format_table(item.extra_table, limit=extra_limit))
    elif not item.extra_lines and display.show_wiki_link and item.has_effect and looks_truncated(item.effect):
        # 上游爬虫不解析 <table>，这类条目在本地是断句，且没有补录内容，给出去 wiki 看原文的入口
        lines.append(f"（此条在 wiki 中以表格/列表给出，本地数据源未收录，详见 {item.wiki_url}）")

    if item.extra_lines:
        lines.append("补充：")
        shown_lines = item.extra_lines[:extra_limit] if extra_limit > 0 else item.extra_lines
        lines.extend(f"· {line}" for line in shown_lines)
        if len(shown_lines) < len(item.extra_lines):
            lines.append(f"（共 {len(item.extra_lines)} 条，此处列出前 {len(shown_lines)} 条）")

    if item.transformations:
        lines.append("套装：")
        for transformation in item.transformations:
            count = transformation.get("count") or 0
            suffix = f"（集齐 {count} 件）" if count else ""
            effect = str(transformation.get("effect") or "").strip()
            line = f"· {transformation.get('name')}{suffix}"
            if effect:
                line += f"：{effect}"
            lines.append(line)

    forward_text = ""
    if display.show_synergies and item.synergies:
        total = max(item.synergy_total, len(item.synergies))
        limit = max(int(display.max_synergies), 0)
        if limit == 0 or total <= limit:
            # 不超过上限：全部内联展示
            lines.append(f"协同（共 {total} 条）：")
            lines.append(format_synergy_lines(item.synergies))
        elif synergy_overflow == SYNERGY_OVERFLOW_TRUNCATE:
            shown = item.synergies[:limit]
            lines.append(f"协同（共 {total} 条，此处列出前 {len(shown)} 条）：")
            lines.append(format_synergy_lines(shown))
        else:
            # 超过上限：不折叠，正文留提示，全部协同交给调用方用合并转发发送
            lines.append(f"协同（共 {total} 条）：全部协同见下方合并转发。")
            forward_text = format_synergies(item, display)

    return DetailOutput(text="\n".join(lines), forward_text=forward_text)


def format_search_hits(hits: Sequence[SearchHit], query: str, display: DisplayConfig) -> str:
    """把检索结果排版成列表。"""

    lines = [f"「{query}」找到 {len(hits)} 条："]
    for index, hit in enumerate(hits, 1):
        item = hit.item
        parts = [f"{index}. 【{item.name}】"]
        if display.show_english and item.en:
            parts.append(f" {item.en}")
        tail: List[str] = []
        if display.show_quality and item.quality >= 0:
            tail.append(f"品质 {item.quality_label}")
        tail.append(item.kind)
        if item.id:
            tail.append(f"编号 {item.id}")
        if display.show_tags and item.tags:
            tail.append("标签 " + "/".join(item.tags[:3]))
        if item.has_effect:
            summary = " ".join(item.effect.split())
            tail.append(_truncate(summary, 60, silent=True))
        lines.append("".join(parts) + "（" + " · ".join(tail) + "）")
    lines.append("用 /以撒详情 <名称> 查看完整效果，/以撒随机 抽一个道具。")
    return "\n".join(lines)


def format_synergies(item: Item, display: DisplayConfig) -> str:
    """排版某个道具的全部协同。"""

    if not item.synergies:
        return f"【{item.name}】在数据源里没有记录协同条目。"
    total = max(item.synergy_total, len(item.synergies))
    header = f"【{item.name}】协同共 {total} 条"
    header += f"（已收录 {len(item.synergies)} 条）：" if total > len(item.synergies) else "："
    lines = [header]
    for synergy_name, synergy_effect in item.synergies:
        line = f"· {synergy_name}"
        if synergy_effect:
            line += f"：{synergy_effect}"
        lines.append(line)
    return "\n".join(lines)


def build_help_text(*, source_note: str = "", forward_threshold_note: str = "") -> str:
    """构建帮助文本。"""

    lines = [
        "以撒的结合 道具图鉴",
        "",
        "用法：",
        "/以撒 <关键词>    查询条目，支持中文名、英文名、编号、标签与效果关键词",
        "/以撒详情 <关键词> 只看完整效果与协同",
        "/以撒协同 <关键词> 查看该道具的全部协同",
        "/以撒编号 <编号>  按图鉴编号查询",
        "/以撒随机 [分类]  随机抽一个，分类可选 道具/饰品/卡牌/药丸",
        "",
        "示例：/以撒 硫磺火 ｜ /以撒 The Sad Onion ｜ /以撒 118 ｜ /以撒 射速",
    ]
    if source_note:
        lines.extend(["", f"数据来源：{source_note}"])
    if forward_threshold_note:
        lines.extend([forward_threshold_note])
    return "\n".join(lines)
