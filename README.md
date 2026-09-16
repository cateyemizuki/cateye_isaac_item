# cateye_isaac_item — 以撒道具图鉴

《以撒的结合》道具图鉴查询插件（MaiBot Manifest v2）。

在聊天里用 `/以撒 硫磺火` 查道具，也可以让麦麦在对话里自己查（LLM 工具 `isaac_item_lookup`）。
数据是打包在插件内的**离线快照**，运行时不联网、不请求任何接口。

## 功能

- **多种查法**：中文名、英文名、图鉴编号、标签、效果关键词，都支持。
- **容错检索**：名称记不全或打错字时自动模糊匹配（阈值可配置）。
- **协同检索**：关键词在名称/标签/效果里都匹配不到时，继续在协同条目里找——搜「不再叠加」能搜到变异蜘蛛，
  搜「错误王冠」能搜到六面骰、大胃王（即"和 X 有协同的道具"）。
- **详情完整**：品质（0~4）、类型、标签、效果正文、套装、协同条目。
- **协同不折叠**：协同条数超过内联上限（默认 5）时，正文只留一行提示，**全部协同改用合并转发发送**，
  不再只给前几条；合并转发不可用时自动回退普通文本。
- **离线可用**：数据随插件打包，无网络依赖、无第三方 Python 包依赖。

## 安装

把整个 `cateye_isaac_item` 目录放进 MaiBot 安装目录的 `plugins/` 下，重启 MaiBot
（或在 WebUI 插件管理中启用）。插件不需要任何额外依赖，也不需要联网。

## 指令

| 指令 | 说明 |
|---|---|
| `/以撒 <关键词>` | 查询条目；唯一或精确命中时直接给详情，否则给候选列表 |
| `/以撒详情 <关键词>` | 只看完整效果与协同 |
| `/以撒协同 <关键词>` | 查看该道具的全部协同 |
| `/以撒编号 <编号>` | 按图鉴编号查询 |
| `/以撒随机 [分类]` | 随机抽一个，分类可选 `道具`/`饰品`/`卡牌`/`药丸` |
| `/以撒` / `/以撒帮助` | 帮助与数据来源 |

`/以撒` 与 `/isaac` 等价，`/以撒道具`、`/以撒图鉴` 也都能触发。完整示例见 [COMMANDS.md](COMMANDS.md)。

## LLM 工具

| 工具 | 参数 | 说明 |
|---|---|---|
| `isaac_item_lookup` | `query`（必填）、`detail`（可选，默认 false） | 返回已排版的图鉴文本，供模型组织回答 |

工具只返回文本给模型，不会自己往群里发消息；需要主动发消息时用上面的指令。

## 配置

WebUI / `plugins/cateye_isaac_item/config.toml` 下共四节：

- `[plugin]`：`enabled`（是否启用）、`config_version`（与插件版本同步，隐藏项）。
- `[search]`：`max_results`（列表模式返回条数，默认 5）、`fuzzy_threshold`（模糊匹配阈值，默认 0.55，填 0 关闭）、
  `search_by_tag`、`search_by_effect`、`search_by_synergy`（名称匹配不到时依次退到标签、效果正文、协同正文）、`max_query_length`。
- `[display]`：`show_english`、`show_quality`、`show_tags`、`show_synergies`、
  `max_synergies`（**内联**展示的协同条数上限，默认 5；超过则全部协同改用合并转发发送，`0` = 全部内联展示）、
  `max_synergies_in_tool`（LLM 工具返回时最多列出的协同条数，默认 20；工具发不了转发，只能截断并注明条数）、
  `max_effect_chars`（效果正文截断字数，0 = 不截断）、
  `show_wiki_link`（效果疑似被表格截断且本地未补录时，是否附上灰机 wiki 页面链接，默认开启）、
  `forward_threshold`（超过多少字改用合并转发，默认 1000，0 = 始终普通文本）。
- `[data]`：`data_file`（自定义数据文件路径，留空用内置数据）、`show_source_in_help`。

## 数据重建

数据是快照，游戏更新后可以自己重新生成（需要能访问 GitHub）：

