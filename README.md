# 以撒道具图鉴 / 存档解析

《以撒的结合》道具图鉴查询插件（MaiBot Manifest v2）。

在聊天里用 `/以撒 硫磺火` 查道具，也可以让麦麦在对话里自己查（LLM 工具 `isaac_item_lookup`）。
数据是打包在插件内的**离线快照**，运行时不联网、不请求任何接口。

另外提供**存档解析**：用户把游戏存档 `.dat` 传到群文件，引用它发送 `/以撒存档绑定`，
插件下载校验后按 QQ 号缓存在 bot 本地，之后 `/以撒存档` 就能拿到一张**进度解析图** +
一条合并转发的详细统计（成就 / 图鉴 / 挑战完成度、全局计数器、还没拿到的道具清单）。

## 许可（请先阅读）

**本仓库整体按 [CC BY-NC-SA 4.0](LICENSE)（署名—非商业性—相同方式共享 4.0 国际）发布**，
`LICENSE` 文件即该协议全文。整包**仅供非商业用途**：使用 / 修改 / 再分发需**署名**（本仓库与下方数据来源）、
**不得用于商业目的**，改编产物需以**相同许可**共享。MaiBot 插件市场展示的许可即 CC BY-NC-SA 4.0。

其中各部分受控情况：

- **受 CC BY-NC-SA 控制的数据**（wiki 社区内容，需署名、非商业、相同方式共享）：

  | 文件 | 内容与来源 |
  |---|---|
  | `assets/isaac_items.json` | 道具 / 饰品 / 卡牌 / 药丸图鉴，灰机 wiki（经上游 isaac-item-recommender 整理） |
  | `assets/isaac_extra.json` | 13 条表格类效果的人工补录，转写自灰机 wiki 页面原文 |
  | `assets/isaac_challenges_zh.json` | 挑战中文名，灰机 wiki「挑战」页面 |
  | `assets/isaac_achievements_zh.json` | 成就中文表，混合来源（英文 wiki CC BY-SA 4.0 + 灰机 wiki CC BY-NC-SA 3.0），对外按更严格的 CC BY-NC-SA 对待 |

- **不受 CC 控制的内容，以 MIT 许可开源**（许可全文附下）：全部代码（含 `tools/` 下的脚本）、
  `assets/` 里上表之外的数据文件（游戏本体语言包提取的官方译名、`isaac_achievements.json` 成就基线表、
  `isaac_boss_slots.json` BOSS 对照表等）、以及本文档之外的自查文档。
- **字体**：Noto Sans SC 子集，SIL Open Font License 1.1（全文见 `assets/fonts/OFL.txt`），与两者都兼容。

MIT 许可全文（适用于上条所列代码与数据）：

