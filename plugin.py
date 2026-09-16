"""cateye_isaac_item —— 《以撒的结合》道具图鉴查询插件。

提供两种触发方式：

1. ``@Command``：聊天里发 ``/以撒 硫磺火``、``/以撒详情 1``、``/以撒随机 饰品`` 等；
2. ``@Tool``：让 LLM 在正常对话中自行查询（``isaac_item_lookup``）。

数据是打包在插件 ``assets/isaac_items.json`` 里的离线快照，由 ``tools/build_data.py``
从上游开源项目生成，运行时不联网。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional, Tuple

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import CONFIG_RELOAD_SCOPE_SELF, ToolParameterInfo, ToolParamType

try:  # 插件作为包被导入时走相对导入
    from .isaac_data import (
        KINDS,
        KIND_ALIASES,
        SYNERGY_OVERFLOW_FORWARD,
        SYNERGY_OVERFLOW_TRUNCATE,
        DisplayConfig,
        Item,
        ItemDatabase,
        build_help_text,
        format_item_detail,
        format_search_hits,
        format_synergies,
    )
except ImportError:  # 插件目录被直接加入 sys.path 时走绝对导入
    from isaac_data import (  # type: ignore[no-redef]
        KINDS,
        KIND_ALIASES,
        SYNERGY_OVERFLOW_FORWARD,
        SYNERGY_OVERFLOW_TRUNCATE,
        DisplayConfig,
        Item,
        ItemDatabase,
        build_help_text,
        format_item_detail,
        format_search_hits,
        format_synergies,
    )

SUPPORTED_CONFIG_VERSION = "0.1.4"

_PLUGIN_DIR = Path(__file__).resolve().parent
_DEFAULT_DATA_FILE = _PLUGIN_DIR / "assets" / "isaac_items.json"
_FORWARD_NICKNAME = "以撒图鉴"

_SUBCOMMAND_PREFIXES: Tuple[str, ...] = ("详情", "协同", "编号")
_HELP_KEYWORDS = {"帮助", "help", "?", "？", "菜单", "说明", "指令"}
# LLM 工具在 detail=False 时，补录表格/补充说明最多预览几行（避免把长列表塞进模型上下文）
_TOOL_EXTRA_PREVIEW_LINES = 3


@dataclass(frozen=True)
class ReplyPayload:
    """一次查询的结果：正文 + 是否命中 + 需要额外合并转发的协同全文。"""

    text: str
    matched: bool
    forward_text: str = ""


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="配置版本（与插件版本同步）",
        json_schema_extra={"hidden": True, "disabled": True},
    )


class SearchSectionConfig(PluginConfigBase):
    """检索行为配置。"""

    __ui_label__ = "检索"
    __ui_icon__ = "search"
    __ui_order__ = 1

    max_results: int = Field(default=5, description="列表模式最多返回多少条结果（1~20）")
    fuzzy_threshold: float = Field(
        default=0.55,
        description="模糊匹配相似度阈值（0~1）：名字记不全或打错字时的兜底，越低越宽松；填 0 关闭模糊匹配",
    )
    search_by_tag: bool = Field(
        default=True,
        description="名称匹配不到时，按标签检索（例如「射速」「攻击性」）",
    )
    search_by_effect: bool = Field(
        default=True,
        description="名称与标签都匹配不到时，在效果正文里找关键词（例如「飞行」「中毒」）",
    )
    search_by_synergy: bool = Field(
        default=True,
        description="以上都匹配不到时，在协同条目里找关键词（例如搜「虚空」能搜到与虚空有协同的道具）",
    )
    max_query_length: int = Field(default=32, description="单次查询关键词的最大长度，超出部分截断")


class DisplaySectionConfig(PluginConfigBase):
    """回复展示配置。"""

    __ui_label__ = "展示"
    __ui_icon__ = "visibility"
    __ui_order__ = 2

    show_english: bool = Field(default=True, description="是否显示英文名（部分条目该字段为英文风味文本）")
    show_quality: bool = Field(default=True, description="是否显示品质等级（0~4）")
    show_tags: bool = Field(default=True, description="是否显示标签")
    show_synergies: bool = Field(default=True, description="详情里是否显示协同条目")
    max_synergies: int = Field(
        default=5,
        description="详情里内联展示的协同条数上限：超过该条数时不再折叠，全部协同改用合并转发发送（0 表示全部内联显示）",
    )
    max_synergies_in_tool: int = Field(
        default=20,
        description="LLM 工具返回时最多列出的协同条数（工具无法发合并转发，只能截断；0 表示全部列出）",
    )
    max_effect_chars: int = Field(default=0, description="效果文本最大字数，0 表示不截断")
    show_wiki_link: bool = Field(
        default=True,
        description="效果疑似被表格截断（且本地没有补录）时，是否附上灰机 wiki 页面链接",
    )
    forward_threshold: int = Field(
        default=1000,
        description="整条回复超过该字数时改用合并转发发送，避免刷屏；0 表示始终用普通文本",
    )


class DataSectionConfig(PluginConfigBase):
    """数据来源配置。"""

    __ui_label__ = "数据"
    __ui_icon__ = "database"
    __ui_order__ = 3

    data_file: str = Field(
        default="",
        description="自定义数据文件路径（留空使用插件内置的 assets/isaac_items.json）",
    )
    show_source_in_help: bool = Field(default=True, description="帮助信息里是否附带数据来源与统计")


class IsaacItemConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    search: SearchSectionConfig = Field(default_factory=SearchSectionConfig)
    display: DisplaySectionConfig = Field(default_factory=DisplaySectionConfig)
    data: DataSectionConfig = Field(default_factory=DataSectionConfig)


class IsaacItemPlugin(MaiBotPlugin):
    """《以撒的结合》道具图鉴查询插件。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = IsaacItemConfig

    def __init__(self) -> None:
        # 必须调用基类构造：SDK 依赖基类里的 _ctx / _dynamic_api_components 等状态
        super().__init__()
        self._db: Optional[ItemDatabase] = None
        self._load_error: str = ""
        self._data_path: str = ""

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def on_load(self) -> None:
        await self._load_database()
        if self._db is not None:
            self.ctx.logger.info("以撒图鉴已加载：%s（%s）", self._db.stats(), self._data_path)
        else:
            self.ctx.logger.error("以撒图鉴数据加载失败：%s", self._load_error)

    async def on_unload(self) -> None:
        self._db = None
        self.ctx.logger.info("插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            # 数据文件路径可能被改过，重新加载一次；其余配置在每次查询时实时读取。
            await self._load_database()
            self.ctx.logger.info("插件配置已热更新（version=%s），数据：%s", version, self._data_path)
        else:
            self.ctx.logger.debug("收到非本插件配置更新（scope=%s），忽略", scope)

    # ------------------------------------------------------------------
    # 数据与配置
    # ------------------------------------------------------------------

    def _resolve_data_path(self) -> Path:
        raw = str(getattr(self.config.data, "data_file", "") or "").strip()
        if raw:
            return Path(raw).expanduser()
        return _DEFAULT_DATA_FILE

    async def _load_database(self) -> None:
        path = self._resolve_data_path()
        try:
            database = await asyncio.to_thread(ItemDatabase.load, path)
        except Exception as exc:
            self._db = None
            self._load_error = str(exc)
            self._data_path = str(path)
            self.ctx.logger.error("读取数据文件失败：%s（%s）", path, exc)
            return
        self._db = database
        self._load_error = ""
        self._data_path = str(path)

    def _display_config(self, *, for_tool: bool = False) -> DisplayConfig:
        display = self.config.display
        # 聊天路径：协同超过上限时改用合并转发；工具路径发不了转发，只能截断
        max_synergies = (
            max(int(display.max_synergies_in_tool), 0) if for_tool else max(int(display.max_synergies), 0)
        )
        return DisplayConfig(
            show_english=bool(display.show_english),
            show_quality=bool(display.show_quality),
            show_tags=bool(display.show_tags),
            show_synergies=bool(display.show_synergies),
            max_synergies=max_synergies,
            max_effect_chars=max(int(display.max_effect_chars), 0),
            show_wiki_link=bool(display.show_wiki_link),
        )

    def _search_kwargs(self) -> Dict[str, Any]:
        search = self.config.search
        return {
            "limit": min(max(int(search.max_results), 1), 20),
            "fuzzy_threshold": max(min(float(search.fuzzy_threshold), 1.0), 0.0),
            "search_by_tag": bool(search.search_by_tag),
            "search_by_effect": bool(search.search_by_effect),
            "search_by_synergy": bool(search.search_by_synergy),
        }

    def _max_query_length(self) -> int:
        return max(int(self.config.search.max_query_length), 1)

    # ------------------------------------------------------------------
    # 文本组装
    # ------------------------------------------------------------------

    def _help_text(self) -> str:
        source_note = ""
        if self._db is None:
            source_note = f"数据未加载：{self._load_error or '未知原因'}"
        elif bool(self.config.data.show_source_in_help):
            meta = self._db.meta
            source = str(meta.get("source") or "内置数据")
            source_url = str(meta.get("source_url") or "")
            source_note = f"{source}（{source_url}）" if source_url else source
            source_note += f"\n数据规模：{self._db.counts_line()}"
        return build_help_text(source_note=source_note)

    def _unavailable_text(self) -> str:
        return (
            "道具数据没有加载成功，暂时无法查询。\n"
            f"原因：{self._load_error or '未知'}\n"
            "可在 WebUI 插件配置的「数据」节检查数据文件路径，或重新加载插件。"
        )

    def _build_reply(
        self,
        raw_query: str,
        *,
        force_detail: bool = False,
        for_tool: bool = False,
    ) -> ReplyPayload:
        """把一次查询请求转成待发送内容。

        ``matched`` 为 False 时表示这条消息可能只是普通聊天（例如「以撒的结合真好玩」），
        调用方据此决定要不要回复。``for_tool=True`` 时协同只截断不转发（工具发不了转发）。
        """

        database = self._db
        if database is None:
            return ReplyPayload(self._unavailable_text(), True)

        query = " ".join(str(raw_query or "").split())[: self._max_query_length()]
        if not query:
            return ReplyPayload(self._help_text(), True)
        if query.lower() in _HELP_KEYWORDS:
            return ReplyPayload(self._help_text(), True)

        if query.startswith("随机"):
            return self._random_reply(
                database,
                query[len("随机") :].strip(),
                for_tool=for_tool,
                full_extra=force_detail,
            )
        for prefix in _SUBCOMMAND_PREFIXES:
            if query.startswith(prefix):
                rest = query[len(prefix) :].strip()
                if prefix == "详情":
                    return self._detail_reply(database, rest, for_tool=for_tool, full_extra=force_detail)
                if prefix == "协同":
                    return self._synergy_reply(database, rest)
                return self._id_reply(database, rest, for_tool=for_tool, full_extra=force_detail)
        if force_detail:
            return self._detail_reply(database, query, for_tool=for_tool, full_extra=True)
        return self._search_reply(database, query, for_tool=for_tool, full_extra=force_detail)

    def _item_detail_payload(
        self,
        item: Item,
        *,
        for_tool: bool = False,
        full_extra: bool = False,
    ) -> ReplyPayload:
        """把条目详情转成待发送内容（协同超限时带上合并转发全文）。

        ``for_tool`` 为真时走工具路径：协同只截断不转发；补录的表格与补充说明在未要求
        完整内容（``detail=false``）时只预览前几行，聊天路径与 ``detail=true`` 输出全部。
        """

        overflow = SYNERGY_OVERFLOW_TRUNCATE if for_tool else SYNERGY_OVERFLOW_FORWARD
        extra_limit = 0 if (not for_tool or full_extra) else _TOOL_EXTRA_PREVIEW_LINES
        detail = format_item_detail(
            item,
            self._display_config(for_tool=for_tool),
            synergy_overflow=overflow,
            extra_limit=extra_limit,
        )
        return ReplyPayload(text=detail.text, matched=True, forward_text=detail.forward_text)

    def _search_reply(
        self,
        database: ItemDatabase,
        query: str,
        *,
        for_tool: bool = False,
        full_extra: bool = False,
    ) -> ReplyPayload:
        hits = database.search(query, **self._search_kwargs())
        if not hits:
            return ReplyPayload(
                f"没找到和「{query}」相关的条目。\n"
                "可以试试中文名、英文名、编号，或者 /以撒随机 抽一个。",
                False,
            )
        # 精确命中名称/编号时直接给详情，避免多一步
        if len(hits) == 1 or hits[0].score >= 1000:
            return self._item_detail_payload(hits[0].item, for_tool=for_tool, full_extra=full_extra)
        return ReplyPayload(format_search_hits(hits, query, self._display_config(for_tool=for_tool)), True)

    def _detail_reply(
        self,
        database: ItemDatabase,
        query: str,
        *,
        for_tool: bool = False,
        full_extra: bool = False,
    ) -> ReplyPayload:
        if not query:
            return ReplyPayload("用法：/以撒详情 <道具名或编号>，例如 /以撒详情 硫磺火", True)
        hits = database.search(query, **self._search_kwargs())
        if not hits:
            return ReplyPayload(f"没找到和「{query}」相关的条目，可以换个写法或用 /以撒 {query} 试试。", False)
        payload = self._item_detail_payload(hits[0].item, for_tool=for_tool, full_extra=full_extra)
        others = [hit.item.name for hit in hits[1:]]
        if not others:
            return payload
        return ReplyPayload(
            text=payload.text + "\n其它匹配：" + "、".join(others) + "（可写更精确的名字再看）",
            matched=True,
            forward_text=payload.forward_text,
        )

    def _synergy_reply(self, database: ItemDatabase, query: str) -> ReplyPayload:
        if not query:
            return ReplyPayload("用法：/以撒协同 <道具名或编号>，例如 /以撒协同 硫磺火", True)
        hits = database.search(query, **self._search_kwargs())
        if not hits:
            return ReplyPayload(f"没找到和「{query}」相关的条目。", False)
        return ReplyPayload(format_synergies(hits[0].item, self._display_config()), True)

    def _id_reply(
        self,
        database: ItemDatabase,
        query: str,
        *,
        for_tool: bool = False,
        full_extra: bool = False,
    ) -> ReplyPayload:
        if not query:
            return ReplyPayload("用法：/以撒编号 <图鉴编号>，例如 /以撒编号 118", True)
        digits = "".join(char for char in query if char.isdigit())
        if not digits:
            return ReplyPayload("编号只能是数字，例如 /以撒编号 118。", True)
        item = database.get(digits)
        if item is None:
            return ReplyPayload(f"图鉴里没有编号为 {digits} 的条目。", True)
        return self._item_detail_payload(item, for_tool=for_tool, full_extra=full_extra)

    def _random_reply(
        self,
        database: ItemDatabase,
        rest: str,
        *,
        for_tool: bool = False,
        full_extra: bool = False,
    ) -> ReplyPayload:
        kind: Optional[str] = None
        if rest:
            kind = KIND_ALIASES.get(rest.strip().lower()) or KIND_ALIASES.get(rest.strip())
            if kind is None:
                return ReplyPayload("分类只能是：" + "、".join(KINDS) + "。例如 /以撒随机 饰品", True)
        item = database.random_item(kind)
        if item is None:
            return ReplyPayload("这个分类下没有可用的条目。", True)
        return self._item_detail_payload(item, for_tool=for_tool, full_extra=full_extra)

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

    async def _send_reply(self, stream_id: str, text: str, forward_text: str = "") -> None:
        """发送回复。

        正文按长度选择普通文本或合并转发；``forward_text`` 非空时（协同条数超过内联上限）
        随后用合并转发补发完整协同，合并转发不可用时回退为普通文本。
        """

        if not stream_id:
            if text or forward_text:
                self.ctx.logger.warning("缺少 stream_id，无法发送回复")
            return

        if text:
            threshold = max(int(self.config.display.forward_threshold), 0)
            sent = False
            if threshold and len(text) > threshold:
                sent = await self._send_forward(stream_id, text)
            if not sent:
                await self.ctx.send.text(text, stream_id)

        if forward_text:
            if not await self._send_forward(stream_id, forward_text):
                await self.ctx.send.text(forward_text, stream_id)

    async def _send_forward(self, stream_id: str, text: str) -> bool:
        """长内容用单条合并转发发送，失败返回 False 交由调用方回退。"""

        try:
            await self.ctx.send.forward(
                [
                    {
                        "user_id": "0",
                        "nickname": _FORWARD_NICKNAME,
                        "segments": [{"type": "text", "content": text}],
                    }
                ],
                stream_id,
            )
            return True
        except Exception as exc:
            self.ctx.logger.warning("合并转发失败，回退为普通文本：%s", exc)
            return False

    # ------------------------------------------------------------------
    # 指令
    # ------------------------------------------------------------------

    @Command(
        "isaac_item_query",
        description="查询《以撒的结合》道具图鉴（名称/编号/效果/协同）",
        pattern=r"(?<!\S)/?(?:以撒|isaac)(?:图鉴|道具)?\s*(?P<query>[\s\S]*?)\s*$",
    )
    async def cmd_isaac_item(self, **kwargs: Any) -> tuple[bool, str, bool]:
        stream_id = str(kwargs.get("stream_id") or "")
        matched = kwargs.get("matched_groups")
        query = ""
        if isinstance(matched, dict):
            query = str(matched.get("query") or "")

        raw_text = str(kwargs.get("text") or "")
        # 显式带 / 前缀时（/以撒 …）无条件回答；不带 / 时只在真正命中图鉴条目时回答，
        # 避免把「以撒的结合真好玩」这类普通聊天误当成指令。
        explicit = "/以撒" in raw_text or "/isaac" in raw_text.lower()

        payload = self._build_reply(query)
        if not payload.matched and not explicit:
            self.ctx.logger.debug("以撒图鉴未命中且无 / 前缀，交回正常聊天流程：%s", raw_text[:40])
            return False, None, False

        await self._send_reply(stream_id, payload.text, payload.forward_text)
        return True, payload.text, True

    # ------------------------------------------------------------------
    # LLM 工具
    # ------------------------------------------------------------------

    @Tool(
        "isaac_item_lookup",
        brief_description="查询《以撒的结合》道具图鉴：中文名、英文名、编号、品质、效果、协同",
        detailed_description=(
            "查询《以撒的结合》的道具/饰品/卡牌/药丸资料，数据来自离线图鉴快照。\n"
            "- query：必填。可以是中文名（硫磺火）、英文名（The Sad Onion）、图鉴编号（118），"
            "也可以是效果关键词（射速、飞行、中毒）。\n"
            "- detail：可选。true 返回完整效果、协同与补录的表格；"
            "false（默认）在匹配到多个条目时返回候选列表，补录表格只预览前几行。\n"
            "返回的 content 已排版好，可以直接引用；不要凭记忆补充返回内容之外的道具效果。"
        ),
        parameters=[
            ToolParameterInfo(
                name="query",
                param_type=ToolParamType.STRING,
                description="道具名称、图鉴编号或效果关键词",
                required=True,
            ),
            ToolParameterInfo(
                name="detail",
                param_type=ToolParamType.BOOLEAN,
                description="是否直接返回完整效果与协同，默认 false",
                required=False,
                default=False,
            ),
        ],
    )
    async def tool_isaac_item_lookup(
        self,
        query: str = "",
        detail: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # 工具发不了合并转发，协同超限时按 max_synergies_in_tool 截断并注明条数
        payload = self._build_reply(query, force_detail=bool(detail), for_tool=True)
        return {
            "content": payload.text,
            "query": query,
            "detail": bool(detail),
            "matched": payload.matched,
            "loaded": self._db is not None,
            "total": len(self._db) if self._db is not None else 0,
        }


def create_plugin() -> IsaacItemPlugin:
    """Runner 加载入口。"""

    return IsaacItemPlugin()