```bash
cd cateye_isaac_item
python tools/build_data.py                                  # 自动下载上游最新数据
python tools/build_data.py --raw path/to/items.db.json      # 用本地已下载的原始文件
```

脚本会重写 `assets/isaac_items.json`，并打印条目数与分类统计；同时自动合并
`assets/isaac_extra.json` 里人工补录的表格/列表。

## 已知限制

1. **wiki 表格类效果靠人工补录**：上游爬虫只解析页面里的 `<li>` 列表项、**不解析 `<table>`**，
   所以 wiki 里用表格描述效果的道具（潘多拉魔盒、透明符文、安慰剂……）在本地数据里会以「……触发以下效果：」断句结尾。
   这类条目共 13 条，**已全部补录**（内容在 `assets/isaac_extra.json`）。
   补录方法：编辑该文件（按图鉴编号加 `table` / `extra_lines`），再执行 `python tools/build_data.py`；
   若将来出现未补录的截断条目，插件会自动附上灰机 wiki 页面链接作为兜底（`display.show_wiki_link` 可关）。
2. **饰品 / 卡牌 / 药丸只有名称**：上游对这三类抓到的效果与协同是错位的（例如饰品「妈妈的脚趾甲」抓到了道具
   「魔法蘑菇」的效果），因此转换时一律丢弃，只保留名称与编号；查询这些条目时插件会明确说明「数据源未提供」。
3. **`en` 字段不全是英文名**：上游有 61 条填的是英文风味文本（如 147 残损铁镐填的是 `Rocks don't stand a chance`），
   展示时按原样输出；不想看可以在配置里关掉 `display.show_english`。
4. **`type` 字段不可靠**：上游把所有道具都标成 `passive`，所以插件不展示「主动/被动」，只展示「道具/饰品/卡牌/药丸」。
5. **数据是快照**：新版本游戏新增的道具需要重新生成数据。
6. **品质为 -1 表示未知**：饰品/卡牌/药丸没有品质数据，展示时会自动省略。

## 开发

- `isaac_data.py` 是纯逻辑模块（加载 / 检索 / 排版），不依赖 `maibot_sdk`，可以离线单测：

  ```bash
  python -c "from isaac_data import ItemDatabase; db=ItemDatabase.load('assets/isaac_items.json'); print(db.stats())"
  ```

- `plugin.py` 只负责配置、指令、工具与发送；改代码后用 WebUI 重载插件即可，
  改 `_manifest.json`（能力声明等）需要完整重启 MaiBot。

## 致谢

数据与知识来自以下来源，谨此致谢：

- **以撒的结合中文 Wiki（灰机 wiki）** — <https://isaac.huijiwiki.com/>：
  道具 / 饰品 / 卡牌 / 药丸的名称、品质、标签、效果与协同均出自该 wiki 的社区词条。
- **上游开源项目 [ChenDekang617/isaac-item-recommender](https://github.com/ChenDekang617/isaac-item-recommender)** —
  其 `knowledge/items.db.json` 是作者用爬虫从以撒 Wiki（灰机 wiki）抓取整理的道具数据库；
  本插件用 [tools/build_data.py](tools/build_data.py) 把它转换为插件内置的精简快照
  （`assets/isaac_items.json`，1039 条：道具 715 / 饰品 183 / 卡牌 94 / 药丸 47）。
- **人工补录**：13 条在 wiki 中以表格描述效果的道具（潘多拉魔盒、透明符文、安慰剂……）
  由本插件按灰机 wiki 页面原文逐条转写，每条的出处链接记录在
  `assets/isaac_extra.json` 的 `source` 字段。

### 许可说明

- 本插件的**代码**以 [MIT](LICENSE) 许可发布。
- 内置的**数据**来自灰机 wiki（社区 wiki 内容通常采用 **CC BY-NC-SA** 系许可），
  上游仓库**未声明 LICENSE**；字段可信度差异见上文「已知限制」，插件会如实标注
  「数据源未提供」，不编造内容。
- 因此本插件**仅随 MaiBot 插件市场发布、用于非商业用途**。如需商业使用或再分发
  内置数据，请先自行向灰机 wiki 与上游项目确认授权。