```text
MIT License

Copyright (c) 2026 cateye

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

> 一句话版：**整包是「非商业 + 署名 + 相同方式共享」的 CC BY-NC-SA 4.0**；只想复用代码的人，
> 取上表之外的 `.py` 与数据文件即可，那部分按 MIT 随便用（含商用）。逐文件来源清单见
> 「致谢 → 许可说明」与 `tools/check_licenses.py` 的 `SOURCE_LICENSE_MAP`。

## 功能

- **多种查法**：中文名、英文名、图鉴编号、标签、效果关键词，都支持。
- **容错检索**：名称记不全或打错字时自动模糊匹配（阈值可配置）。
- **协同检索**：关键词在名称/标签/效果里都匹配不到时，继续在协同条目里找——搜「不再叠加」能搜到变异蜘蛛，
  搜「错误王冠」能搜到六面骰、大胃王（即"和 X 有协同的道具"）。
- **详情完整**：品质（0~4）、类型、标签、效果正文、套装、协同条目。
- **协同不折叠**：协同条数超过内联上限（默认 5）时，正文只留一行提示，**全部协同改用合并转发发送**，
  不再只给前几条；合并转发不可用时自动回退普通文本。
- **存档解析（只读）**：识别胎衣+ / 忏悔 / 忏悔+ 三种格式，解析 11 个数据块，输出成就、道具发现、
  BOSS / 小 BOSS 记录、挑战、特殊种子的完成度与 10 项已确认的全局统计，并列出**还没拿到的道具中文名**、
  **还没完成的挑战**、**还没解锁的成就（带解锁条件）**与**存档没有记录的 BOSS（含逐条原因）**。
- **解析图**：由插件**本地绘制**（Pillow），不依赖宿主的浏览器渲染能力——实测宿主 `render.html2png`
  经常超时失败，所以干脆不用它，插件也不再声明该能力。绘制所需的中文字体随插件打包
  （内置 Noto Sans SC 子集，见 `assets/fonts/`），因此在没装中文字体的 Linux 上同样能出图；
  Pillow 不可用时才退化为文字摘要。
  版式是瘦高型：宽度由 `save.image_width` 控制（默认 700 CSS 像素，统计卡按宽度自动 4/3/2 列，
  末行不满时居中），倍率由 `save.image_scale` 控制（默认 1.4，默认输出约 980×1645）。
- **不刷屏**：一条回复**字数超过 `forward_threshold`（默认 400）或行数超过 `forward_max_lines`
  （默认 12）**时改用合并转发发送——帮助、候选列表、长效果条目都会自动变成一条聊天记录，
  聊天窗里只占一条；需要同时发完整协同的详情（如 `/以撒 硫磺火`）会把正文与协同**合并成同一条转发**，
  而不是「一条正文 + 一条转发」。短回复（如 `/以撒 血袋`）仍是普通文本。
  合并转发不可用时逐段回退为普通文本（超长时按 1200 字切块），不会因为发不出去就丢内容；
  解析图单独一条消息。
- **离线可用**：图鉴数据与渲染字体都随插件打包，无网络依赖；除渲染用的 Pillow 外不需要第三方包。


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
| `/以撒存档`（=`/以撒进度`） | 查看解析图 + 详细进度；还没绑定时给出「去哪找存档」的指引 |
| `/以撒存档绑定` | 引用（回复）群文件里的 `.dat` 后绑定 / 更新存档 |
| `/以撒存档2` | 查看存档位 2（把 `2` 换成 `1`~`3`） |
| `/以撒存档列表` | 查看已缓存的存档 |
| `/以撒存档清除` | 删除 bot 本地缓存的存档 |

`/以撒` 与 `/isaac` 等价，`/以撒道具`、`/以撒图鉴` 也都能触发。完整示例见 [COMMANDS.md](COMMANDS.md)。

## 存档解析

### 用户视角的三步

1. 找到存档文件（`/以撒存档` 空发时插件会把位置指引发给用户）：

   | 版本 | 位置 | 文件名 |
   |---|---|---|
   | 忏悔+ | `文档\My Games\Binding of Isaac Repentance+\` | `rep+persistentgamedata1.dat` |
   | 忏悔 | `文档\My Games\Binding of Isaac Repentance\` | `rep_persistentgamedata1.dat` |
   | 胎衣+ | `文档\My Games\Binding of Isaac Afterbirth+\` | `abp_persistentgamedata1.dat` |
   | Steam 云存档 | `Steam\userdata\<SteamID>\250900\remote\` | 同上 |

2. 把 `.dat` 传到群文件（或直接拖进聊天窗口），**引用那条文件消息**后发送 `/以撒存档绑定`；
3. 之后随时 `/以撒存档` 看解析结果。

### 插件侧做了什么

- **取文件**：优先用消息里带的下载链接；没有直链时按文件名调用 Napcat 适配器的
  `get_group_root_files` + `get_group_file_url` 取链接（可在配置里关掉）。
- **校验**：魔数、长度、文件名、数据块边界全部校验；`gamestate`（中途退出的单局）会被明确拒绝并说明原因；
  超过大小上限、非 http(s)、指向内网 / 回环地址的链接都会被拒绝（SSRF 防护，可用 `allow_private_url` 放开）。
- **缓存**：`ctx.paths.data_dir/saves/<QQ号>/slotN.dat` + `.meta.json`，按 QQ 号隔离，
  同一存档位重复上传即覆盖；缓存默认 **30 天后自动过期**（`cache_ttl_days` 可调，`0` = 永久保留），
  也可随时 `/以撒存档清除` 手动删除。
- **解析**：`isaac_save.py` 纯逻辑模块，只读字节流，不写任何存档文件。
- **输出**：一条解析图 + 一条合并转发的详情（避免刷屏）。

### 支持范围

| 项目 | 说明 |
|---|---|
| 版本识别 | 魔数区分重生 / 胎衣；`09R` 再用成就段条目数区分胎衣+（349 / 404）/ 忏悔（638）/ 忏悔+（642） |
| 进度项 | 成就、道具发现、BOSS 记录（104）、小 BOSS 记录（7 = 七宗罪）、挑战、特殊种子（各给 `已得/总数` 与百分比） |
| 下标基准 | 成就 / 道具 / 挑战 / 特殊种子的**第 0 位未使用**（总数 = `count − 1`）；**小 BOSS 与 BOSS 段的第 0 位是真实条目**（总数 = `count`），见「已知限制」8 |
| 全局统计 | 妈妈击杀、死亡次数、店主击杀、碎石数、大便破坏、染色石破坏、**累计捐款**、伊甸币、最佳连胜、当前连胜（10 项） |
| 未发现道具 | 复用内置图鉴的 715 条道具中文名，未收录的编号会标注「图鉴未收录，可能是空槽位」 |
| 未完成挑战 / 未解锁成就 | 用内置名称表给出**中文名**；成就还带**解锁条件**（如「通过挑战#15：慢吞吞」），照着打就行 |
| 未遇到的小 BOSS | 七宗罪（懒惰 / 色欲 / 愤怒 / 暴食 / 贪婪 / 嫉妒 / 傲慢）按名字列出 |
| BOSS 名 | **给出中文名**：104 个槽位的下标就是游戏 `entities2.xml` 里的 `bossID` 属性，名称取自游戏本体语言包（官方简体译名），逐条依据见 `assets/isaac_boss_slots.json` |
| BOSS 段特殊槽位 | 3 个未分配空槽位（89 / 90 / 103）、1 个旧版保留位（0）、2 个游戏不写入的槽位（62 究极贪婪 / 71 究极大贪婪）都会在清单里**带上说明**，不会误报成「你没打过」 |
| 怪物图鉴 | **不参与统计**：`bestiary` 段只在报告里跳过（该段结构已解出，见 `tools/build_boss_table.py` 的核对逻辑） |


## LLM 工具

| 工具 | 参数 | 说明 |
|---|---|---|
| `isaac_item_lookup` | `query`（必填）、`detail`（可选，默认 false） | 返回已排版的图鉴文本，供模型组织回答 |

工具只返回文本给模型，不会自己往群里发消息；需要主动发消息时用上面的指令。

## 配置

WebUI / `plugins/cateye_isaac_item/config.toml` 下共五节。
括号里是 **WebUI 上显示的中文标签**（`config.toml` 里的键名仍是英文，WebUI 通过字段的 `json_schema_extra.label/hint` 做本地化）：

- `[plugin]`：`enabled`（启用插件）、`config_version`（配置版本，隐藏项）。
- `[search]`：`max_results`（结果条数上限，默认 5）、`fuzzy_threshold`（模糊匹配阈值，默认 0.55，填 0 关闭）、
  `search_by_tag`（按标签检索）、`search_by_effect`（按效果检索）、`search_by_synergy`（按协同检索）
  ——名称匹配不到时依次退到标签、效果正文、协同正文，`max_query_length`（关键词长度上限）。
- `[display]`：`show_english`（显示英文名）、`show_quality`（显示品质）、`show_tags`（显示标签）、`show_synergies`（显示协同）、
  `max_synergies`（内联协同上限，默认 5；超过则全部协同改用合并转发发送，`0` = 全部内联展示）、
  `max_synergies_in_tool`（工具协同上限，默认 20；工具发不了转发，只能截断并注明条数）、
  `max_effect_chars`（效果字数上限，0 = 不截断）、
  `show_wiki_link`（附 wiki 链接：效果疑似被表格截断且本地未补录时是否附上灰机 wiki 页面链接，默认开启）、
  `forward_threshold`（转发字数阈值：回复超过多少**字**改用合并转发，默认 400，0 = 不按字数判断）、
  `forward_max_lines`（转发行数阈值：回复超过多少**行**也改用合并转发，默认 12，0 = 不按行数判断；
  两者都为 0 时始终用普通文本。行数阈值专治「字数不多但行数很多」的帮助 / 长效果内容）。
- `[data]`：`data_file`（数据文件路径，留空用内置数据）、`show_source_in_help`（帮助里显示来源）。
- `[save]`：存档解析相关——
  `enabled`（启用存档解析）、`auto_analyze_after_bind`（绑定后自动解析，默认开启）、
  `send_image`（发送解析图，默认开启）、
  `image_width`（解析图宽度，600~1000 CSS 像素，默认 **700**：调小则排版收窄、统计卡自动减列，
  长宽比变瘦高；这是控制「太宽」的主要开关）、
  `image_scale`（解析图倍率 1~3，默认 **1.4**：最终像素 = 卡片尺寸 × 该倍率，控制整体清晰度/大小）、
  `max_image_chips`（图内未发现道具数，默认 24）、
  `max_image_track_chips`（图内未解锁条目数，默认 12，0 = 不在图里列）、
  `detail_max_items`（详情未发现道具数，默认 60，其余只列编号）、
  `detail_max_named`（详情未解锁条目数，默认 40，成就带解锁条件）、
  `max_file_mb`（文件大小上限，默认 4 MB）、`cache_ttl_days`（缓存有效期，默认 30 天自动过期，0 = 永久）、
  `download_timeout_sec`（下载超时，默认 30 秒）、
  `allow_private_url`（允许内网地址，默认关闭）、
  `lookup_group_files`（按文件名查群文件：消息里没有直链时是否去群文件列表里找，默认开启）、
  `name_table_file`（名称表路径：成就 / BOSS / 挑战中文名称表，留空用内置 `assets/isaac_save_names.json`）、
  `achievement_table_file`（成就详情表路径，含解锁条件；留空用内置两张表叠加：
  `assets/isaac_achievements.json`（游戏本体生成）**+** `assets/isaac_achievements_zh.json`（第三方中文表，
  wiki 来源、CC 许可、非商业，后者优先）、
  `use_wiki_zh_names`（用中文成就表，默认开；关掉后成就名 84% 官方中文、条件是游戏内英文原文，
  且成就部分不再带非商业限制））。

> 解析图**不需要**宿主开启浏览器渲染（`[plugin_runtime.render]`）：插件自己用 Pillow 画，
> 字体也从 `assets/fonts/` 里取，因此在没有中文字体、也没有浏览器的环境（例如精简的 Linux 容器）里照样出图。
> 只有 Pillow 缺失时才会退化成纯文字，不会让用户看到报错。

## 数据重建

数据是快照，游戏更新后可以自己重新生成（需要能访问 GitHub）：

```bash
cd cateye_isaac_item
python tools/build_data.py                                  # 自动下载上游最新数据
python tools/build_data.py --raw path/to/items.db.json      # 用本地已下载的原始文件
```

脚本会重写 `assets/isaac_items.json`，并打印条目数与分类统计；同时自动合并
`assets/isaac_extra.json` 里人工补录的表格/列表。

### 中文名称表

存档里的成就 / BOSS / 挑战只有编号，中文名单独放在 `assets/isaac_save_names.json`
（可用配置 `save.name_table_file` 换成自己的文件）。重新生成分两步：

```bash
# 1) 从玩家自装游戏的语言包里提取（离线，不需要联网）
python tools/extract_game_strings.py                          # 自动探测游戏目录
python tools/extract_game_strings.py --pack <repentance_zh.a> # 或显式指定

