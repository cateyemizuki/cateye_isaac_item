"""cateye_isaac_item —— 《以撒的结合》道具图鉴查询插件。

提供两种触发方式：

1. ``@Command``：聊天里发 ``/以撒 硫磺火``、``/以撒详情 1``、``/以撒存档`` 等；
2. ``@Tool``：让 LLM 在正常对话中自行查询（``isaac_item_lookup``）。

数据是打包在插件 ``assets/isaac_items.json`` 里的离线快照，由 ``tools/build_data.py``
从上游开源项目生成，运行时不联网。

存档解析（``/以撒存档``）走的是**用户主动上传**的路线：群里引用 / 发送 .dat 文件 →
插件下载校验后按 QQ 号缓存到本地 → 需要时解析并回图 + 合并转发详情，全程只读。
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple

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
    from .isaac_save import (
        NameTables,
        SaveFormatError,
        SaveReport,
        SaveStore,
        build_report,
        build_upload_guide,
        extract_file_reference,
        format_cache_list,
        format_report_text,
        format_summary_text,
        parse_save_bytes,
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
    from isaac_save import (  # type: ignore[no-redef]
        NameTables,
        SaveFormatError,
        SaveReport,
        SaveStore,
        build_report,
        build_upload_guide,
        extract_file_reference,
        format_cache_list,
        format_report_text,
        format_summary_text,
        parse_save_bytes,
    )

try:  # 渲染模块可选：只用标准库也能跑（此时解析图自动降级为纯文字）
    from .isaac_save_render import build_card_png, canvas_available, using_bundled_font
except ImportError:  # pragma: no cover
    try:
        from isaac_save_render import build_card_png, canvas_available, using_bundled_font  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover - 极端情况下只要文本
        build_card_png = None  # type: ignore[assignment]

        def canvas_available() -> bool:  # type: ignore[misc]
            return False

        def using_bundled_font() -> bool:  # type: ignore[misc]
            return False

SUPPORTED_CONFIG_VERSION = "0.2.3"

_PLUGIN_DIR = Path(__file__).resolve().parent
_DEFAULT_DATA_FILE = _PLUGIN_DIR / "assets" / "isaac_items.json"
_DEFAULT_SAVE_NAMES_FILE = _PLUGIN_DIR / "assets" / "isaac_save_names.json"
_DEFAULT_ACHIEVEMENT_TABLE_FILE = _PLUGIN_DIR / "assets" / "isaac_achievements_zh.json"
_FORWARD_NICKNAME = "以撒图鉴"
_SAVE_FORWARD_NICKNAME = "以撒存档解析"

_SUBCOMMAND_PREFIXES: Tuple[str, ...] = ("详情", "协同", "编号")
_HELP_KEYWORDS = {"帮助", "help", "?", "？", "菜单", "说明", "指令"}
# LLM 工具在 detail=False 时，补录表格/补充说明最多预览几行（避免把长列表塞进模型上下文）
_TOOL_EXTRA_PREVIEW_LINES = 3

#: 存档相关指令的固定写法（错误提示里反复用到）
_SAVE_COMMAND = "/以撒存档"
_SAVE_GUIDE_ACTIONS = {"帮助", "说明", "help"}
_SAVE_BIND_ACTIONS = {"绑定", "上传", "更新", "bind"}
_SAVE_CLEAR_ACTIONS = {"清除", "删除", "解绑", "clear"}
_SAVE_LIST_ACTIONS = {"列表", "状态", "list"}
_SAVE_TEXT_ONLY_ACTIONS = {"详情", "文本", "text"}

#: 下载存档时的 User-Agent（QQ 文件 CDN 对空 UA 偶有不友好）
_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

#: 不允许下载的内网网段判定（SSRF 防护）
_BLOCKED_IP_FLAGS = ("is_private", "is_loopback", "is_link_local", "is_reserved", "is_multicast", "is_unspecified")

#: 群文件相关 Action（Napcat 适配器透传 API）
_GROUP_ROOT_FILES_API = "adapter.napcat.file.get_group_root_files"
_GROUP_FILE_URL_API = "adapter.napcat.file.get_group_file_url"



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


class SaveSectionConfig(PluginConfigBase):
    """存档解析配置（/以撒存档）。"""

    __ui_label__ = "存档"
    __ui_icon__ = "save"
    __ui_order__ = 4

    enabled: bool = Field(default=True, description="是否启用存档解析（/以撒存档 系列指令）")
    auto_analyze_after_bind: bool = Field(
        default=True,
        description="用户绑定存档后，是否立即返回解析图与详情（关闭则只回复绑定结果）",
    )
    send_image: bool = Field(default=True, description="是否发送解析图（渲染不可用时自动回退为纯文字）")
    image_width: int = Field(
        default=700,
        description="解析图卡片宽度（600~1000 CSS 像素）：调小则排版收窄、长宽比变瘦高（推荐 700~800）",
    )
    image_scale: float = Field(
        default=1.4,
        description="解析图输出倍率（1~3）：最终像素 = 卡片尺寸 × 该倍率，觉得整体太大就调小",
    )
    max_image_chips: int = Field(default=24, description="解析图里最多列出多少个未发现道具")
    max_image_track_chips: int = Field(
        default=12,
        description="解析图里最多列出多少个未解锁成就 / 挑战的名称（0 表示不在图里列出）",
    )
    detail_max_items: int = Field(default=60, description="合并转发详情里最多逐个列出多少个未发现道具")
    detail_max_named: int = Field(
        default=40,
        description="合并转发详情里最多逐个列出多少个未解锁成就 / 挑战（带中文名与解锁条件）",
    )
    max_file_mb: float = Field(default=4.0, description="允许解析的存档文件大小上限（MB）")
    cache_ttl_days: int = Field(default=0, description="本地存档缓存有效期（天），0 表示永久保留直到用户清除")
    download_timeout_sec: int = Field(default=30, description="下载存档文件的超时时间（秒）")
    allow_private_url: bool = Field(
        default=False,
        description="是否允许从内网 / 回环地址下载存档（默认关闭：引用消息可被伪造，开启后可能被指向内网服务）",
    )
    lookup_group_files: bool = Field(
        default=True,
        description="消息里没有下载链接时，是否按文件名去群文件列表里查找并取下载链接",
    )
    name_table_file: str = Field(
        default="",
        description="成就 / BOSS / 挑战中文名称表路径（留空使用内置 assets/isaac_save_names.json）",
    )
    achievement_table_file: str = Field(
        default="",
        description="成就详情表路径（含解锁条件；留空使用内置 assets/isaac_achievements_zh.json）",
    )


class IsaacItemConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    search: SearchSectionConfig = Field(default_factory=SearchSectionConfig)
    display: DisplaySectionConfig = Field(default_factory=DisplaySectionConfig)
    data: DataSectionConfig = Field(default_factory=DataSectionConfig)
    save: SaveSectionConfig = Field(default_factory=SaveSectionConfig)


@dataclass(frozen=True)
class SaveFileRef:
    """从消息里找到的存档文件引用。"""

    url: str = ""
    name: str = ""
    file_id: str = ""
    hint: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.url or self.name)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """跟随重定向前重新校验目标地址，避免用 302 绕到内网。"""

    def __init__(self, validator: Any) -> None:
        super().__init__()
        self._validator = validator

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)



class IsaacItemPlugin(MaiBotPlugin):
    """《以撒的结合》道具图鉴查询插件。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = IsaacItemConfig

    def __init__(self) -> None:
        # 必须调用基类构造：SDK 依赖基类里的 _ctx / _dynamic_api_components 等状态
        super().__init__()
        self._db: Optional[ItemDatabase] = None
        self._load_error: str = ""
        self._data_path: str = ""
        self._save_store: Optional[SaveStore] = None
        self._save_root: str = ""
        self._name_tables: NameTables = NameTables()
        self._name_table_path: str = ""
        self._item_names: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def on_load(self) -> None:
        await self._load_database()
        if self._db is not None:
            self.ctx.logger.info("以撒图鉴已加载：%s（%s）", self._db.stats(), self._data_path)
        else:
            self.ctx.logger.error("以撒图鉴数据加载失败：%s", self._load_error)
        self._setup_save_store()

    async def on_unload(self) -> None:
        self._db = None
        self._save_store = None
        self.ctx.logger.info("插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            # 数据文件路径可能被改过，重新加载一次；其余配置在每次查询时实时读取。
            await self._load_database()
            self._setup_save_store()
            self.ctx.logger.info(
                "插件配置已热更新（version=%s），数据：%s，存档缓存：%s",
                version,
                self._data_path,
                self._save_root,
            )
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
            self._item_names = {}
            self.ctx.logger.error("读取数据文件失败：%s（%s）", path, exc)
            return
        self._db = database
        self._load_error = ""
        self._data_path = str(path)
        # 编号 → 名称的映射随数据集变化，重新加载后要重建
        self._item_names = {}

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
    # 存档解析：配置装配 / 取存
    # ------------------------------------------------------------------

    def _setup_save_store(self) -> None:
        """装配存档缓存与名称表（``on_load`` 与配置热更新时调用）。"""

        try:
            data_dir = Path(self.ctx.paths.data_dir)
        except Exception as exc:  # noqa: BLE001 - 拿不到数据目录时功能降级
            self._save_store = None
            self.ctx.logger.error("无法获取插件数据目录，存档解析不可用：%s", exc)
            return

        save = self.config.save
        root = data_dir / "saves"
        max_bytes = int(max(float(save.max_file_mb), 0.1) * 1024 * 1024)
        store = SaveStore(root, max_bytes=max_bytes)
        self._save_store = store
        self._save_root = str(root)
        try:
            pruned = store.prune(ttl_days=int(save.cache_ttl_days))
            stats = store.stats()
            self.ctx.logger.info(
                "存档缓存就绪：%s（用户 %d 个 / 存档 %d 份 / %.0f KB，本次清理 %d 份）",
                root,
                stats["users"],
                stats["saves"],
                stats["bytes"] / 1024,
                pruned,
            )
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("存档缓存自检失败：%s", exc)

        self._load_name_tables()

    def _resolve_name_table_path(self) -> Path:
        raw = str(getattr(self.config.save, "name_table_file", "") or "").strip()
        if raw:
            return Path(raw).expanduser()
        return _DEFAULT_SAVE_NAMES_FILE

    def _resolve_achievement_table_path(self) -> Path:
        raw = str(getattr(self.config.save, "achievement_table_file", "") or "").strip()
        if raw:
            return Path(raw).expanduser()
        return _DEFAULT_ACHIEVEMENT_TABLE_FILE

    def _load_name_tables(self) -> None:
        """读取（可选）成就 / BOSS / 挑战中文名称表与成就解锁条件；没有就只输出编号。"""

        path = self._resolve_name_table_path()
        achievement_path = self._resolve_achievement_table_path()
        self._name_table_path = str(path)
        tables = NameTables.load(path, achievement_path=achievement_path)
        self._name_tables = tables
        if tables.is_empty:
            self.ctx.logger.info("未加载名称表（%s 不存在或为空），成就 / BOSS / 挑战只输出编号", path)
        else:
            self.ctx.logger.info("名称表已加载：%s（%s）", path, tables.counts_line())

    def _item_name_map(self) -> Dict[str, str]:
        """内置道具数据的「编号 → 中文名」，用于未发现道具清单。"""

        if self._db is None:
            return {}
        if not self._item_names:
            self._item_names = self._db.item_names("道具")
        return self._item_names

    def _save_store_or_none(self) -> Optional[SaveStore]:
        return self._save_store

    # ------------------------------------------------------------------
    # 存档解析：从消息里找文件
    # ------------------------------------------------------------------

    @staticmethod
    def _message_texts(kwargs: Dict[str, Any], message: Dict[str, Any]) -> List[str]:
        """收集这条消息里所有可能含文件引用的文本（含被引用消息的预览文本）。"""

        texts: List[str] = []
        for key in ("text", "raw_text"):
            value = kwargs.get(key)
            if value:
                texts.append(str(value))
        processed = message.get("processed_plain_text")
        if processed:
            texts.append(str(processed))
        segments = message.get("raw_message")
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                seg_type = str(segment.get("type") or "")
                data = segment.get("data")
                if seg_type == "text" and isinstance(data, str):
                    texts.append(data)
                elif seg_type == "reply" and isinstance(data, dict):
                    target = data.get("target_message_content")
                    if target:
                        texts.append(str(target))
                elif seg_type == "file" and isinstance(data, dict):
                    parts = [
                        str(data.get("name") or data.get("file") or ""),
                        str(data.get("url") or data.get("file_url") or ""),
                    ]
                    if any(parts):
                        texts.append("[文件] " + "，".join(part for part in parts if part))
        if not texts:
            texts.append(json.dumps(message, ensure_ascii=False))
        return texts

    @classmethod
    def _extract_save_ref(cls, kwargs: Dict[str, Any], message: Dict[str, Any]) -> SaveFileRef:
        """从消息里解析出存档文件引用（优先结构化文件段，其次文本里的链接）。"""

        segments = message.get("raw_message")
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict) or str(segment.get("type")) != "file":
                    continue
                data = segment.get("data")
                if not isinstance(data, dict):
                    continue
                name = str(data.get("name") or data.get("file") or "").strip()
                url = str(data.get("url") or data.get("file_url") or "").strip()
                file_id = str(data.get("file_id") or data.get("id") or "").strip()
                if name or url:
                    return SaveFileRef(url=url, name=name, file_id=file_id, hint=name or url)

        texts = cls._message_texts(kwargs, message)
        url, name, hint = extract_file_reference(texts)
        return SaveFileRef(url=url, name=name, hint=hint)

    @staticmethod
    def _napcat_data(resp: Any) -> Dict[str, Any]:
        """把 Napcat 适配器 API 的返回归一化成 data 字典（失败时抛异常）。"""

        if isinstance(resp, dict) and resp.get("success") is False:
            raise RuntimeError(str(resp.get("error") or "适配器调用失败"))
        if not isinstance(resp, dict):
            return {}
        data = resp.get("data")
        if isinstance(data, dict):
            return data
        return resp

    async def _resolve_group_file_url(self, name: str, group_id: str) -> str:
        """按文件名在群文件里找下载链接（引用消息里没有直链时的兜底）。"""

        wanted = str(name or "").strip()
        if not wanted or not str(group_id or "").strip():
            return ""
        try:
            group = int(str(group_id).strip())
        except (TypeError, ValueError):
            return ""

        root = self._napcat_data(
            await self.ctx.api.call(_GROUP_ROOT_FILES_API, params={"group_id": group, "file_count": 100})
        )
        files = root.get("files") if isinstance(root.get("files"), list) else []
        target: Optional[Dict[str, Any]] = None
        for entry in files:
            if not isinstance(entry, dict):
                continue
            entry_name = str(entry.get("file_name") or entry.get("name") or "").strip()
            if entry_name == wanted:
                target = entry
                break
            if target is None and wanted.lower() in entry_name.lower():
                target = entry
        if target is None:
            return ""

        file_id = str(target.get("file_id") or "").strip()
        busid = target.get("busid")
        if not file_id:
            return ""
        params: Dict[str, Any] = {"group_id": group, "file_id": file_id}
        if busid not in (None, ""):
            params["busid"] = busid
        detail = self._napcat_data(await self.ctx.api.call(_GROUP_FILE_URL_API, params=params))
        url = str(detail.get("url") or detail.get("file_url") or "").strip()
        return url

    # ------------------------------------------------------------------
    # 存档解析：下载
    # ------------------------------------------------------------------

    def _validate_download_target(self, url: str) -> None:
        """校验下载目标（协议 + 主机解析结果），必要时抛 :class:`SaveFormatError`。

        除了首次请求，重定向的每一跳也会走这里（见 :class:`_SafeRedirectHandler`），
        避免用 302 绕回内网。
        """

        parsed = urllib.parse.urlsplit(str(url or ""))
        if parsed.scheme not in ("http", "https"):
            raise SaveFormatError("存档链接不是 http/https，无法下载。")
        host = parsed.hostname or ""
        if not host:
            raise SaveFormatError("存档链接里没有主机名，无法下载。")
        if bool(self.config.save.allow_private_url):
            return
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        except OSError as exc:
            raise SaveFormatError(f"无法解析存档链接的主机名：{exc}") from exc
        for info in infos:
            address = str(info[4][0])
            try:
                ip = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError:
                continue
            if any(getattr(ip, flag) for flag in _BLOCKED_IP_FLAGS):
                raise SaveFormatError(
                    "存档链接指向内网 / 本机地址，出于安全考虑已拒绝下载。"
                    "（确实需要时可以打开「存档」配置里的 allow_private_url）"
                )

    def _download_bytes(self, url: str) -> bytes:
        """同步下载（由 ``asyncio.to_thread`` 调用），带 SSRF 校验与大小上限。"""

        self._validate_download_target(url)
        timeout = max(int(self.config.save.download_timeout_sec), 5)
        max_bytes = self._save_store.max_bytes if self._save_store is not None else 4 * 1024 * 1024
        request = urllib.request.Request(url, headers=_DOWNLOAD_HEADERS)
        opener = urllib.request.build_opener(_SafeRedirectHandler(self._validate_download_target))
        chunks: List[bytes] = []
        total = 0
        try:
            with opener.open(request, timeout=timeout) as response:  # noqa: S310 - 已做 scheme/主机校验
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise SaveFormatError(
                            f"文件超过 {max_bytes / 1024 / 1024:.0f} MB 上限，看起来不是以撒存档。"
                        )
                    chunks.append(chunk)
        except SaveFormatError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络错误统一转成用户可读文案
            raise SaveFormatError(f"下载存档失败：{exc}") from exc
        return b"".join(chunks)

    async def _fetch_save_bytes(self, ref: SaveFileRef, group_id: str) -> Tuple[bytes, str]:
        """拿到存档字节，返回 ``(内容, 文件名)``。"""

        name = ref.name
        url = ref.url
        if not url and name and bool(self.config.save.lookup_group_files):
            try:
                url = await self._resolve_group_file_url(name, group_id)
            except Exception as exc:  # noqa: BLE001 - 群文件查询失败不影响后面的错误提示
                self.ctx.logger.warning("按文件名查询群文件失败：%s", exc)
                url = ""
            if url:
                self.ctx.logger.info("按文件名从群文件取到下载链接：%s", name)
        if not url:
            raise SaveFormatError(
                "没有在这条消息里找到存档的下载链接。\n"
                f"请引用（回复）群文件里的 .dat 文件后发送 {_SAVE_COMMAND}绑定；"
                "或把文件直接发到聊天里再发送该指令。"
            )
        raw = await asyncio.to_thread(self._download_bytes, url)
        return raw, name or "save.dat"

    # ------------------------------------------------------------------
    # 存档解析：报告与发送
    # ------------------------------------------------------------------

    def _save_report(self, raw: bytes, file_name: str) -> SaveReport:
        analysis = parse_save_bytes(raw, file_name=file_name)
        return build_report(
            analysis,
            item_names=self._item_name_map(),
            name_tables=self._name_tables,
            max_missing_named=max(max(int(self.config.save.detail_max_named), 1) * 4, 80),
        )

    def _save_source_note(self) -> str:
        parts = ["道具名来自内置图鉴"]
        if not self._name_tables.is_empty:
            source = str(self._name_tables.meta.get("source") or "").strip()
            parts.append(f"名称表：{source or '本地名称表'}")
        return " ｜ ".join(parts)

    async def _render_save_image(
        self,
        report: SaveReport,
        *,
        nickname: str = "",
        user_id: str = "",
        bound_at: str = "",
    ) -> str:
        """本地绘制解析图，返回 base64（失败返回空串，由调用方降级为纯文字）。

        只走本地绘制：宿主 ``render.html2png`` 在实测中会超时失败（还得回退），
        因此插件不再调用它，绘制所需的字体也随插件打包。
        """

        if not bool(self.config.save.send_image) or build_card_png is None:
            return ""
        if not canvas_available():
            self.ctx.logger.warning("当前环境没有 Pillow，解析图降级为纯文字")
            return ""
        try:
            png = await asyncio.to_thread(
                build_card_png,
                report,
                width=max(min(int(self.config.save.image_width), 1000), 600),
                scale=float(self.config.save.image_scale),
                nickname=nickname,
                user_id=user_id,
                max_chips=max(int(self.config.save.max_image_chips), 1),
                max_track_chips=max(int(self.config.save.max_image_track_chips), 0),
                source_note=self._save_source_note(),
                bound_at=bound_at,
            )
            return base64.b64encode(png).decode("ascii")
        except Exception as exc:  # noqa: BLE001 - 渲染失败不应影响文字结果
            self.ctx.logger.warning("绘制解析图失败，改为纯文字：%s", exc)
            return ""

    async def _send_save_result(
        self,
        stream_id: str,
        report: SaveReport,
        *,
        nickname: str = "",
        user_id: str = "",
        bound_at: str = "",
        with_image: bool = True,
        intro: str = "",
    ) -> None:
        """发送解析结果：解析图 + 合并转发详情（图片不可用时改发文字摘要）。"""

        detail = format_report_text(
            report,
            max_undiscovered=max(int(self.config.save.detail_max_items), 1),
            max_missing_named=max(int(self.config.save.detail_max_named), 1),
        )
        image_base64 = ""
        if with_image:
            image_base64 = await self._render_save_image(
                report, nickname=nickname, user_id=user_id, bound_at=bound_at
            )

        if image_base64:
            if intro:
                await self.ctx.send.text(intro, stream_id)
            try:
                sent = await self.ctx.send.image(image_base64, stream_id)
            except Exception as exc:  # noqa: BLE001 - 能力被拒或适配器不支持
                self.ctx.logger.warning("发送解析图失败，改为文字发送：%s", exc)
                sent = False
            if not sent:
                await self.ctx.send.text(format_summary_text(report, command=_SAVE_COMMAND), stream_id)
        else:
            summary = format_summary_text(report, command=_SAVE_COMMAND)
            text = f"{intro}\n{summary}" if intro else summary
            await self.ctx.send.text(text, stream_id)

        if not await self._send_forward(stream_id, detail, nickname=_SAVE_FORWARD_NICKNAME):
            await self._send_text_chunked(stream_id, detail)

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
        text = build_help_text(source_note=source_note)
        if bool(self.config.save.enabled):
            text += "\n" + self._save_help_text()
        return text

    def _save_help_text(self) -> str:
        """存档解析部分的帮助文本。"""

        store = self._save_store_or_none()
        cache_note = "未启用（插件数据目录不可用）" if store is None else "已就绪"
        lines = [
            "",
            "存档解析（只读）：",
            f"{_SAVE_COMMAND}         查看解析图与详细进度",
            f"{_SAVE_COMMAND}绑定     引用（回复）群文件里的 .dat 后绑定 / 更新",
            f"{_SAVE_COMMAND}2        查看存档位 2 的解析结果",
            f"{_SAVE_COMMAND}列表     查看已缓存的存档",
            f"{_SAVE_COMMAND}清除     删除 bot 本地缓存的存档",
            f"{_SAVE_COMMAND}帮助     找不到存档文件时看这里",
            f"本地缓存：{cache_note}",
        ]
        if not self._name_tables.is_empty:
            source = str(self._name_tables.meta.get("source") or "").strip()
            lines.append(f"名称表：{source or self._name_table_path}")
        return "\n".join(lines)

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

    async def _send_forward(self, stream_id: str, text: str, *, nickname: str = _FORWARD_NICKNAME) -> bool:
        """长内容用单条合并转发发送，失败返回 False 交由调用方回退。"""

        try:
            await self.ctx.send.forward(
                [
                    {
                        "user_id": "0",
                        "nickname": nickname,
                        "segments": [{"type": "text", "content": text}],
                    }
                ],
                stream_id,
            )
            return True
        except Exception as exc:
            self.ctx.logger.warning("合并转发失败，回退为普通文本：%s", exc)
            return False

    async def _send_text_chunked(self, stream_id: str, text: str, *, limit: int = 1200) -> None:
        """合并转发不可用时的兜底：把长文本切成若干条普通消息发送。"""

        chunk_limit = max(int(limit), 200)
        lines = str(text or "").split("\n")
        buffer: List[str] = []
        length = 0
        for line in lines:
            if length + len(line) + 1 > chunk_limit and buffer:
                await self.ctx.send.text("\n".join(buffer), stream_id)
                buffer = []
                length = 0
            buffer.append(line)
            length += len(line) + 1
        if buffer:
            await self.ctx.send.text("\n".join(buffer), stream_id)

    # ------------------------------------------------------------------
    # 指令
    # ------------------------------------------------------------------

    @Command(
        "isaac_item_query",
        description="查询《以撒的结合》道具图鉴（名称/编号/效果/协同）",
        pattern=(
            r"(?<!\S)/?(?:以撒|isaac)(?:图鉴|道具)?\s*"
            r"(?!存档|进度|save)(?P<query>[\s\S]*?)\s*$"
        ),
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
    # 指令：存档解析
    # ------------------------------------------------------------------

    @Command(
        "isaac_save_query",
        description="解析《以撒的结合》存档：绑定群文件里的 .dat，输出进度解析图与详细统计",
        pattern=(
            r"(?<!\S)/?(?:以撒|isaac)(?:存档|进度|save)\s*"
            r"(?P<action>绑定|上传|更新|解绑|清除|删除|列表|状态|帮助|说明|详情|文本|[1-9])?\s*$"
        ),
    )
    async def cmd_isaac_save(self, **kwargs: Any) -> tuple[bool, str, bool]:
        stream_id = str(kwargs.get("stream_id") or "")
        user_id = str(kwargs.get("user_id") or "")
        message = kwargs.get("message") if isinstance(kwargs.get("message"), dict) else {}
        matched = kwargs.get("matched_groups")
        action = str(matched.get("action") or "").strip() if isinstance(matched, dict) else ""
        self.ctx.logger.debug("存档指令：user=%s action=%r", user_id, action)

        if not bool(self.config.save.enabled):
            await self.ctx.send.text(
                "存档解析功能当前已被管理员关闭。\n（可在 WebUI 插件配置的「存档」节打开 enabled）",
                stream_id,
            )
            return True, "存档解析已关闭", True

        store = self._save_store_or_none()
        if store is None:
            await self.ctx.send.text(
                "存档缓存目录不可用，暂时无法处理存档。请检查插件数据目录权限后重载插件。",
                stream_id,
            )
            return True, "存档缓存不可用", True

        if not user_id:
            await self.ctx.send.text("没有识别到你的 QQ 号，无法保存存档。请在群聊或私聊里使用该指令。", stream_id)
            return True, "缺少 user_id", True

        group_id = ""
        message_info = message.get("message_info") if isinstance(message.get("message_info"), dict) else {}
        group_info = message_info.get("group_info") if isinstance(message_info.get("group_info"), dict) else {}
        if group_info:
            group_id = str(group_info.get("group_id") or "")
        user_info = message_info.get("user_info") if isinstance(message_info.get("user_info"), dict) else {}
        nickname = str(user_info.get("user_cardname") or user_info.get("user_nickname") or "").strip()

        try:
            if action in _SAVE_GUIDE_ACTIONS:
                await self._send_save_guide(stream_id)
            elif action in _SAVE_BIND_ACTIONS:
                await self._handle_save_bind(stream_id, user_id, nickname, kwargs, message, group_id)
            elif action in _SAVE_CLEAR_ACTIONS:
                await self._handle_save_clear(stream_id, user_id)
            elif action in _SAVE_LIST_ACTIONS:
                await self._handle_save_list(stream_id, user_id)
            else:
                slot = int(action) if action.isdigit() else None
                with_image = action not in _SAVE_TEXT_ONLY_ACTIONS
                await self._handle_save_view(stream_id, user_id, nickname, slot, with_image=with_image)
        except SaveFormatError as exc:
            await self.ctx.send.text(str(exc), stream_id)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免指令异常冒泡成宿主报错
            self.ctx.logger.error("处理存档指令失败：%s", exc, exc_info=True)
            await self.ctx.send.text(f"解析存档时出错了：{exc}", stream_id)
        return True, "存档指令已处理", True

    async def _send_save_guide(self, stream_id: str, *, intro: str = "") -> None:
        """发送「怎么找存档 / 怎么交给我」的指引（合并转发，避免刷屏）。"""

        guide = build_upload_guide(command="以撒存档")
        if intro:
            await self.ctx.send.text(intro, stream_id)
        if not await self._send_forward(stream_id, guide, nickname=_SAVE_FORWARD_NICKNAME):
            await self._send_text_chunked(stream_id, guide)

    async def _handle_save_bind(
        self,
        stream_id: str,
        user_id: str,
        nickname: str,
        kwargs: Dict[str, Any],
        message: Dict[str, Any],
        group_id: str,
    ) -> None:
        """从「引用的群文件 / 直接发送的文件」绑定存档。"""

        store = self._save_store
        assert store is not None

        ref = self._extract_save_ref(kwargs, message)
        if not ref.usable:
            await self._send_save_guide(
                stream_id,
                intro="这条消息里没有看到存档文件，先看看下面怎么找存档、怎么发给我：",
            )
            return

        raw, file_name = await self._fetch_save_bytes(ref, group_id)
        source = "群文件引用" if group_id else "文件消息"
        entry = store.put(user_id, raw, file_name=file_name, source=source)
        self.ctx.logger.info(
            "存档已绑定：user=%s slot=%s 文件=%s（%d 字节，%s）",
            user_id,
            entry.slot,
            entry.file_name,
            entry.size,
            entry.version_label,
        )

        notice = (
            f"存档已收好：{entry.file_name}（{entry.size_text}，{entry.version_label}）\n"
            f"已按你的 QQ 号缓存在 bot 本地，存档位 {entry.slot}；解析是只读的，不会改动原文件。"
        )
        if not bool(self.config.save.auto_analyze_after_bind):
            await self.ctx.send.text(notice + f"\n发送 {_SAVE_COMMAND} 查看解析结果。", stream_id)
            return

        report = self._save_report(raw, entry.file_name)
        await self._send_save_result(
            stream_id,
            report,
            nickname=nickname,
            user_id=user_id,
            bound_at=entry.bound_at_text,
            intro=notice,
        )

    async def _handle_save_view(
        self,
        stream_id: str,
        user_id: str,
        nickname: str,
        slot: Optional[int],
        *,
        with_image: bool = True,
    ) -> None:
        """查看已绑定存档的解析结果；没绑定时给指引。"""

        store = self._save_store
        assert store is not None
        entry = store.get(user_id, slot)
        if entry is None:
            if slot is not None:
                cached = store.slots(user_id)
                if cached:
                    await self.ctx.send.text(
                        f"你还没有绑定存档位 {slot} 的存档。\n" + format_cache_list(cached),
                        stream_id,
                    )
                    return
            await self._send_save_guide(stream_id, intro="还没有你的存档，先把存档文件发给我吧：")
            return

        try:
            raw = await asyncio.to_thread(entry.load_bytes)
        except OSError as exc:
            await self.ctx.send.text(f"读取本地缓存的存档失败：{exc}\n可以重新发送 {_SAVE_COMMAND}绑定。", stream_id)
            return
        report = self._save_report(raw, entry.file_name)
        await self._send_save_result(
            stream_id,
            report,
            nickname=nickname,
            user_id=user_id,
            bound_at=entry.bound_at_text,
            with_image=with_image,
        )

    async def _handle_save_clear(self, stream_id: str, user_id: str) -> None:
        store = self._save_store
        assert store is not None
        removed = store.remove(user_id)
        if removed:
            await self.ctx.send.text(
                f"已删除本地缓存的 {removed} 份存档（原文件在你自己电脑上，不受影响）。",
                stream_id,
            )
        else:
            await self.ctx.send.text("本地没有你的存档缓存，无需删除。", stream_id)

    async def _handle_save_list(self, stream_id: str, user_id: str) -> None:
        store = self._save_store
        assert store is not None
        entries = store.slots(user_id)
        text = format_cache_list(entries)
        if entries:
            text += f"\n发送 {_SAVE_COMMAND} 查看解析结果，{_SAVE_COMMAND}清除 删除缓存。"
        await self.ctx.send.text(text, stream_id)

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
