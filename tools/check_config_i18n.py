#!/usr/bin/env python
"""配置项汉化自检：确认 WebUI 上不会露出英文字段名。

背景：WebUI 渲染一个配置字段时，标题取 ``json_schema_extra["label"]``、副标题取
``json_schema_extra["hint"]``（``maibot_sdk/config.py::_build_field_schema``），
**两者都缺时标题会兜底成英文字段名**（``label = json_extra.get("label") or field_name``）；
``Field(description=…)`` 只进 Schema、前端不读它。配置节的标题取 ``__ui_label__``、
描述取**配置模型类的 ``__doc__``**（不写 docstring 就是空）。
所以「汉化」= 每个字段补 ``label`` + ``hint``、每个节类补 ``__ui_label__`` + docstring。

检查项
------
1. 每个 ``PluginConfigBase`` 子类都有非空 ``__ui_label__`` 与非空 docstring；
2. 每个配置字段（类型不是另一个配置节类的字段）都有 ``json_schema_extra``，
   且其中 ``label`` / ``hint`` 非空；
3. 上述文案都含非 ASCII 字符（即真的写了中文，而不是把英文字段名抄一遍）；
4. ``hint`` 不超过 ``MAX_HINT_CHARS`` 字（默认 15，沿用 cateye 系列插件的既有约定：
   悬停提示只留一句关键约束，完整解释留在 ``Field(description=…)`` 与 README）；
5. 装了 ``maibot_sdk`` 时再用**真实 Schema** 复核一遍
   （``plugin.get_webui_config_schema()["sections"]``，是 dict 不是 list）。

用法::

    python tools/check_config_i18n.py              # 在插件目录内运行（静态检查，无需依赖）
    python tools/check_config_i18n.py --root <插件目录>
    # 有 maibot_sdk 的解释器（如宿主 venv）会额外跑真实 Schema 校验：
    E:/maibot/MaiBot/.venv/Scripts/python.exe tools/check_config_i18n.py
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

#: hint 字数上限（cateye 系列插件约定：悬停提示要短）
MAX_HINT_CHARS = 15


def is_localized(text: str) -> bool:
    """非空，且含非 ASCII 字符（即确实写了中文，而不是回落到英文字段名）。"""

    return bool(text and text.strip()) and not text.isascii()


def _str_arg(call: ast.Call, keyword: str) -> str:
    """取出 ``call`` 里某个关键字参数的字符串字面量（支持隐式拼接的多段字符串）。"""

    for kw in call.keywords:
        if kw.arg != keyword:
            continue
        try:
            value = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError):
            return ""
        return str(value) if value is not None else ""
    return ""


def _json_extra(call: ast.Call) -> dict[str, str]:
    """把 ``json_schema_extra={...}`` 里的字符串键值对抽出来（非字面量则忽略）。"""

    for kw in call.keywords:
        if kw.arg != "json_schema_extra" or not isinstance(kw.value, ast.Dict):
            continue
        out: dict[str, str] = {}
        for key_node, value_node in zip(kw.value.keys, kw.value.values):
            try:
                key = ast.literal_eval(key_node)  # type: ignore[arg-type]
                value = ast.literal_eval(value_node)
            except (ValueError, SyntaxError):
                continue
            out[str(key)] = str(value)
        return out
    return {}


def _is_config_class(node: ast.ClassDef) -> bool:
    """判断是不是 ``PluginConfigBase`` 的子类（含 ``maibot_sdk`` 里的写法）。"""

    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if name == "PluginConfigBase":
            return True
    return False


def _has_factory(call: ast.Call) -> bool:
    """``Field(default_factory=…)`` 多为配置节容器（如 ``save: SaveSectionConfig``）。

    ⚠️ 仅作参考：**不能**用它判断"是不是可编辑字段"——``group_list: list[str] =
    Field(default_factory=list)`` 这类列表字段是可编辑的。真正的判据见
    :func:`_annotation_name`（字段类型是否为另一个配置节类）。
    """

    return any(kw.arg == "default_factory" for kw in call.keywords)


def _section_class_names(tree: ast.Module) -> set[str]:
    """本模块里所有 ``PluginConfigBase`` 子类的类名（= 配置节容器的类型名）。"""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _is_config_class(node):
            names.add(node.name)
    return names


def _annotation_name(stmt: ast.AnnAssign) -> str:
    """取字段标注里的类名（``save: SaveSectionConfig`` → ``SaveSectionConfig``）。"""

    ann = stmt.annotation
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Attribute):
        return ann.attr
    if isinstance(ann, ast.Subscript):  # list[str] / Optional[X] 之类
        inner = ann.slice
        if isinstance(inner, ast.Name):
            return inner.id
        if isinstance(inner, ast.Attribute):
            return inner.attr
    return ""


def check_static(plugin_py: Path) -> tuple[list[str], int, int]:
    """静态检查：返回 ``(问题列表, 配置节数, 字段数)``。"""

    tree = ast.parse(plugin_py.read_text(encoding="utf-8"), filename=str(plugin_py))
    section_classes = _section_class_names(tree)
    problems: list[str] = []
    section_count = 0
    field_count = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not _is_config_class(node):
            continue
        # 只统计"真正的配置节"：类体里至少有一个可编辑字段。
        # 判据是「字段类型不是另一个配置节类」（如 ``save: SaveSectionConfig``），
        # 根配置类（如 ``IsaacItemConfig``）里全是这种节容器，要排除。
        # ⚠️ 别用 ``default_factory`` 判断——``group_list: list[str] = Field(default_factory=list)``
        # 这种列表字段是可编辑的，用 default_factory 排除会漏计。
        field_assigns = [
            stmt
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.value, ast.Call)
            and getattr(stmt.value.func, "id", "") == "Field"
            and _annotation_name(stmt) not in section_classes
        ]
        if not field_assigns:
            continue
        section_count += 1

        ui_label = ""
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and any(
                getattr(t, "id", "") == "__ui_label__" for t in stmt.targets
            ):
                try:
                    ui_label = str(ast.literal_eval(stmt.value))
                except (ValueError, SyntaxError):
                    ui_label = ""
        if not is_localized(ui_label):
            problems.append(f"[节 {node.name}] __ui_label__ 缺失或未汉化：{ui_label!r}")
        doc = (ast.get_docstring(node) or "").strip()
        if not is_localized(doc):
            problems.append(f"[节 {node.name}] docstring（= WebUI 节描述）缺失或未汉化：{doc!r}")

        for stmt in field_assigns:
            call = stmt.value
            assert isinstance(call, ast.Call)
            field_count += 1
            name = getattr(stmt.target, "id", "<unknown>")
            extra = _json_extra(call)
            if not extra:
                problems.append(f"[节 {node.name}] 字段 {name} 没有 json_schema_extra（UI 会显示英文字段名）")
                continue
            for key in ("label", "hint"):
                if not is_localized(extra.get(key, "")):
                    problems.append(
                        f"[节 {node.name}] 字段 {name} 的 {key} 缺失或未汉化：{extra.get(key, '')!r}"
                    )
            hint = extra.get("hint", "")
            if len(hint) > MAX_HINT_CHARS:
                problems.append(
                    f"[节 {node.name}] 字段 {name} 的 hint 超过 {MAX_HINT_CHARS} 字"
                    f"（{len(hint)}）：{hint!r}"
                )
    return problems, section_count, field_count


def check_real_schema(plugin_root: Path) -> tuple[list[str], int, int]:
    """用真实 SDK 生成 WebUI Schema 再复核一遍（需要 ``maibot_sdk``）。"""

    sys.path.insert(0, str(plugin_root.parent))
    import importlib

    module = importlib.import_module(f"{plugin_root.name}.plugin")
    plugin = module.create_plugin()
    plugin.set_plugin_config(
        {"plugin": {"enabled": True, "config_version": module.SUPPORTED_CONFIG_VERSION}}
    )
    schema = plugin.get_webui_config_schema(plugin_id=plugin_root.name)
    sections = schema.get("sections")
    assert isinstance(sections, dict), f"sections 应为 dict，实际 {type(sections)}"

    problems: list[str] = []
    field_count = 0
    for section_name, section in sections.items():
        if not is_localized(str(section.get("title") or "")):
            problems.append(f"[节 {section_name}] title 未汉化：{section.get('title')!r}")
        if not is_localized(str(section.get("description") or "")):
            problems.append(f"[节 {section_name}] description 未汉化：{section.get('description')!r}")
        for name, field in (section.get("fields") or {}).items():
            field_count += 1
            for key in ("label", "hint"):
                if not is_localized(str(field.get(key) or "")):
                    problems.append(
                        f"[节 {section_name}] 字段 {name} 的 {key} 缺失或未汉化：{field.get(key)!r}"
                    )
            hint = str(field.get("hint") or "")
            if len(hint) > MAX_HINT_CHARS:
                problems.append(
                    f"[节 {section_name}] 字段 {name} 的 hint 超过 {MAX_HINT_CHARS} 字"
                    f"（{len(hint)}）：{hint!r}"
                )
    return problems, len(sections), field_count


def main() -> int:
    global MAX_HINT_CHARS  # noqa: PLW0603 - 允许命令行覆盖默认约定

    parser = argparse.ArgumentParser(description="插件配置项汉化自检")
    parser.add_argument("--root", type=Path, default=PLUGIN_ROOT, help="插件目录（默认取脚本所在目录的上级）")
    parser.add_argument("--no-sdk", action="store_true", help="跳过真实 Schema 校验，只做静态检查")
    parser.add_argument(
        "--max-hint",
        type=int,
        default=MAX_HINT_CHARS,
        help=f"hint 字数上限（默认 {MAX_HINT_CHARS}）",
    )
    args = parser.parse_args()
    MAX_HINT_CHARS = args.max_hint

    plugin_py = args.root / "plugin.py"
    if not plugin_py.is_file():
        print(f"❌ 找不到 {plugin_py}")
        return 2

    problems, sections, fields = check_static(plugin_py)
    print(f"[静态检查] 配置节 {sections} 个，字段 {fields} 个，问题 {len(problems)} 个")

    used_real_schema = False
    if not args.no_sdk:
        try:
            real_problems, real_sections, real_fields = check_real_schema(args.root.resolve())
        except ImportError as exc:
            print(f"[真实 Schema] 跳过（本解释器没有 maibot_sdk：{exc}）")
        except Exception as exc:  # noqa: BLE001 - 自检脚本要打印原因而不是崩掉
            print(f"[真实 Schema] ❌ 生成 Schema 失败：{exc!r}")
            return 1
        else:
            used_real_schema = True
            problems += real_problems
            print(
                f"[真实 Schema] 配置节 {real_sections} 个，字段 {real_fields} 个，问题 {len(real_problems)} 个"
            )

    if problems:
        print(f"\n❌ 共 {len(problems)} 个问题：")
        for item in problems:
            print("   -", item)
        return 1

    mode = "静态 + 真实 Schema" if used_real_schema else "仅静态"
    print(
        f"\n✅ 通过（{mode}）：所有配置节与字段都有中文 label / hint / title / description，"
        f"且 hint ≤ {MAX_HINT_CHARS} 字"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