# 2) 生成成就 / 挑战表并合并进 isaac_save_names.json
python tools/build_name_tables.py --src <第三方数据目录>
```

第 1 步不需要任何第三方来源：游戏本体把资源打包在 `resources/packed/*.a` 里，
其中 `repentance_zh.a` 是官方简体中文语言包，内含 14 个分类的官方译名
（怪物、小 BOSS、道具、角色、关卡、诅咒……），解密后直接取用。

第 2 步同样**只用游戏本体**：成就来自 `afterbirthp.a` 里的 `achievements.xml`
（641 条，含编号、游戏内弹窗文案、解锁条件注释），用
`tools/build_achievement_table.py` 生成 `assets/isaac_achievements.json`；
挑战名（45 条）与第 3 步的道具图鉴一样取自灰机 wiki，内置于 `tools/build_name_tables.py`。

> **成就与挑战没有中文的官方版本**：游戏本体只提供英文（`achievements.xml` 的弹窗文案与条件注释、
> `challenges.xml` 的英文挑战名）。因此成就中文名只在语言包里能查到相同译名时才给出
> （641 条里 539 条，84%），其余**退回游戏英文标题**；解锁条件是**游戏内的英文原文**，
> **中文解锁条件默认是有的**：插件内置了一份第三方中文成就表
> （`assets/isaac_achievements_zh.json`，数据来自 wiki：英文 wiki CC BY-SA 4.0 + 灰机 wiki CC BY-NC-SA 3.0），
> 它只覆盖中文名与中文条件，英文标题与游戏内条件仍取自游戏本体那份表。
> 这份中文表**需署名、不可商用**，与代码的 MIT 许可分开（聚合分发，互不改变）；
> 不需要它时把 `save.use_wiki_zh_names` 关掉即可，那时成就部分只剩游戏本体数据。
> **BOSS 名表已填充**：存档里 104 个 BOSS 槽位用的下标就是游戏 `entities2.xml` 的 `bossID` 属性，
> 由 `tools/build_boss_table.py` 生成（见「已知限制」8 与可行性评估附录 B）。

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
7. **存档解析只读**：插件不写、不修、不回传存档；解析结果与游戏内表现一致，但**不能用来改档**。
8. **名称表需要单独维护**：成就 / 挑战 / BOSS / 小 BOSS 的中文名称都在
   `assets/isaac_save_names.json` 里，来自上游整理或游戏本体语言包；某一类没有名表时
   插件只给编号与计数，不会编造名称。名称表结构：

   ```json
   {
     "meta": { "source": "数据来源说明", "reviewed": true },
     "achievements": { "1": "成就名" },
     "bosses": { "1": "BOSS 名", "62": { "name": "究极贪婪", "note": "游戏不写这一项" } },
     "minibosses": { "0": "懒惰" },
     "challenges": { "1": "挑战名" }
   }
   ```

   填好后重载插件即生效（不需要重新构建 `assets/isaac_items.json`）；也接受
   `[{"id": 1, "name": "…"}]` 形式的列表；某一条写成 `{"name": …, "note": …}` 时，
   `note` 会作为补充说明一起显示（用于「名字之外还得解释一句」的条目）。

   当前填充状态：

   | 分组 | 状态 | 编号范围 | 说明 |
   |---|---|---|---|
   | `achievements` | ✅ 641 条 | 1–641 | 两张表叠加：游戏本体 `achievements.xml` + 官方语言包生成基线（`isaac_achievements.json`，84% 官方中文名），再叠加第三方中文表（`isaac_achievements_zh.json`，wiki 来源 / CC 许可 / 非商业）提供中文名与**中文解锁条件**；`save.use_wiki_zh_names=false` 可关掉后者 |
   | `challenges` | ✅ 44 条 | 1–44 | 第 45 号在游戏内本就无名称，留空 |
   | `minibosses` | ✅ 7 条 | **0–6** | 该段固定 7 项 = 七宗罪，**第 0 位就是懒惰**（官方格式说明 + 实测），名称取自游戏语言包 |
   | `bosses` | ✅ 100 条具名 + 4 条说明 | **0–103** | 下标 = 游戏 `entities2.xml` 的 `bossID` 属性；名称取自游戏本体语言包。用 `python tools/build_boss_table.py` 重新生成（会自行核对本机全部历史存档，验证不过就拒绝写入） |

   **`bosses` 是怎么定下来的**：槽位下标不是实体 `type`、也不是 `(type, variant)` 升序，
   而是 `entities2.xml` 里独立的 `bossID` 属性（例：`#MONSTRO` 的 `id=20`、`bossID=1`）。
   该文件打包在 `resources/packed/afterbirthp.a` 里，本仓库用 `tools/archive_reader.py`
   解出（块格式与「原样存储」判据都是实测的，10228 + 4180 条条目全部解码成功）。
   证据链：`bossID` 范围 1–110 且 89 / 90 / 103 无实体占用，而这三个槽位在所有存档里恒为 0；
   胎衣+ 存档的 BOSS 段只有 72 / 73 槽，其位模式与忏悔+ 存档**前缀逐位相同**；
   两份公开的 100% 存档里「图鉴已击杀但位为 0」的只有 62 / 71 两项。
   详见 [存档分析-可行性评估.md](存档分析-可行性评估.md) 附录 B。
9. **怪物图鉴（bestiary）不参与统计**：插件在报告里跳过该数据块（只用它确认「11 段数据块全部对齐」）。
   该段结构其实已经解出（`[段号][段长][每组 8 字节] × 4`，`type = packed >> 20`、
   `variant = (packed >> 8) & 0xFF`），但本插件不展示其中的「遇到 / 击杀 / 命中 / 死亡」计数，
   避免又一处口径未经验证的数字；核对逻辑见 `tools/build_boss_table.py` 的 `bestiary_counts()`。
10. **全局统计只覆盖能自证的 10 项**：存档的计数器段有 523 项，其中大部分没有公开对照表，
    插件只展示在真实数据上验证过口径的那些（妈妈击杀、死亡、店主击杀、碎石、大便、染色石、
    捐款机累计、伊甸币、最佳/当前连胜）。
    验证方式（可复现）：把 `Documents\My Games\Binding of Isaac Repentance+\save_backups` 里的
    93 份每日快照全部解析一遍，按「累计型只增不减 / 状态型会升会降」分类，再与成就交叉验证。

    - 累计型：妈妈击杀、碎石、大便、死亡、店主击杀等，14 个月里全部单调不减 ✔
    - 状态型：伊甸币（会被花掉）、当前连胜（会断）——与游戏机制一致 ✔
    - **捐款机**：下标 `20` 从 2025-07 的 `151` 一路涨到 2026-08-08 的 `1133`（当前 `1092`），
      越过 900 之后成就 59「蓝蜡烛（捐献 900 枚硬币给捐款机）」确实已解锁 ✔
      ——所以展示为「**捐款机累计**」。
    - **下标 `0` 与 `19` 在全部 93 份快照里恒为 `0`**（同期捐款机累计值从 151 涨到 1133），
      第三方偏移表把它们标成 `DONATION` / `DONATION_COINS` 与实测不符，因此**不展示**
      （详见 `isaac_save.py` 里 `COUNTER_LABELS` 的注释）。

    想继续确认某个下标的含义，用 `tools/diff_saves.py` 对比两份存档即可：

    ```bash
    python tools/diff_saves.py 捐币前.dat 捐币后.dat --max-counters 20
    ```
11. **需要一个能拿到文件的通道**：插件通过 QQ 群文件 / 文件消息的下载链接取存档，
    所以 Napcat 适配器要能正常返回链接；私聊场景下如果平台不给直链、也无法按文件名查群文件，会提示用户改为在群里引用。
12. **渲染字体是子集**：内置字体覆盖 GB2312 一级常用字 + 插件数据用到的全部汉字，
    因此极生僻的人名/符号可能显示为方框（会退到系统字体，但 Pillow 不做逐字回退）。
    需要更全的覆盖时，用 `python tools/build_fonts.py --level full` 重新生成（体积约翻倍）。


## 开发

模块分工（前三个都不依赖 `maibot_sdk`，可以离线单测）：

| 模块 | 职责 |
|---|---|
| `isaac_data.py` | 图鉴的加载 / 检索 / 排版 |
| `isaac_save.py` | 存档解析（11 个数据块 / 版本识别 / 报告排版）、群文件引用解析、按 QQ 分目录的缓存 |
| `isaac_save_render.py` | 解析图：Pillow 本地绘制（宽度/倍率可调、内置字体、末行居中） |
| `plugin.py` | 配置、指令、工具、下载与发送 |

`tools/` 下另有几个离线脚本（都不依赖插件运行时）：

| 脚本 | 用途 |
|---|---|
| `tools/build_data.py` | 从上游重建 `assets/isaac_items.json` |
| `tools/extract_game_strings.py` | 从游戏语言包提取官方中文译名 |
| `tools/build_name_tables.py` | 生成成就 / 挑战 / 小 BOSS 名称表（成就来自上一条的产物） |
| `tools/build_achievement_table.py` | 从游戏本体 `achievements.xml` + 语言包生成成就表（无第三方数据） |
| `tools/archive_reader.py` | **读取游戏 `.a` 资源归档**（只读）：解出 `entities2.xml`、`achievements.xml` 等原始资源 |
| `tools/build_boss_table.py` | 由 `entities2.xml` 的 `bossID` 生成 BOSS 段 104 槽位的名表（自带验证，不过不写） |
| `tools/check_licenses.py` | **许可合规审计**：扫描 GPL 等传染性许可标记、核对整包 CC BY-NC-SA 4.0 与代码 MIT 声明的一致性、登记每个数据文件来源 |
| `tools/check_config_i18n.py` | **配置汉化自检**：静态扫描（+ 有 `maibot_sdk` 时用真实 Schema 复核）每个配置节与字段是否都有中文 `label` / `hint` |
| `tools/diff_saves.py` | **对比两份存档**（只读）：确认计数器口径、反推 BOSS 编号 |
| `tools/build_fonts.py` | 生成内置渲染字体（Noto Sans SC 子集，SIL OFL 1.1） |

```bash
cd cateye_isaac_item

# 图鉴模块自检
python -c "from isaac_data import ItemDatabase; db=ItemDatabase.load('assets/isaac_items.json'); print(db.stats())"

# 存档解析自检（把路径换成你自己的存档）
python -c "from isaac_save import *; raw=open('rep+persistentgamedata1.dat','rb').read(); print(format_report_text(build_report(parse_save_bytes(raw, file_name='rep+persistentgamedata1.dat'))))"
```

改 `plugin.py` 后用 WebUI 重载插件即可；改 `_manifest.json`（能力声明、版本号）需要完整重启 MaiBot。

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
- **存档格式**：二进制布局（魔数、数据块顺序、条目宽度、下标基准、计数器下标含义）参考了社区公开的
  格式记录与实现——**REPENTOGON（GPL-2.0）的 `SaveSyncing/SaveFile.h`（对存档格式的逐段记录）**、
  [isaac.ardor.guru 的格式说明](https://isaac.ardor.guru/guides/save-file-format/)、
  [Zamiell/isaac-save-viewer](https://github.com/Zamiell/isaac-save-viewer)（GPL-3.0）、
  [Demorck/Isaac-save-manager](https://github.com/Demorck/Isaac-save-manager)——
  但**没有复制它们的任何代码或数据文件**（只取「字节布局」这类事实信息）：本插件的解析器是按
  字节布局自行实现的，且只做只读解析。格式规格属于事实信息，实现与数据表均自行整理。
  上表所有来源的许可与本插件的可用方式，逐条列在
  [存档分析-可行性评估.md](存档分析-可行性评估.md) 第九节。
- **怪物 / 小 BOSS / BOSS / 道具 / 角色 / 关卡 / 诅咒的中文名** —— 直接取自**玩家自装游戏本体**的
  官方简体中文语言包 `resources/packed/repentance_zh.a`，不经过任何第三方整理，
  由 [tools/extract_game_strings.py](tools/extract_game_strings.py) 解密提取
  （归档格式同样参考上述社区逆向资料自行实现）。
- **BOSS 段 104 个槽位的对应关系** —— 来自**游戏本体**的 `resources/packed/afterbirthp.a` 里的
  `entities2.xml`（实体 `bossID` 属性），由 [tools/archive_reader.py](tools/archive_reader.py)
  自行实现的归档解码器取出；`.a` 的结构体定义参考了社区公开的逆向记录
  （zenhax 论坛 Ekey 的 `AHeader` / `AEntry`、Gibbed.Rebirth 的 `ArchiveFile.cs`（**Zlib 许可**）、
  QuickBMS 的 `arch000.bms`），**块语义与「原样存储」判据为本仓库实测结论**，未复制其代码。
  证据链见 [存档分析-可行性评估.md](存档分析-可行性评估.md) 附录 B。
- **渲染字体** —— [Noto Sans SC](https://fonts.google.com/noto/specimen/Noto+Sans+SC)
  （Google / Noto 项目，**SIL Open Font License 1.1**）：插件内置的是按实际字符集裁剪的子集
  （GB2312 一级常用字 + 插件数据用到的全部汉字 + ASCII 与常用标点），
  由 [tools/build_fonts.py](tools/build_fonts.py) 生成，许可全文见 `assets/fonts/OFL.txt`。
- **成就名与解锁条件（1–641）的两份来源** ——
  ① **游戏本体**：`afterbirthp.a` 里的 `achievements.xml`（编号、弹窗文案、英文条件注释）由
  [tools/build_achievement_table.py](tools/build_achievement_table.py) 生成，中文名取自官方简体语言包
  （能查到同名译名才用，539/641）；② **第三方中文表**：`assets/isaac_achievements_zh.json`
  （aprisyourlie/IsaacAchievementGuide 整理，其数据源自
  [英文 wiki](https://bindingofisaacrebirth.wiki.gg/wiki/Achievement)（CC BY-SA 4.0）与
  [以撒中文维基](https://isaac.huijiwiki.com/wiki/成就)（CC BY-NC-SA 3.0）），
  只提供中文名与中文条件，默认叠加在①之上、可用 `save.use_wiki_zh_names` 关闭。
- **挑战中文名（1–45）** —— 以撒的结合中文 Wiki（灰机 wiki）的「挑战」页面。

### 许可说明

- **整包以 [CC BY-NC-SA 4.0](LICENSE) 发布**（仓库 `LICENSE` 即该协议全文）；
  **代码与非 CC 数据以 MIT 开源**，两部分各自的受控范围见 README 开头的「许可」一节。产物中**不含任何 GPL 内容**
  （`tools/check_licenses.py` 会扫描传染性许可标记，自检里也有防回归断言）。
  **wiki 数据文件按各自来源的许可使用**：
  - 游戏本体数据（语言包、`achievements.xml`、`entities2.xml` 里提取的译名与对照表）：玩家自装游戏数据，随 MIT 部分提供。
  - **wiki 数据（需署名、不可商用）**：道具图鉴 `isaac_items.json`、13 条表格补录 `isaac_extra.json`、
    挑战名 `isaac_challenges_zh.json`（灰机 wiki，CC BY-NC-SA 系）与
    **成就中文表 `isaac_achievements_zh.json`**（英文 wiki CC BY-SA 4.0 + 灰机 wiki CC BY-NC-SA 3.0）。
    正因这四个文件存在，**整包按 CC BY-NC-SA 4.0 发布、只能非商业使用**；
    想要一个不含非商业数据的成就体验，把 `save.use_wiki_zh_names` 关掉即可
    （道具图鉴 / 挑战名仍来自 wiki）。
  - 字体：Noto Sans SC 子集（SIL OFL 1.1），许可全文见 `assets/fonts/OFL.txt`。
- **为什么不是把插件整体改成 GPL-3.0**：上游那份中文表的**数据**其实来自 wiki（CC BY-SA 4.0 / CC BY-NC-SA），
  其仓库的 GPL-3.0 只覆盖自己的代码；而 CC BY-NC-SA 的 **NonCommercial** 与 GPL-3.0「不得附加进一步限制」
  冲突，所以改 GPL-3.0 **并不能**让这份中文数据变得合规，只会让代码也被传染。
  现在的仓库结构正是这样落地的：**wiki 数据按 CC 引入 → 整包 CC BY-NC-SA 4.0；代码与非 CC 数据单独以 MIT 开放**。
- 数据来源分三类，逐文件清单见 `tools/check_licenses.py` 的 `SOURCE_LICENSE_MAP`：
  - **玩家自装游戏数据**（`isaac_*_zh.json`、`isaac_achievements.json`、`isaac_boss_slots.json`）：
    官方简体语言包、`achievements.xml`、`entities2.xml` —— 提取的是玩家自己已购买游戏内的数据。
  - **灰机 wiki 内容**（`isaac_items.json`、`isaac_extra.json`、`isaac_challenges_zh.json`）：
    社区 wiki 通常采用 **CC BY-NC-SA** 系许可，上游仓库未声明 LICENSE。这类内容是**数据**，
    与代码的 MIT 许可分开：**非商业使用并署名**，商业使用需自行确认授权。
  - **字体**：Noto Sans SC 子集（**SIL OFL 1.1**），许可全文见 `assets/fonts/OFL.txt`。
- 因此本插件**仅随 MaiBot 插件市场发布、用于非商业用途**。如需商业使用或再分发
  内置的 wiki 数据，请先自行向灰机 wiki 确认授权（游戏本体数据与字体不受此限）。
