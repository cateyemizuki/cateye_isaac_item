"""《以撒的结合》存档解析（只读）与本地缓存。

本模块是纯逻辑实现，不依赖 ``maibot_sdk``，可以脱离插件 Runner 单独运行与单测：

    python -c "from isaac_save import *; a=parse_save_bytes(open('rep+persistentgamedata1.dat','rb').read()); print(format_report_text(build_report(a)))"

格式要点（在真实存档上实测确认，详见 ``存档分析-可行性评估.md``）：

* 偏移 0 起 16 字节魔数 ``ISAACNGSAVE09R  ``（尾随两个空格），偏移 16 起 4 字节校验值，
  偏移 20 起依次排列 11 个数据块：``type s4 | len s4 | count s4 | body``；
* ``len`` 字段**不可信**（成就段等于 count，其余各段恒为 ``count × 4``，与实际条目宽度无关），
  跳过数据块必须用 ``count × 实际条目宽度``；
* 数组的**下标基准按段区分**（见 :data:`CHUNK_INDEX_BASE`）：成就 / 道具 / 挑战 / 特殊种子的第 0 位
  未使用（真实总数 = ``count − 1``）；小 BOSS（七宗罪 7 项）与 BOSS（104 项）按下标直接对应真实条目
  （总数 = ``count``）；
* 魔数只区分大版本，胎衣+ / 忏悔 / 忏悔+ 共用 ``09R``，要靠成就段自身的 ``count`` 再分
  （349 / 404 胎衣+、638 忏悔、642 忏悔+）。

本模块只解析、只读，**不修改任何存档文件**。
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

SAVE_MAGIC_PREFIX = b"ISAACNGSAVE"
GAMESTATE_MAGIC_PREFIX = b"ISAACNG_GSR"
SAVE_HEADER_SIZE = 20  # 16 字节魔数 + 4 字节校验值
CHUNK_COUNT = 11
BESTIARY_CHUNK_TYPE = 11

#: 各数据块单条目字节数（bestiary 变长，本模块不解析）
CHUNK_WIDTH: Dict[int, int] = {1: 1, 2: 4, 3: 4, 4: 1, 5: 1, 6: 1, 7: 1, 8: 4, 9: 4, 10: 1}

#: 各类数组「真实条目的起始下标」——决定总数是 ``count`` 还是 ``count − 1``。
#:
#: * 成就 / 道具 / 挑战 / 特殊种子的**第 0 位未使用**（官方格式说明：``index 0 is unused``），
#:   真实总数 = ``count − 1``；
#: * **小 BOSS（七宗罪 7 项）与 BOSS（104 项）按下标直接对应真实条目**，总数 = ``count``。
#:
#: 依据：REPENTOGON 对存档格式的记录（小 BOSS 段 7 项 = 七宗罪、BOSS 段 104 项 = 按 BOSS id）
#: 与公开格式说明；并且在真实存档上实测——同一台机器上「空存档位」与「有进度存档位」相比，
#: 这两段的第 0 位会随进度从 0 变成 1，而成就 / 道具 / 挑战段的第 0 位恒为 0
#: （占位值不会随进度变化）。早期评估文档把这第 0 位误判为占位，已在本模块纠正。
CHUNK_INDEX_BASE: Dict[int, int] = {5: 0, 6: 0}
DEFAULT_CHUNK_INDEX_BASE = 1

CHUNK_LABELS: Dict[int, str] = {
    1: "成就",
    2: "全局计数器",
    3: "楼层计数器",
    4: "道具",
    5: "小 BOSS（七宗罪）",
    6: "BOSS 记录",
    7: "挑战",
    8: "过场动画",
    9: "游戏设置",
    10: "特殊种子",
    11: "怪物图鉴",
}

#: 进度类数据块：key -> (数据块类型, 展示名)
TRACK_CHUNKS: Dict[str, Tuple[int, str]] = {
    "achievements": (1, "成就"),
    "collectibles": (4, "道具"),
    "bosses": (6, "BOSS 记录"),
    "minibosses": (5, "小 BOSS 记录"),
    "challenges": (7, "挑战"),
    "special_seeds": (10, "特殊种子"),
}

#: 成就段 count → 版本（349 为胎衣+早期、404 为胎衣+  booster5）
VERSION_BY_ACHIEVEMENT_COUNT: Dict[int, str] = {
    349: "afterbirth+",
    404: "afterbirth+",
    638: "repentance",
    642: "repentance+",
}

#: 魔数中的大版本代号 → 版本说明
VERSION_BY_MAGIC_MAJOR: Dict[str, str] = {
    "06R": "rebirth",
    "08R": "afterbirth",
    "09R": "09R-ambiguous",  # 胎衣+ / 忏悔 / 忏悔+ 共用，需看成就段
}

VERSION_LABELS: Dict[str, str] = {
    "rebirth": "重生（Rebirth）",
    "afterbirth": "胎衣（Afterbirth）",
    "afterbirth+": "胎衣+（Afterbirth+）",
    "repentance": "忏悔（Repentance）",
    "repentance+": "忏悔+（Repentance+）",
    "unknown": "未知版本",
}

#: 已确认含义的全局计数器（下标 = 字节偏移 ÷ 4）。其余 500 多个下标没有公开对照表，不猜。
#:
#: 每一项都要求「在真实存档上读出的值与游戏内表现相符」才算确认，例如：
#: 妈妈击杀 226、死亡 438、碎石 59304、店主击杀 58、捐款机累计 1092、伊甸币 223、最佳连胜 11。
#:
#: **实测方法**：拿 93 份每日快照（2025-07 ~ 2026-09）对比——累计型统计只增不减
#: （妈妈击杀、碎石、大便、死亡、店主击杀等全部单调不减）；状态型会升也会降
#: （伊甸币会被花掉、连胜会断）。再与成就交叉验证：下标 20 越过 900 后，
#: 成就 59「蓝蜡烛（捐献 900 枚硬币给捐款机）」确实已解锁。
#:
#: **故意不展示的两项**：第三方偏移表把下标 ``0`` 标为 ``DONATION``、``19`` 标为 ``DONATION_COINS``，
#: 但这 93 份快照里两项**恒为 0**（同期捐款机累计值从 151 涨到 1133），
#: 所以它们不是捐款机相关计数（Rep+ 的布局与那张旧表已经对不上，或另有含义），故不展示。
COUNTER_LABELS: Tuple[Tuple[int, str], ...] = (
    (1, "妈妈击杀"),
    (10, "死亡次数"),
    (11, "店主击杀"),
    (2, "碎石数"),
    (5, "大便破坏"),
    (3, "染色石破坏"),
    (20, "捐款机累计"),
    (21, "伊甸币"),
    (23, "最佳连胜"),
    (22, "当前连胜"),
)

#: 存档文件名：前缀决定版本，persistentgamedata 才是持久存档，gamestate 是「进行中的一局」
SAVE_FILE_RE = re.compile(
    r"(?i)^(?P<prefix>rep\+|rep_|abp_|ab_)?(?P<kind>persistentgamedata|gamestate)(?P<slot>[1-9])?\.dat$"
)

FILE_KIND_LABELS: Dict[str, str] = {
    "persistent": "持久存档",
    "gamestate": "单局状态（中途退出）",
    "unknown": "未知文件",
}

#: 上传大小上限（真实存档只有几十 KB，这里只用来挡异常文件）
DEFAULT_MAX_SAVE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_SLOT = 3

_UID_SAFE_RE = re.compile(r"[^0-9A-Za-z_-]+")
_URL_RE = re.compile(r"https?://[^\s，,、；;）)】\]\"'<>]+")
_FILE_HINT_RE = re.compile(r"[\[【]\s*文件\s*[\]】]")


class SaveFormatError(ValueError):
    """上传的文件不是可解析的以撒持久存档。"""


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class SaveChunk:
    """一个数据块的头部与统计结果。"""

    type: int
    length: int
    count: int
    body_offset: int
    unlocked: Tuple[int, ...] = ()
    index_base: int = DEFAULT_CHUNK_INDEX_BASE

    @property
    def label(self) -> str:
        return CHUNK_LABELS.get(self.type, f"未知数据块 {self.type}")

    @property
    def total(self) -> int:
        """真实条目总数（取决于该段是否使用第 0 位，见 :data:`CHUNK_INDEX_BASE`）。"""

        return max(self.count - self.index_base, 0)

    @property
    def unlocked_count(self) -> int:
        return len(self.unlocked)

    @property
    def missing(self) -> Tuple[int, ...]:
        found = set(self.unlocked)
        return tuple(index for index in range(self.index_base, max(self.count, 1)) if index not in found)


@dataclass(frozen=True)
class ProgressTrack:
    """一条进度（成就 / 道具 / 图鉴 / 挑战……）。"""

    key: str
    label: str
    unlocked: int
    total: int

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.unlocked / self.total * 100

    @property
    def ratio_text(self) -> str:
        return f"{self.unlocked}/{self.total}"

    @property
    def percent_text(self) -> str:
        return f"{self.percent:.1f}%"

    @property
    def remaining(self) -> int:
        return max(self.total - self.unlocked, 0)


@dataclass(frozen=True)
class CounterStat:
    """一条全局统计。"""

    index: int
    label: str
    value: int


@dataclass(frozen=True)
class SaveAnalysis:
    """一次存档解析的完整结果。"""

    file_name: str
    file_size: int
    magic: str
    magic_major: str
    version_key: str
    version_label: str
    slot: Optional[int]
    chunks: Tuple[SaveChunk, ...]
    counters: Tuple[int, ...]
    trailing_bytes: Optional[int]
    layout_ok: bool
    bestiary_walked: bool = False
    warnings: Tuple[str, ...] = ()

    def chunk(self, chunk_type: int) -> Optional[SaveChunk]:
        for chunk in self.chunks:
            if chunk.type == chunk_type:
                return chunk
        return None

    def track(self, key: str) -> Optional[ProgressTrack]:
        entry = TRACK_CHUNKS.get(key)
        if entry is None:
            return None
        chunk = self.chunk(entry[0])
        if chunk is None:
            return None
        return ProgressTrack(key=key, label=entry[1], unlocked=chunk.unlocked_count, total=chunk.total)

    def tracks(self) -> Tuple[ProgressTrack, ...]:
        result: List[ProgressTrack] = []
        for key in TRACK_CHUNKS:
            track = self.track(key)
            if track is not None:
                result.append(track)
        return tuple(result)

    def counter(self, index: int) -> int:
        if 0 <= index < len(self.counters):
            return int(self.counters[index])
        return 0

    def counter_stats(self) -> Tuple[CounterStat, ...]:
        stats: List[CounterStat] = []
        for index, label in COUNTER_LABELS:
            if index < len(self.counters):
                stats.append(CounterStat(index=index, label=label, value=int(self.counters[index])))
        return tuple(stats)

    def missing(self, key: str) -> Tuple[int, ...]:
        entry = TRACK_CHUNKS.get(key)
        if entry is None:
            return ()
        chunk = self.chunk(entry[0])
        return chunk.missing if chunk is not None else ()

    @property
    def is_empty(self) -> bool:
        """空存档（没有任何进度）判定，用于提示「可能是空存档位」。"""

        return all(track.unlocked == 0 for track in self.tracks())

    @property
    def achievement_count(self) -> int:
        chunk = self.chunk(1)
        return chunk.count if chunk is not None else 0


@dataclass(frozen=True)
class UndiscoveredItem:
    """未发现的道具（``name`` 为空表示图鉴里没有这一编号）。"""

    item_id: str
    name: str = ""

    @property
    def known(self) -> bool:
        return bool(self.name)


@dataclass(frozen=True)
class NamedEntry:
    """带名称对照表的条目（成就 / 挑战 / BOSS / 小 BOSS）。"""

    index: int
    name: str
    detail: str = ""


@dataclass(frozen=True)
class AchievementDetail:
    """成就表里的一条更完整的记录（``assets/isaac_achievements.json``）。

    成就的「名称」其实就是解锁出来的内容（角色 / 道具 / 挑战……），``condition``
    是解锁条件，因此「还差哪些成就 + 怎么解锁」可以直接告诉用户。
    """

    index: int
    name: str = ""
    en: str = ""
    condition: str = ""
    reward: str = ""
    kind: str = ""


def load_achievement_details(paths: Optional[Sequence[Path | str]] = None) -> Dict[int, AchievementDetail]:
    """读取一张或多张成就详情表；后面的表按字段覆盖前面的（文件缺失/异常时跳过）。

    默认两张：游戏本体生成的 ``isaac_achievements.json``（英文条件）与第三方中文表
    ``isaac_achievements_zh.json``（wiki 来源，CC BY-SA 4.0 / CC BY-NC-SA 3.0）。
    覆盖按**字段**进行：中文表有中文名与中文条件就用它，缺失字段保留游戏本体表的值。
    """

    merged: Dict[int, AchievementDetail] = {}
    for item in paths or ():
        if not item:
            continue
        file_path = Path(item)
        if not file_path.is_file():
            continue
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            continue
        for key, value in entries.items():
            if not isinstance(value, dict):
                continue
            try:
                index = int(str(key).strip())
            except (TypeError, ValueError):
                continue
            incoming = AchievementDetail(
                index=index,
                name=str(value.get("name") or "").strip(),
                en=str(value.get("en") or "").strip(),
                condition=str(value.get("cond") or "").strip(),
                reward=str(value.get("reward") or "").strip(),
                kind=str(value.get("type") or "").strip(),
            )
            previous = merged.get(index)
            if previous is None:
                merged[index] = incoming
                continue
            merged[index] = AchievementDetail(
                index=index,
                name=incoming.name or previous.name,
                en=incoming.en or previous.en,
                condition=incoming.condition or previous.condition,
                reward=incoming.reward or previous.reward,
                kind=incoming.kind or previous.kind,
            )
    return merged


@dataclass(frozen=True)
class NameTables:
    """可选的中文名称表（``assets/isaac_save_names.json``）。

    文件缺失或字段为空时，对应类别只输出编号与计数，不编造名称。支持的结构：

    ```json
    {
      "meta": {"source": "…", "reviewed": false},
      "achievements": {"1": "以撒的泪"},
      "bosses": {"1": "怪物", "62": {"name": "究极贪婪", "note": "游戏不写这一项"}},
      "minibosses": {"1": "…"},
      "challenges": {"1": "…"}
    }
    ```

    也接受 ``[{"id": 1, "name": "…"}, …]`` 形式的列表。条目写成 ``{"name": …, "note": …}``
    时，``note`` 会作为该条目的补充说明一起显示（例如存档 BOSS 段里游戏本身不会写入、
    或该版本没有分配的空槽位）。成就的解锁条件另有一张可选的详情表
    （``assets/isaac_achievements.json``，``entries`` 下每条含
    ``name`` / ``en`` / ``cond`` / ``reward`` / ``type``）。
    """

    achievements: Mapping[int, str] = None  # type: ignore[assignment]
    bosses: Mapping[int, str] = None  # type: ignore[assignment]
    minibosses: Mapping[int, str] = None  # type: ignore[assignment]
    challenges: Mapping[int, str] = None  # type: ignore[assignment]
    achievement_details: Mapping[int, AchievementDetail] = None  # type: ignore[assignment]
    notes: Mapping[str, Mapping[int, str]] = None  # type: ignore[assignment]
    meta: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        for field_name in (
            "achievements",
            "bosses",
            "minibosses",
            "challenges",
            "achievement_details",
            "notes",
            "meta",
        ):
            if getattr(self, field_name) is None:
                object.__setattr__(self, field_name, {})

    @classmethod
    def load(
        cls,
        path: Optional[Path | str],
        *,
        achievement_path: Optional[Path | str] = None,
        achievement_paths: Optional[Sequence[Path | str]] = None,
    ) -> "NameTables":
        """读取名称表；文件不存在或格式异常时返回空表（功能降级，不报错）。

        ``achievement_paths`` 为空时退回单个 ``achievement_path``（向后兼容）。
        """

        sources = list(achievement_paths) if achievement_paths else ([achievement_path] if achievement_path else [])
        details = load_achievement_details(sources)
        if not path:
            return cls(achievement_details=details)
        file_path = Path(path)
        if not file_path.is_file():
            return cls(achievement_details=details)
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return cls(achievement_details=details)
        if not isinstance(raw, dict):
            return cls(achievement_details=details)
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        track_keys = ("achievements", "bosses", "minibosses", "challenges")
        return cls(
            achievements=_normalize_name_map(raw.get("achievements")),
            bosses=_normalize_name_map(raw.get("bosses")),
            minibosses=_normalize_name_map(raw.get("minibosses")),
            challenges=_normalize_name_map(raw.get("challenges")),
            achievement_details=details,
            notes={key: _normalize_notes_map(raw.get(key)) for key in track_keys},
            meta=meta,
        )

    def names_for(self, track_key: str) -> Mapping[int, str]:
        return {
            "achievements": self.achievements,
            "bosses": self.bosses,
            "minibosses": self.minibosses,
            "challenges": self.challenges,
        }.get(track_key, {})

    def note_for(self, track_key: str, index: int) -> str:
        """取某一条目的补充说明（没有则返回空串）。"""

        table = self.notes.get(track_key) or {}
        return str(table.get(int(index)) or "").strip()

    def achievement_detail(self, index: int) -> Optional[AchievementDetail]:
        return self.achievement_details.get(int(index))

    @property
    def is_empty(self) -> bool:
        return not any((self.achievements, self.bosses, self.minibosses, self.challenges))

    def counts_line(self) -> str:
        """一行规模描述（日志与帮助文本用）。"""

        parts = [
            f"成就 {len(self.achievements)}",
            f"挑战 {len(self.challenges)}",
            f"BOSS {len(self.bosses)}",
            f"小 BOSS {len(self.minibosses)}",
        ]
        if self.achievement_details:
            parts.append(f"成就详情 {len(self.achievement_details)}")
        return " / ".join(parts)



@dataclass(frozen=True)
class SaveReport:
    """面向展示的解析报告（不含图片数据）。"""

    analysis: SaveAnalysis
    tracks: Tuple[ProgressTrack, ...]
    counters: Tuple[CounterStat, ...]
    undiscovered: Tuple[UndiscoveredItem, ...]
    missing_named: Mapping[str, Tuple[NamedEntry, ...]]
    name_source: str = ""

    @property
    def undiscovered_known(self) -> Tuple[UndiscoveredItem, ...]:
        return tuple(item for item in self.undiscovered if item.known)

    @property
    def undiscovered_unknown(self) -> Tuple[UndiscoveredItem, ...]:
        return tuple(item for item in self.undiscovered if not item.known)


@dataclass(frozen=True)
class CachedSave:
    """本地缓存里的一份存档。"""

    user_id: str
    slot: int
    file_name: str
    size: int
    version_key: str
    version_label: str
    bound_at: str
    source: str
    sha256: str
    path: Path

    def load_bytes(self) -> bytes:
        return self.path.read_bytes()

    @property
    def bound_at_text(self) -> str:
        return _format_timestamp(self.bound_at)

    @property
    def size_text(self) -> str:
        if self.size < 1024:
            return f"{self.size} 字节"
        return f"{self.size / 1024:.1f} KB"


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------


def _normalize_name_map(raw: Any) -> Dict[int, str]:
    """把 JSON 里的名称表归一化成 ``{下标: 名称}``。

    值既可以是名称字符串，也可以是 ``{"name": …, "note": …}``——后者用于给
    「名字之外还需要解释」的条目（例如存档 BOSS 段里游戏本身不会写入的槽位）
    附加一行说明，见 :func:`_normalize_notes_map`。
    """

    result: Dict[int, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                index = int(str(key).strip())
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                name = str(value.get("name") or value.get("名称") or "").strip()
            else:
                name = str(value or "").strip()
            if name:
                result[index] = name
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            for key in ("id", "index", "no", "编号"):
                if key in entry:
                    try:
                        index = int(str(entry[key]).strip())
                    except (TypeError, ValueError):
                        continue
                    name = str(entry.get("name") or entry.get("名称") or "").strip()
                    if name:
                        result[index] = name
                    break
    return result


def _normalize_notes_map(raw: Any) -> Dict[int, str]:
    """从同一张名称表里取出 ``{下标: 说明}``（只有写成对象形式的条目才有）。"""

    result: Dict[int, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            try:
                index = int(str(key).strip())
            except (TypeError, ValueError):
                continue
            note = str(value.get("note") or value.get("detail") or value.get("说明") or "").strip()
            if note:
                result[index] = note
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            note = str(entry.get("note") or entry.get("detail") or entry.get("说明") or "").strip()
            if not note:
                continue
            for key in ("id", "index", "no", "编号"):
                if key in entry:
                    try:
                        result[int(str(entry[key]).strip())] = note
                    except (TypeError, ValueError):
                        pass
                    break
    return result


def _format_timestamp(value: str) -> str:
    """把 ISO 时间戳转成 ``YYYY-MM-DD HH:MM``（本地时区）。"""

    text = str(value or "").strip()
    if not text:
        return "未知"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def describe_save_file(file_name: str) -> str:
    """判断文件名属于哪种存档：``persistent`` / ``gamestate`` / ``unknown``。"""

    match = SAVE_FILE_RE.match(Path(str(file_name or "")).name)
    if not match:
        return "unknown"
    return "persistent" if match.group("kind").lower() == "persistentgamedata" else "gamestate"


def extract_slot(file_name: str) -> Optional[int]:
    """从文件名里取存档位（``rep+persistentgamedata2.dat`` → 2）。"""

    match = SAVE_FILE_RE.match(Path(str(file_name or "")).name)
    if not match:
        return None
    raw = match.group("slot")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def version_from_file_name(file_name: str) -> str:
    """按文件名前缀粗略判断版本（仅用于提示，权威判断看魔数与成就段）。"""

    match = SAVE_FILE_RE.match(Path(str(file_name or "")).name)
    if not match:
        return "unknown"
    prefix = (match.group("prefix") or "").lower()
    return {
        "rep+": "repentance+",
        "rep_": "repentance",
        "abp_": "afterbirth+",
        "ab_": "afterbirth+",
    }.get(prefix, "unknown")


def extract_file_reference(texts: Iterable[str]) -> Tuple[str, str, str]:
    """从若干段文本里找出「群文件」引用。

    返回 ``(下载链接, 文件名, 命中片段)``；找不到链接时返回空串。
    适配器会把文件段渲染成 ``[文件] 名字，大小: N，链接: http://…`` 这样的文本，
    因此这里同时兼容这一形态和裸链接。

    只读、纯字符串处理：不访问网络，也不判断链接是否可信（下载前由调用方校验）。
    """

    candidates: List[str] = []
    for text in texts:
        raw = str(text or "")
        if raw:
            candidates.append(raw)
    for raw in candidates:
        has_file_hint = bool(_FILE_HINT_RE.search(raw))
        for match in _URL_RE.finditer(raw):
            url = match.group(0).rstrip(".,;)]}")
            if not url:
                continue
            if has_file_hint or url.lower().endswith(".dat") or _looks_like_qq_file_url(url):
                head = raw[: match.start()]
                name = _file_name_from_text(head)
                return url, name, raw[max(match.start() - 60, 0) : match.end()]
    # 没有任何链接：只有文件名也算命中（调用方可以按名字去群文件里找）
    for raw in candidates:
        name = _file_name_from_text(raw)
        if name:
            return "", name, raw[:120]
    return "", "", ""


def _file_name_from_text(text: str) -> str:
    """从文本里提取疑似存档文件名。"""

    for match in re.finditer(r"(?i)[^\s，,、；;：:\[\]【】\"'<>|/\\]{1,80}\.dat", str(text or "")):
        candidate = Path(match.group(0).strip()).name
        if describe_save_file(candidate) != "unknown":
            return candidate
    return ""


def _looks_like_qq_file_url(url: str) -> bool:
    lowered = url.lower()
    if ".dat" in lowered:
        return True
    return any(host in lowered for host in ("qfile", "groupfile", "ftn", "multimedia.nt.qq.com", "qq.com"))


def parse_save_bytes(raw: bytes, *, file_name: str = "") -> SaveAnalysis:
    """解析一份持久存档。

    只读：不会修改传入的字节或磁盘上的文件。文件不是持久存档时抛
    :class:`SaveFormatError`，消息面向普通用户、可直接发给 QQ。
    """

    if not raw:
        raise SaveFormatError("文件是空的，没有读到任何内容，请重新发送存档文件。")

    magic_bytes = raw[:16]
    if magic_bytes.startswith(GAMESTATE_MAGIC_PREFIX):
        raise SaveFormatError(
            "这是「进行中的一局」状态文件（gamestate），不是持久存档，无法用于进度统计。\n"
            "请在游戏里退回主菜单后再复制存档，文件名应为 rep+persistentgamedata1.dat 这种。"
        )
    if not magic_bytes.startswith(SAVE_MAGIC_PREFIX):
        raise SaveFormatError(
            "这个文件不是《以撒的结合》存档（文件头不匹配）。\n"
            f"读到的文件头：{magic_bytes[:12]!r}\n"
            "常见原因是上传了压缩包、快捷方式或其它文件。"
        )
    if len(raw) < SAVE_HEADER_SIZE:
        raise SaveFormatError("存档文件长度不足，可能上传时被截断了，请重新发送。")

    magic_text = magic_bytes.decode("ascii", errors="replace")
    magic_major = magic_text[len(SAVE_MAGIC_PREFIX) : len(SAVE_MAGIC_PREFIX) + 3]
    warnings: List[str] = []

    offset = SAVE_HEADER_SIZE
    chunks: List[SaveChunk] = []
    bestiary_walked = False
    for order in range(1, CHUNK_COUNT + 1):
        if offset + 12 > len(raw):
            raise SaveFormatError(
                f"存档结构损坏：读到第 {order} 个数据块时已越过文件末尾（文件可能被截断）。"
            )
        chunk_type, length, count = struct.unpack_from("<iii", raw, offset)
        offset += 12
        if chunk_type == BESTIARY_CHUNK_TYPE:
            # 怪物图鉴（变长）：本插件只在报告里跳过这一段，不参与进度统计；
            # 结构本身已经解出（[段号][段长][每组 8 字节] ×4，见 tools/build_boss_table.py），
            # 这里按同一规则跳过，用来确认「整段对齐」这一完整性信号。
            chunks.append(SaveChunk(type=chunk_type, length=length, count=count, body_offset=offset))
            warnings.append("怪物图鉴（bestiary）段本插件只跳过、不参与统计。")
            if count >= 0:
                cursor = offset
                walked = True
                for _ in range(count):
                    if cursor + 8 > len(raw):
                        walked = False
                        break
                    _bestiary_type, bestiary_count = struct.unpack_from("<ii", raw, cursor)
                    cursor += 8 + max(bestiary_count // 4, 0) * 8
                if walked and cursor <= len(raw):
                    offset = cursor
                    bestiary_walked = True
            break
        width = CHUNK_WIDTH.get(chunk_type)
        if width is None:
            raise SaveFormatError(f"存档结构异常：出现未知数据块类型 {chunk_type}，无法继续解析。")
        if count < 0:
            raise SaveFormatError("存档结构异常：数据块条目数为负数，文件可能已损坏。")
        body_offset = offset
        offset += count * width
        if offset > len(raw):
            raise SaveFormatError(
                f"存档结构损坏：{CHUNK_LABELS.get(chunk_type, '数据块')} 段越过文件末尾（文件可能被截断）。"
            )
        index_base = CHUNK_INDEX_BASE.get(chunk_type, DEFAULT_CHUNK_INDEX_BASE)
        unlocked: Tuple[int, ...] = ()
        if width == 1:
            unlocked = tuple(index for index in range(index_base, count) if raw[body_offset + index])
        chunks.append(
            SaveChunk(
                type=chunk_type,
                length=length,
                count=count,
                body_offset=body_offset,
                unlocked=unlocked,
                index_base=index_base,
            )
        )

    by_type = {chunk.type: chunk for chunk in chunks}

    # 一致性校验：len 字段与 count × 4 的关系（不可信，但异常值值得提示）
    for chunk in chunks:
        if chunk.type in (1, BESTIARY_CHUNK_TYPE):
            continue
        if chunk.length not in (0, chunk.count * 4):
            warnings.append(f"{chunk.label}段的长度字段（{chunk.length}）与条目数不匹配，已按条目数解析。")

    counters: Tuple[int, ...] = ()
    counter_chunk = by_type.get(2)
    if counter_chunk is not None:
        available = (len(raw) - counter_chunk.body_offset) // 4
        usable = min(counter_chunk.count, max(available, 0))
        if usable:
            counters = struct.unpack_from(f"<{usable}i", raw, counter_chunk.body_offset)

    achievement_count = by_type[1].count if 1 in by_type else 0
    version_key = VERSION_BY_ACHIEVEMENT_COUNT.get(achievement_count, "")
    if not version_key:
        version_key = VERSION_BY_MAGIC_MAJOR.get(magic_major, "unknown")
        if version_key in ("rebirth", "afterbirth"):
            pass  # 老版本以魔数为准
        elif magic_major == "09R":
            version_key = "unknown"
            warnings.append(
                f"成就段条目数 {achievement_count} 不在已知范围（404 / 638 / 642），"
                "无法判断是胎衣+、忏悔还是忏悔+。"
            )

    trailing = max(len(raw) - offset, 0)
    if not bestiary_walked:
        # 怪物图鉴段没走通时，剩余字节数没有意义（不能当成「文件被截断」）
        trailing = None
    analysis = SaveAnalysis(
        file_name=Path(str(file_name or "")).name,
        file_size=len(raw),
        magic=magic_text,
        magic_major=magic_major,
        version_key=version_key,
        version_label=VERSION_LABELS.get(version_key, "未知版本"),
        slot=extract_slot(file_name),
        chunks=tuple(chunks),
        counters=counters,
        trailing_bytes=trailing,
        layout_ok=len(chunks) >= CHUNK_COUNT - 1 and bestiary_walked,
        bestiary_walked=bestiary_walked,
        warnings=tuple(warnings),
    )
    return analysis


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------


def build_report(
    analysis: SaveAnalysis,
    *,
    item_names: Optional[Mapping[str, str]] = None,
    name_tables: Optional[NameTables] = None,
    max_missing_named: int = 300,
) -> SaveReport:
    """把解析结果整理成展示用的报告。

    ``item_names`` 是「图鉴编号 → 道具中文名」的映射（来自插件内置道具数据），
    用于「还没拿到的道具」清单；``name_tables`` 是可选的成就 / BOSS / 挑战中文名与成就解锁条件。
    """

    tables = name_tables or NameTables()
    names = item_names or {}
    limit = max(int(max_missing_named), 1)

    undiscovered: List[UndiscoveredItem] = []
    for index in analysis.missing("collectibles"):
        item_id = str(index)
        undiscovered.append(UndiscoveredItem(item_id=item_id, name=str(names.get(item_id) or "").strip()))

    missing_named: Dict[str, Tuple[NamedEntry, ...]] = {}
    # 顺序即展示顺序：先挑战（未完成的可直接去打），再成就，最后是暂时没有名表的 BOSS / 小 BOSS
    for key in ("challenges", "achievements", "bosses", "minibosses"):
        table = dict(tables.names_for(key))
        if key == "achievements":
            # 成就详情表里也有名称（且带解锁条件），名称表缺项时用它兜底
            for index, detail in tables.achievement_details.items():
                if detail.name and index not in table:
                    table[index] = detail.name
        if not table:
            continue
        entries: List[NamedEntry] = []
        for index in analysis.missing(key):
            name = str(table.get(index) or "").strip()
            note = tables.note_for(key, index)
            if not name and not note:
                continue
            detail = ""
            if key == "achievements":
                record = tables.achievement_detail(index)
                if record is not None:
                    detail = record.condition
            if not detail:
                detail = note
            entries.append(NamedEntry(index=index, name=name, detail=detail))
            if len(entries) >= limit:
                break
        # 有名表就登记（哪怕一条也没匹配上），方便调用方区分「没名表」与「名表里没有这一项」
        missing_named[key] = tuple(entries)


    return SaveReport(
        analysis=analysis,
        tracks=analysis.tracks(),
        counters=analysis.counter_stats(),
        undiscovered=tuple(undiscovered),
        missing_named=missing_named,
        name_source=str(tables.meta.get("source") or "") if tables.meta else "",
    )


# ----------------------------------------------------------------------
# 文本排版
# ----------------------------------------------------------------------


def format_progress_line(track: ProgressTrack) -> str:
    return f"{track.label} {track.ratio_text}（{track.percent_text}）"


def format_report_text(
    report: SaveReport,
    *,
    max_undiscovered: int = 60,
    max_missing_named: int = 40,
    file_note: str = "",
) -> str:
    """排版详细的存档报告（合并转发正文）。"""

    analysis = report.analysis
    lines: List[str] = []
    lines.append(f"【存档解析】{analysis.file_name or '存档'}")
    meta_parts = [analysis.version_label]
    if analysis.slot:
        meta_parts.append(f"存档位 {analysis.slot}")
    meta_parts.append(f"{analysis.file_size / 1024:.1f} KB")
    if analysis.bestiary_walked and analysis.trailing_bytes is not None:
        meta_parts.append(f"整段对齐（尾部保留 {analysis.trailing_bytes} 字节）")
    lines.append(" · ".join(meta_parts))
    if file_note:
        lines.append(file_note)

    section = 0
    section += 1
    lines.append("")
    lines.append(f"{_section_label(section)}进度总览")
    for track in report.tracks:
        lines.append(f"· {format_progress_line(track)}")
    if analysis.is_empty:
        lines.append("（这个存档位没有任何进度，像是空存档——如果你有进度，可能是存档位选错了。）")

    if report.counters:
        section += 1
        lines.append("")
        lines.append(f"{_section_label(section)}全局统计")
        for stat in report.counters:
            lines.append(f"· {stat.label}：{stat.value:,}")

    collectibles = next((track for track in report.tracks if track.key == "collectibles"), None)
    if collectibles is not None:
        section += 1
        lines.append("")
        lines.append(f"{_section_label(section)}还没拿到的道具（{collectibles.remaining} 件）")
        if not report.undiscovered:
            lines.append("· 全部道具都已发现。")
        elif collectibles.unlocked == 0:
            lines.append(f"· 这一位还没发现任何道具（0/{collectibles.total}）。")
        else:
            shown = report.undiscovered[: max(int(max_undiscovered), 1)]
            for item in shown:
                if item.known:
                    lines.append(f"· {item.item_id} {item.name}")
                else:
                    lines.append(f"· {item.item_id}（图鉴未收录该编号，可能是空槽位）")
            if len(report.undiscovered) > len(shown):
                remaining = report.undiscovered[len(shown) :]
                lines.append("· 其余 " + str(len(remaining)) + " 件编号：" + "、".join(i.item_id for i in remaining))
            unknown = report.undiscovered_unknown
            if unknown:
                lines.append(
                    f"（其中 {len(unknown)} 个编号在图鉴里没有收录；已识别中文名 "
                    f"{len(report.undiscovered_known)} 个。）"
                )

    track_labels = {"achievements": "成就", "bosses": "BOSS", "minibosses": "小 BOSS", "challenges": "挑战"}
    track_headers = {
        "bosses": "存档没有记录的 BOSS",
        "minibosses": "存档没有记录的小 BOSS",
    }
    track_by_key = {track.key: track for track in report.tracks}
    limit = max(int(max_missing_named), 1)
    for key in ("challenges", "achievements", "bosses", "minibosses"):
        track = track_by_key.get(key)
        if track is None or track.remaining <= 0:
            continue
        entries = list(report.missing_named.get(key) or ())
        section += 1
        lines.append("")
        title = track_headers.get(key) or f"未解锁的{track_labels.get(key, key)}"
        lines.append(f"{_section_label(section)}{title}（{track.remaining} 个）")
        if key == "achievements":
            details = [entry.detail for entry in entries[:limit] if entry.detail]
            ascii_only = sum(1 for detail in details if detail and all(ord(ch) < 128 for ch in detail))
            if details and ascii_only * 2 >= len(details):
                lines.append("（条件取自游戏本体 achievements.xml 的英文原文——游戏内没有中文版本。）")
        if not entries:
            # 没有中文名表：只给编号，绝不猜名字
            ids = analysis.missing(key)
            lines.append("· 编号：" + "、".join(str(index) for index in ids[:limit]))
            if len(ids) > limit:
                lines.append(f"（其余 {len(ids) - limit} 个略）")
            lines.append("（这一项还没有中文名表，只列编号。）")
            continue
        shown = entries[:limit]
        for entry in shown:
            if entry.name:
                line = f"· {entry.index} {entry.name}"
                if entry.detail:
                    line += f"：{entry.detail}"
            else:
                line = f"· {entry.index} {entry.detail}"
            lines.append(line)
        hidden_named = entries[limit:]
        hidden_unnamed = max(track.remaining - len(entries), 0)
        if hidden_named:
            lines.append("· 其余编号：" + "、".join(str(entry.index) for entry in hidden_named))
        if hidden_named or hidden_unnamed:
            parts: List[str] = []
            if hidden_named:
                parts.append(f"其中 {len(hidden_named)} 个只列了编号")
            if hidden_unnamed:
                parts.append(f"{hidden_unnamed} 个暂无中文名表")
            lines.append(f"（共 {track.remaining} 个，{'；'.join(parts)}。）")

    lines.append("")
    notes: List[str] = ["只读解析，不会修改你的存档。"]
    if analysis.warnings:
        notes.extend(analysis.warnings)
    if report.name_source:
        notes.append(f"名称数据来源：{report.name_source}")
    else:
        notes.append("成就 / BOSS / 挑战的中文名称表尚未接入，只给出编号与计数，避免显示错误名称。")
    lines.append("说明：" + "".join(notes))
    return "\n".join(lines)


_SECTION_NUMBERS = "一二三四五六七八九十"


def _section_label(index: int) -> str:
    """把序号转成中文小标题前缀（``1`` → ``一、``）。"""

    if 1 <= index <= len(_SECTION_NUMBERS):
        return f"{_SECTION_NUMBERS[index - 1]}、"
    return f"{index}、"


def format_summary_text(report: SaveReport, *, command: str = "/以撒存档") -> str:
    """排版随图片一起发送的短说明。"""

    analysis = report.analysis
    head_parts = [analysis.version_label]
    if analysis.slot:
        head_parts.append(f"存档位 {analysis.slot}")
    if analysis.file_name:
        head_parts.append(analysis.file_name)
    lines = [f"【存档解析】{' · '.join(head_parts)}"]
    if analysis.is_empty:
        lines.append("这一位没有任何进度，像是空存档。")
    else:
        for track in report.tracks[:4]:
            lines.append(f"{track.label} {track.ratio_text}（{track.percent_text}）")
        lines.append(f"完整清单见下方合并转发，更多指令：{command}帮助")
    return "\n".join(lines)


def build_upload_guide(*, command: str = "以撒存档", prefix: str = "/") -> str:
    """构造「怎么把存档交给 bot」的指引（空发指令时使用）。"""

    cmd = f"{prefix}{command}"
    lines = [
        "还不认识你的存档，先把存档文件发给 bot 吧。",
        "",
        "【一、找到存档文件】",
        "存档在游戏目录之外，按版本在这些位置（文件名里的 1/2/3 是存档位）：",
        "· 忏悔+：文档\\My Games\\Binding of Isaac Repentance+\\rep+persistentgamedata1.dat",
        "· 忏悔 ：文档\\My Games\\Binding of Isaac Repentance\\rep_persistentgamedata1.dat",
        "· 胎衣+：文档\\My Games\\Binding of Isaac Afterbirth+\\abp_persistentgamedata1.dat",
        "· 开了 Steam 云存档的话，也可能在：Steam\\userdata\\<你的 SteamID>\\250900\\remote\\",
        "· Linux/Steam Deck 用 Proton 时在：~/.steam/steam/steamapps/compatdata/250900/pfx/drive_c/users/steamuser/Documents/My Games/…",
        "提示：要的是 persistentgamedata，不是 gamestate（那是「中途退出的单局」，统计不了进度）。",
        "",
        "【二、发给我】",
        f"1. 把 .dat 文件传到群文件（手机 QQ 可用「文件 → 上传到群文件」）；",
        f"2. 在群里引用（回复）那个文件消息，发送 {cmd}绑定；",
        f"3. 也可以直接把文件拖进聊天窗口发送，再说一句 {cmd}绑定。",
        "",
        "【三、其它用法】",
        f"{cmd}          查看解析结果（解析图 + 详细进度）",
        f"{cmd}绑定      绑定 / 更新你引用的存档",
        f"{cmd}2         查看存档位 2（如果你上传了多个存档位）",
        f"{cmd}清除      删除 bot 本地缓存的存档",
        "",
        "存档只会保存在 bot 本地用于解析，解析是只读的，不会修改你的存档。",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 本地缓存（按 QQ 号分目录）
# ----------------------------------------------------------------------


def sanitize_user_id(user_id: Any) -> str:
    """把平台用户 ID 变成安全的目录名（防路径穿越）。"""

    text = str(user_id or "").strip()
    safe = _UID_SAFE_RE.sub("", text)[:48]
    if safe:
        return safe
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"u{digest}"


class SaveStore:
    """按用户（QQ 号）缓存存档文件。

    目录结构：``<root>/<user_id>/slot1.dat`` + ``slot1.meta.json``。
    写入前一律先解析校验，非持久存档直接拒绝；写入使用临时文件 + 替换，避免半截文件。
    """

    def __init__(self, root: Path | str, *, max_bytes: int = DEFAULT_MAX_SAVE_BYTES) -> None:
        self.root = Path(root)
        self.max_bytes = max(int(max_bytes), 1024)

    # -- 路径 ----------------------------------------------------------

    def user_dir(self, user_id: Any) -> Path:
        return self.root / sanitize_user_id(user_id)

    def data_path(self, user_id: Any, slot: int) -> Path:
        return self.user_dir(user_id) / f"slot{int(slot)}.dat"

    def meta_path(self, user_id: Any, slot: int) -> Path:
        return self.user_dir(user_id) / f"slot{int(slot)}.meta.json"

    # -- 读写 ----------------------------------------------------------

    def put(
        self,
        user_id: Any,
        raw: bytes,
        *,
        file_name: str,
        source: str = "",
        max_slot: int = DEFAULT_MAX_SLOT,
    ) -> CachedSave:
        """校验并缓存一份存档，返回缓存条目（覆盖同一存档位的旧文件）。"""

        if len(raw) > self.max_bytes:
            raise SaveFormatError(
                f"文件太大（{len(raw) / 1024 / 1024:.1f} MB），看起来不是以撒存档"
                f"（真实存档通常只有几十 KB）。上限 {self.max_bytes / 1024 / 1024:.0f} MB。"
            )
        analysis = parse_save_bytes(raw, file_name=file_name)
        slot = analysis.slot or 1
        if slot > max_slot:
            slot = slot % max(max_slot, 1) or 1
        directory = self.user_dir(user_id)
        directory.mkdir(parents=True, exist_ok=True)
        data_path = self.data_path(user_id, slot)
        temp_path = data_path.with_suffix(".dat.tmp")
        temp_path.write_bytes(raw)
        temp_path.replace(data_path)

        digest = hashlib.sha256(raw).hexdigest()
        entry = CachedSave(
            user_id=sanitize_user_id(user_id),
            slot=slot,
            file_name=Path(str(file_name or "")).name or data_path.name,
            size=len(raw),
            version_key=analysis.version_key,
            version_label=analysis.version_label,
            bound_at=now_iso(),
            source=str(source or "").strip(),
            sha256=digest,
            path=data_path,
        )
        meta = {
            "user_id": entry.user_id,
            "slot": entry.slot,
            "file_name": entry.file_name,
            "size": entry.size,
            "version_key": entry.version_key,
            "version_label": entry.version_label,
            "bound_at": entry.bound_at,
            "source": entry.source,
            "sha256": entry.sha256,
        }
        self.meta_path(user_id, slot).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return entry

    def get(self, user_id: Any, slot: Optional[int] = None, *, max_slot: int = DEFAULT_MAX_SLOT) -> Optional[CachedSave]:
        """读取缓存；``slot`` 为空时优先返回最近绑定的一份。"""

        entries = self.slots(user_id, max_slot=max_slot)
        if not entries:
            return None
        if slot is None:
            return entries[0]
        for entry in entries:
            if entry.slot == int(slot):
                return entry
        return None

    def slots(self, user_id: Any, *, max_slot: int = DEFAULT_MAX_SLOT) -> List[CachedSave]:
        """列出某个用户已缓存的全部存档位，按绑定时间倒序。"""

        directory = self.user_dir(user_id)
        if not directory.is_dir():
            return []
        entries: List[CachedSave] = []
        for meta_file in sorted(directory.glob("slot*.meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            try:
                slot = int(meta.get("slot") or 0)
            except (TypeError, ValueError):
                continue
            if slot <= 0 or slot > max_slot:
                continue
            data_path = self.data_path(user_id, slot)
            if not data_path.is_file():
                continue
            entries.append(
                CachedSave(
                    user_id=sanitize_user_id(user_id),
                    slot=slot,
                    file_name=str(meta.get("file_name") or data_path.name),
                    size=int(meta.get("size") or data_path.stat().st_size),
                    version_key=str(meta.get("version_key") or "unknown"),
                    version_label=str(meta.get("version_label") or VERSION_LABELS["unknown"]),
                    bound_at=str(meta.get("bound_at") or ""),
                    source=str(meta.get("source") or ""),
                    sha256=str(meta.get("sha256") or ""),
                    path=data_path,
                )
            )
        entries.sort(key=lambda entry: (entry.bound_at, entry.slot), reverse=True)
        return entries

    def remove(self, user_id: Any, slot: Optional[int] = None, *, max_slot: int = DEFAULT_MAX_SLOT) -> int:
        """删除缓存，返回删除的存档份数。"""

        targets = self.slots(user_id, max_slot=max_slot)
        if slot is not None:
            targets = [entry for entry in targets if entry.slot == int(slot)]
        removed = 0
        for entry in targets:
            for path in (entry.path, self.meta_path(user_id, entry.slot)):
                try:
                    path.unlink()
                except OSError:
                    continue
            removed += 1
        directory = self.user_dir(user_id)
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass
        return removed

    def prune(self, *, ttl_days: int, max_slot: int = DEFAULT_MAX_SLOT) -> int:
        """清理超过 ``ttl_days`` 天未更新的缓存（``ttl_days <= 0`` 表示不清理）。"""

        if ttl_days <= 0 or not self.root.is_dir():
            return 0
        deadline = time.time() - ttl_days * 86400
        removed = 0
        for user_dir in self.root.iterdir():
            if not user_dir.is_dir():
                continue
            for entry in self.slots(user_dir.name, max_slot=max_slot):
                try:
                    updated = entry.path.stat().st_mtime
                except OSError:
                    continue
                if updated < deadline:
                    for path in (entry.path, self.meta_path(user_dir.name, entry.slot)):
                        try:
                            path.unlink()
                        except OSError:
                            continue
                    removed += 1
        return removed

    def stats(self) -> Dict[str, int]:
        """统计缓存规模，便于日志与自检。"""

        users = files = total_bytes = 0
        if self.root.is_dir():
            for user_dir in self.root.iterdir():
                if not user_dir.is_dir():
                    continue
                users += 1
                for data_file in user_dir.glob("slot*.dat"):
                    files += 1
                    try:
                        total_bytes += data_file.stat().st_size
                    except OSError:
                        continue
        return {"users": users, "saves": files, "bytes": total_bytes}


def format_cache_list(entries: Sequence[CachedSave]) -> str:
    """把已缓存的存档列表排版成文本。"""

    if not entries:
        return "本地还没有缓存任何存档。"
    lines = ["已缓存的存档："]
    for entry in entries:
        source = f"｜来源 {entry.source}" if entry.source else ""
        lines.append(
            f"· 存档位 {entry.slot}：{entry.file_name}（{entry.size_text}，{entry.version_label}）"
            f"\n  绑定于 {entry.bound_at_text}{source}"
        )
    return "\n".join(lines)
