# 以撒道具图鉴 / 存档解析

《以撒的结合》图鉴查询与存档解析插件（MaiBot Manifest v2，id `github.cateye.isaac.item`）。

在群里发 `/以撒 硫磺火` 查道具，麦麦也能在对话里用 `isaac_item_lookup` 自己查。图鉴数据是打包在插件里的离线快照：1039 条（道具 715 / 饰品 183 / 卡牌 94 / 药丸 47），含 3599 条协同，运行时不请求图鉴接口。

存档部分让群友把自己的 `persistentgamedata*.dat` 传到群文件或聊天里，引用后发 `/以撒存档绑定`。插件下载校验后按 QQ 号只读缓存在 bot 本地，之后 `/以撒存档` 返回一张进度解析图，再跟一条合并转发的详细统计（成就、图鉴、挑战、BOSS 与全局计数）。解析全程只读，不改存档。

作为发行包按 [CC BY-NC-SA 4.0](LICENSE) 发布，仅限非商业使用；代码与非 CC 数据另按 MIT 提供（可单独商用），详见「[许可与来源](#许可与来源)」。

## 安装与启用

### 宿主与依赖

- manifest 为 v2，`host_application` 兼容 `1.0.0 ~ 1.99.99`，SDK 兼容 `2.5.0 ~ 2.99.99`（`_manifest.json`）。
- 把整个 `cateye_isaac_item` 目录放进 MaiBot 的 `plugins/` 下，重启 MaiBot 或在 WebUI 插件管理里启用。入口是 `plugin.py` 的 `create_plugin()`。
- `dependencies` 为空，没有必须安装的第三方 Python 包。
- 解析图用 Pillow 在本机绘制；没装 Pillow、或绘制失败时自动改发文字摘要，图鉴查询与存档解析本身不受影响。
- 能力声明为 `send.text`、`send.image`、`send.forward`、`api.call`。解析图不调用宿主的 `render.html2png`。
- 存档功能需要宿主提供 `ctx.paths.data_dir`；拿不到时存档指令会提示缓存目录不可用。
- 消息里没有直链时，按文件名查群文件走 Napcat 适配器的 `adapter.napcat.file.get_group_root_files` 与 `adapter.napcat.file.get_group_file_url`；可用 `save.lookup_group_files = false` 关掉这条兜底。

### 配置文件

Runner 会在插件目录生成 `config.toml`（`.gitignore` 已忽略，不随仓库提交），WebUI 的插件配置页对应同一组字段。五个配置节和全部字段见「[配置](#配置)」。

### 随包数据

| 路径 | 作用 |
|---|---|
| `assets/isaac_items.json` | 运行时图鉴快照：1039 条（道具 715 / 饰品 183 / 卡牌 94 / 药丸 47），其中 713 条带效果正文；另有 3599 条协同，协同条目均带效果说明。 |
| `assets/isaac_extra.json` | 13 条 wiki 表格类效果的补录；`tools/build_data.py` 构建时并入图鉴。 |
| `assets/isaac_save_names.json` | 存档报告的名称表：成就 641、BOSS 104 个槽位、小 BOSS 7、挑战 44。 |
| `assets/isaac_achievements.json` | 成就详情：641 条；539 条能对上官方简体译名；解锁条件为游戏内英文原文（有则填）。 |
| `assets/isaac_achievements_zh.json` | 第三方中文成就表，提供中文名与中文条件；默认叠加，可用 `save.use_wiki_zh_names = false` 关闭。 |
| `assets/isaac_challenges_zh.json` | 挑战中文名（第 45 号在游戏内本就没有名称，留空）；由 `tools/build_name_tables.py` 生成，实际名称同时写入 `isaac_save_names.json`。 |
| `assets/isaac_boss_slots.json` | BOSS 段 104 个槽位与 `entities2.xml` 的 `bossID` 对照，供核验与重建。 |
| `assets/isaac_entities_zh.json`、`isaac_minibosses_zh.json`、`isaac_items_zh_names.json`、`isaac_players_zh.json`、`isaac_stages_zh.json`、`isaac_curses_zh.json` | 从游戏官方简体语言包提取的译名表；重建 BOSS / 名称表时的输入与备查数据。 |
| `assets/fonts/NotoSansSC-Regular.ttf`、`NotoSansSC-Bold.ttf` | 解析图内置字体（Noto Sans SC 子集）；许可见 `assets/fonts/OFL.txt`。 |

`assets/isaac_save_names.json` 的四个分组都是「编号 → 名称」映射；名称可以写成字符串，也可以写成 `{ "name": "…", "note": "…" }`，`note` 会作为补充说明一起显示。改完重载插件即可生效，不需要重建 `isaac_items.json`；也可以用 `save.name_table_file` 指向自己的表。

## 指令

两个斜杠指令都没有声明权限参数，插件内部也不做权限判断；`save.enabled = false` 时，存档系列指令只回复「已被管理员关闭」。

插件只注册了两个 `@Command` 和一个 `@Tool`，没有 Hook 或消息监听；聊天里的可见行为都来自本章的指令和下一章的 LLM 工具。

通用触发规则：

- 前缀 `/` 可以省略；英文写法用小写 `isaac`。
- 图鉴指令必须位于整条消息的开头，前面只允许空白和可选的 `/`；`以撒` 前面还有别的字（例如「哈哈 以撒」）不会触发。
- 不带 `/` 且没有命中任何图鉴条目时，插件不回复，这条消息继续走普通聊天；图鉴查询带 `/` 时总会回复，包括「没找到」。
- 存档指令要单独发送，指令后不要再跟其它文字；`/以撒存档` 系列与 `/以撒 <关键词>` 不会互相抢占。

### 图鉴查询

| 指令 | 说明 |
|---|---|
| `/以撒 <关键词>` | 查询条目。也接受 `以撒 <关键词>`、`/isaac <关键词>`、`/以撒道具 <关键词>`、`/以撒图鉴 <关键词>`。关键词可以是中文名、英文名、图鉴编号、标签或效果词。唯一命中，或名称 / 编号精确命中时直接给详情，否则给候选列表。 |
| `/以撒详情 <关键词>` | 只看完整效果、套装与全部协同；也可写成 `/以撒详情<关键词>`。有多个匹配时给首条，并附「其它匹配」名单。 |
| `/以撒协同 <关键词>` | 只输出该条目的全部协同，不受 `display.max_synergies` 限制。 |
| `/以撒编号 <编号>` | 按图鉴编号查询；只取输入里的数字字符，例如 `/以撒编号 118`。 |
| `/以撒随机 [分类]` | 随机抽一条。分类可取 `道具` / `饰品` / `卡牌` / `药丸`，也接受 `物品`、`卡片`、`胶囊` 与 `item`、`trinket`、`card`、`pill` 等别名；不填则在全部条目里抽。分类无效时会列出可用分类。 |
| `/以撒`、`/以撒帮助` | 输出帮助；默认附数据来源与规模（`data.show_source_in_help`），存档功能开启时还会附存档指令清单与缓存状态。查询为 `帮助`、`help`、`菜单`、`说明`、`指令`、`?`、`？` 时同样进入帮助。 |

示例：

```text
/以撒 硫磺火
/以撒 The Sad Onion
/以撒 118
/以撒 射速
/以撒详情 297
/以撒协同 硫磺火
/以撒随机 饰品
```

检索顺序为：编号 → 中文名精确 → 英文名精确 → 中文名前缀 / 包含 → 英文名前缀 / 包含 → 标签 → 效果正文 → 协同正文。标签、效果、协同三级只在前面的结果不足 `search.max_results` 时补充；以上全部没有命中时，才用 `search.fuzzy_threshold` 做近似匹配（名称记不全或打错字时有用）。三段回退可分别用 `search.search_by_tag`、`search.search_by_effect`、`search.search_by_synergy` 关闭。

协同正文也能搜：例如 `/以撒 不再叠加` 命中变异蜘蛛，`/以撒 被吞噬后` 命中生死逆转、神秘礼物、熔炉等。想查某个道具和谁有协同，用 `/以撒协同 <道具名>`。

### 存档指令

| 指令 | 说明 |
|---|---|
| `/以撒存档` | 查看已绑定存档的解析图与详细进度。也接受 `/以撒进度`、`/isaac存档`、`/isaac进度`（英文与「存档 / 进度」之间不能有空格）。还没绑定时发「去哪找存档」的指引。 |
| `/以撒存档绑定` | 从消息里的文件段或文本链接提取 `.dat`，下载校验后缓存；也接受 `上传`、`更新`。引用群文件后发送最方便。 |
| `/以撒存档<1-3>` | 查看指定存档位，例如 `/以撒存档2`。可选 1~3；绑定 4~9 号会报错，10 号及以上的文件名不会被识别为独立档位（见「已知限制」12）。指定存档位未绑定时，会列出已缓存的存档或给出上传指引。 |
| `/以撒存档详情` | 只发文字摘要与详情，不绘制、不发送解析图；也接受 `文本`。 |
| `/以撒存档列表` | 列出当前用户已缓存的存档位、文件名、大小、识别出的版本、绑定时间与来源；也接受 `状态`。 |
| `/以撒存档清除` | 删除当前用户在本地的全部缓存存档，并回报删除份数；也接受 `删除`、`解绑`。不影响用户自己电脑上的存档文件。 |
| `/以撒存档帮助` | 发存档位置与上传方式指引（含忏悔+、忏悔、胎衣+、Steam 云存档与 Proton 路径）；也接受 `说明`。 |

引用群文件后发送 `/以撒存档绑定`，其余常用写法：

```text
/以撒存档
/以撒存档2
/以撒存档详情
/以撒存档列表
/以撒存档清除
```

### 长回复与协同

- 正文超过 `display.forward_threshold`（默认 400 字）或超过 `display.forward_max_lines`（默认 12 行）时，改用合并转发发送；两个阈值都设 0 时正文始终用普通文本（协同溢出仍按上一条处理）。
- 协同条数超过 `display.max_synergies`（默认 5）时，正文只留一行提示，全部协同改用合并转发；正文本身也需要转发时，正文与协同合成同一条聊天记录的两个节点。`max_synergies = 0` 表示全部内联。
- 合并转发不可用时，逐段回退普通文本；超长内容按行累积到约 1200 字切块，不丢内容。
- LLM 工具发不了合并转发，协同超过 `display.max_synergies_in_tool`（默认 20）时截断并注明「共 N 条，此处列出前 M 条」。

## LLM 工具

| 工具 | 参数 | 说明 |
|---|---|---|
| `isaac_item_lookup` | `query`（string，必填）、`detail`（boolean，可选，默认 `false`） | 查询图鉴并把排版好的文本交给模型；不主动发送消息。 |

- `query` 可以是中文名、英文名、图鉴编号或效果关键词。
- `detail = false`：多个匹配时返回候选列表；补录的表格与补充说明只预览前 3 行。
- `detail = true`：返回完整效果、套装与协同；协同受 `max_synergies_in_tool` 限制，超出时截断并注明条数。
- 返回对象包含 `content`（已排版文本）、`query`、`detail`、`matched`（是否命中）、`loaded`（图鉴是否加载成功）、`total`（图鉴总条数）。

## 存档解析

### 找到存档文件

| 版本 | 默认位置 | 文件名示例 |
|---|---|---|
| 忏悔+ | `文档\My Games\Binding of Isaac Repentance+\` | `rep+persistentgamedata1.dat` |
| 忏悔 | `文档\My Games\Binding of Isaac Repentance\` | `rep_persistentgamedata1.dat` |
| 胎衣+ | `文档\My Games\Binding of Isaac Afterbirth+\` | `abp_persistentgamedata1.dat` |
| Steam 云存档 | `Steam\userdata\<SteamID>\250900\remote\` | 同上 |
| Linux / Steam Deck（Proton） | `~/.steam/steam/steamapps/compatdata/250900/pfx/drive_c/users/steamuser/Documents/My Games/…` | 同上 |

文件名里的 `1` / `2` / `3` 是存档位。要的是 `persistentgamedata`，不是 `gamestate`——后者是「中途退出的单局」，插件会明确拒绝。

### 绑定与查看

1. 把 `.dat` 传到群文件，或直接把文件发到聊天里。
2. 引用（回复）那条文件消息，发送 `/以撒存档绑定`；如果适配器支持，也可以在发送文件时把指令文字放在同一条消息里。
3. 之后随时用 `/以撒存档` 查看；同一存档位重复上传会覆盖旧缓存。

`save.auto_analyze_after_bind` 默认开启：绑定成功后立即解析并回图。关掉后只回复绑定结果，提示用户再发 `/以撒存档`。

### 下载与校验

- 优先使用消息文件段里的下载链接；没有直链时从文本里找 `.dat` 或 QQ 文件链接；仍没有、且 `save.lookup_group_files` 开启时，按文件名到群文件列表里找（最多读取 100 个根文件，先精确匹配再包含匹配）。
- 只允许 `http` / `https`，主机名必须存在；默认校验还会拒绝地址里携带凭据。
- 默认拒绝内网与保留地址：`10/8`、`172.16/12`、`192.168/16`、`127/8`、`169.254/16`、`100.64/10`（CGNAT）、IPv6 回环 / 链路本地 / ULA、v4-mapped、NAT64 本地化段（`64:ff9b:1::/48`）等；无法识别的地址格式按拒绝处理。`64:ff9b::/96` 这类 well-known NAT64 前缀目前不在拦截列表内。
- 重定向的每一跳都会重新校验，避免用 302 绕回内网。
- 流式读取，超过 `save.max_file_mb`（默认 4 MB）立即中止；下载超时由 `save.download_timeout_sec` 控制（最小 5 秒）。
- `save.allow_private_url = true` 会跳过内网 / 回环拦截与凭据检查，只保留协议与主机名检查。默认关闭；开启后引用消息可以被伪造指向内网服务。
- 校验与 urllib 实际建连各解析一次域名，两次之间 DNS 记录可变，理论上存在 DNS rebinding 的残留窗口；插件按「默认拒绝内网 + 只下载用户自己提供的群文件直链」的场景接受这一低风险口子。

坏文件会得到明确原因，而不是静默失败：空文件、`gamestate`、非以撒存档（文件头不匹配）、被截断、出现未知数据块、文件过大、4~9 号存档位，都会直接说明。

### 缓存

- 目录为 `<data_dir>/saves/<安全化后的 QQ 号>/slotN.dat`，旁边有 `slotN.meta.json` 记录文件名、大小、版本、绑定时间、来源与 sha256。
- 写入时先写临时文件再原子替换；同一存档位覆盖。解析只读字节流，不会改写原文件；缓存只在绑定时写入。
- `save.cache_ttl_days` 默认 30 天。过期清理按文件 mtime 计算，只在插件加载或本插件配置热更新时执行；设为 `0` 表示永久保留，直到用户用 `/以撒存档清除` 删除。
- 用户 ID 会被清洗成安全的目录名，避免路径穿越。

### 报告内容

- 识别版本：魔数 `ISAACNGSAVE` 后的三位版本号区分重生（`06R`）与胎衣（`08R`）；`09R` 再按成就段条目数区分胎衣+（349 / 404）、忏悔（638）、忏悔+（642）。`09R` 存档的成就段条目数不在已知范围时，标为未知版本并给出提示。
- 解析 11 个数据块，报告使用其中 6 项进度：成就、道具、BOSS 记录、小 BOSS 记录（七宗罪 7 项）、挑战、特殊种子。成就 / 道具 / 挑战 / 特殊种子的第 0 位未使用（总数 = `count − 1`）；BOSS 与小 BOSS 的第 0 位是真实条目（总数 = `count`）。
- 10 项全局统计：妈妈击杀、死亡次数、店主击杀、碎石数、大便破坏、染色石破坏、捐款机累计、伊甸币、最佳连胜、当前连胜。计数器段其余下标没有可靠对照表，不展示。
- 未发现的道具会复用内置图鉴的 715 条中文名；图鉴没有收录的编号会标注「图鉴未收录该编号，可能是空槽位」。
- 未完成的挑战、未解锁的成就（有条件的会带解锁条件）、存档没有记录的 BOSS / 小 BOSS 都会带名称；名称表缺失时只给编号与计数，不编造名称。
- BOSS 段的 104 个槽位里，100 个已分配并具名；3 个空槽位（89 / 90 / 103）与 1 个旧版保留位（0）会附说明。游戏不写入、无法正常击败或从未出现的槽位也会逐条说明，不会误报成「你没打过」。
- 怪物图鉴（bestiary）段只用来确认整段对齐，不参与统计。
- 空存档位会提示「这一位没有任何进度，像是空存档」。

### 解析图

- 由插件用 Pillow 本地绘制，不依赖宿主的浏览器渲染，也不声明 `render.html2png` 能力。
- 字体随插件打包（Noto Sans SC 子集），因此在没有中文字体的 Linux 上也能出图；内置字体缺失时才退回系统字体候选。
- `save.image_width` 控制排版宽度（600~1000 CSS 像素，默认 700）：宽度 ≥ 860 时统计卡 4 列，≥ 700 时 3 列，更窄时 2 列；末行不满会居中。
- `save.image_scale` 控制输出倍率（默认 1.4；配置描述写 1~3，渲染层实际夹到 0.8~4.0）。
- `save.max_image_chips`（默认 24）限制图内未发现道具数；`save.max_image_track_chips`（默认 12）限制每组名称清单条数，0 表示不在图里列。
- `save.send_image = false`、Pillow 不可用或绘制失败时，改发文字摘要；合并转发的详细报告仍会发送。解析图单独一条消息。

## 配置

### 生效方式

- `data.data_file`、`save.name_table_file`、`save.achievement_table_file`、`save.use_wiki_zh_names`、`save.max_file_mb`、`save.cache_ttl_days` 在插件加载或本插件配置热更新时装配（图鉴、名称表、缓存目录、清理）。
- 其余字段在每次调用时读取，改完保存即可生效。
- `[plugin] enabled` 只是配置模型里的总开关字段，插件代码没有读取它；实际控制存档指令的是 `save.enabled`。禁用整个插件请用宿主的插件管理。
- `[plugin] config_version` 是隐藏、禁用的配置版本字段，插件不用它做迁移或校验，保持默认即可。

### 字段

表里用 `节.字段` 表示，例如 `save.enabled` 对应 `[save]` 节的 `enabled`；WebUI 按字段的 `label` / `hint` 显示中文。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `plugin.enabled` | `true` | 配置模型里的总开关字段；插件代码不读取，见上方说明。 |
| `plugin.config_version` | `"0.2.10"` | 配置版本，WebUI 中隐藏且禁用。 |
| `search.max_results` | `5` | 列表模式最多返回条数，读取时夹到 1~20。 |
| `search.fuzzy_threshold` | `0.55` | 模糊匹配阈值 0~1；只在其它检索全无命中时使用，0 关闭。 |
| `search.search_by_tag` | `true` | 名称不足时按标签补检索。 |
| `search.search_by_effect` | `true` | 仍不足且关键词至少 2 字时在效果正文里找。 |
| `search.search_by_synergy` | `true` | 仍不足且关键词至少 2 字时在协同名称 / 效果里找。 |
| `search.max_query_length` | `32` | 图鉴查询关键词的最大长度，超出截断；存档指令不受影响。 |
| `display.show_english` | `true` | 详情标题与候选列表是否附英文名。 |
| `display.show_quality` | `true` | 是否显示品质 0~4；品质为 -1 的条目本就省略。 |
| `display.show_tags` | `true` | 详情与候选列表是否显示标签。 |
| `display.show_synergies` | `true` | 详情里是否展示协同条目。 |
| `display.max_synergies` | `5` | 聊天详情内联协同上限；超过则正文留提示、全部协同走合并转发，0 表示全部内联。 |
| `display.max_synergies_in_tool` | `20` | LLM 工具的协同上限；超过截断并注明条数，0 表示全部列出。 |
| `display.max_effect_chars` | `0` | 效果正文截断上限，0 表示不截断。 |
| `display.show_wiki_link` | `true` | 效果疑似被表格截断且本地没有补录时，附灰机 wiki 页面链接。 |
| `display.forward_threshold` | `400` | 回复字数超过该值时改用合并转发，0 关闭字数判断。 |
| `display.forward_max_lines` | `12` | 回复行数超过该值时改用合并转发，0 关闭行数判断；两者都为 0 时正文始终用普通文本。 |
| `data.data_file` | `""` | 图鉴数据文件路径；留空用 `assets/isaac_items.json`。 |
| `data.show_source_in_help` | `true` | 帮助文本是否附数据来源与规模。 |
| `save.enabled` | `true` | 是否启用存档解析；关闭后存档指令只回复「已被管理员关闭」，图鉴查询不受影响。 |
| `save.auto_analyze_after_bind` | `true` | 绑定成功后是否立即解析并回图；关闭则只回绑定结果。 |
| `save.send_image` | `true` | 是否绘制并发送解析图；关闭或渲染不可用时改发文字摘要。 |
| `save.image_width` | `700` | 解析图排版宽度，600~1000 CSS 像素。 |
| `save.image_scale` | `1.4` | 输出倍率；配置描述写 1~3，渲染层实际夹到 0.8~4.0。 |
| `save.max_image_chips` | `24` | 解析图里最多列出的未发现道具数，最小 1。 |
| `save.max_image_track_chips` | `12` | 解析图里每组名称清单最多列出的条数，0 表示不列。 |
| `save.detail_max_items` | `60` | 合并转发详情里逐个列出的未发现道具数，最小 1；其余只列编号。 |
| `save.detail_max_named` | `40` | 详情里带名称 / 条件的未解锁条目数，最小 1；构建报告时内部上限放大为 `max(n × 4, 80)`。 |
| `save.max_file_mb` | `4.0` | 允许解析的存档大小上限（MB），最小 0.1；同时作为下载流式上限。 |
| `save.cache_ttl_days` | `30` | 缓存有效期（天），≤ 0 表示永久保留；过期清理只在加载 / 热更新时执行。 |
| `save.download_timeout_sec` | `30` | 下载存档的超时秒数，最小 5。 |
| `save.allow_private_url` | `false` | 是否允许从内网 / 回环地址下载；默认关闭，开启后有 SSRF 风险。 |
| `save.lookup_group_files` | `true` | 消息里没有直链时，是否按文件名去群文件列表取下载链接。 |
| `save.name_table_file` | `""` | 成就 / BOSS / 小 BOSS / 挑战中文名称表路径；留空用 `assets/isaac_save_names.json`。 |
| `save.achievement_table_file` | `""` | 成就详情表路径；留空叠加 `isaac_achievements.json` + `isaac_achievements_zh.json`，显式指定时只用该文件。 |
| `save.use_wiki_zh_names` | `true` | 是否叠加第三方中文成就表；关闭后中文名与中文条件退回游戏本体表。 |

### 路径规则

`data.data_file`、`save.name_table_file`、`save.achievement_table_file` 三个路径配置项共用同一套规则：

- 留空使用插件内置文件。
- 相对路径按插件目录解析，不跟随宿主进程的工作目录。
- 解析结果必须落在插件目录或插件数据目录之内；`..` 穿越和指向机器上任意位置的绝对路径一律拒绝。
- 必须是存在的常规文件；目录、设备、管道、不存在的路径一律拒绝。
- 被拒绝时回退内置默认文件，并在日志里写明原因。`save.achievement_table_file` 被拒绝时回退默认的两张表。

## 数据重建

数据是快照，游戏更新或需要换数据源时按下面的入口重新生成。以下命令都在插件目录下执行，读取玩家自装的游戏文件或本地原始数据；只有 `build_data.py` 在不给 `--raw` 时会从 GitHub 下载上游数据。

| 命令 | 前置 | 产物 |
|---|---|---|
| `python tools/build_data.py` | 能访问 GitHub；或加 `--raw <items.db.json>` 使用本地原始文件 | 重写 `assets/isaac_items.json`，自动合并 `assets/isaac_extra.json`，并打印条目数与分类统计。 |
| `python tools/extract_game_strings.py --pack <repentance_zh.a>` | 游戏本体的官方简体语言包（不传 `--pack` 时自动探测游戏目录） | 在 `assets/` 下生成实体、小 BOSS、道具、角色、楼层、诅咒等中文译名表。 |
| `python tools/build_achievement_table.py --game <游戏根目录>` | 游戏本体 `resources/packed/afterbirthp.a` 里的 `achievements.xml` | 生成 `assets/isaac_achievements.json`（641 条成就、条件与可对上的中文名）。 |
| `python tools/build_name_tables.py` | 已生成 `assets/isaac_achievements.json`；小 BOSS 表可选 | 生成 `assets/isaac_challenges_zh.json`，并把成就、挑战、小 BOSS 合并进 `assets/isaac_save_names.json`（保留已有的 `bosses` 段）。 |
| `python tools/build_boss_table.py --game <游戏根目录> --save <存档>.dat` | 游戏本体 `entities2.xml`，以及至少一份用于核对的存档；多份存档用 `--verify-dir` | 生成 `assets/isaac_boss_slots.json`，并写入名称表的 `bosses` 段；结构或位图核对不通过时拒绝写入。 |
| `python tools/build_fonts.py --source <NotoSansSC-VF.ttf> --level common` | Noto Sans SC 可变字体；`--license` 可同时复制 OFL 文本 | 重写 `assets/fonts/NotoSansSC-Regular.ttf` 与 `NotoSansSC-Bold.ttf`。 |

名称表重建的推荐顺序：

```bash
python tools/extract_game_strings.py --pack "<游戏目录>/resources/packed/repentance_zh.a"
python tools/build_achievement_table.py --game "<游戏根目录>"
python tools/build_name_tables.py
python tools/build_boss_table.py --game "<游戏根目录>" --save "<存档>.dat"
```

排查与审计脚本：`tools/diff_saves.py` 对比两份存档的进度与计数器（只读）；`tools/check_licenses.py` 检查许可标记与数据来源登记；`tools/check_config_i18n.py` 检查配置字段的中英文案。

## 已知限制

1. **数据是快照**：新版本游戏新增的道具 / 成就 / BOSS 需要按上一节重新生成数据，插件不会在运行时联网更新。
2. **饰品 / 卡牌 / 药丸只有名称**：上游只对「道具」抓到了完整效果文本，这三类只保留名称与编号；查询时会明确说明「数据源只收录了名称」。
3. **部分 `en` 字段不是英文名**：上游有些条目填的是英文风味文本，插件按原样输出；不想看可以关掉 `display.show_english`。
4. **不展示主动 / 被动**：数据里没有可靠的 `type` 信息，插件只展示条目类别（道具 / 饰品 / 卡牌 / 药丸）。
5. **品质为 -1 表示未知**：饰品 / 卡牌 / 药丸没有品质数据，展示时自动省略。
6. **wiki 表格类效果靠补录**：上游爬虫不解析 `<table>`，效果会在「……触发以下效果：」处断句。这类条目共 13 条，已按灰机 wiki 原文补录并随 `assets/isaac_extra.json` 分发；将来出现未补录的截断条目时，插件会附上灰机 wiki 页面链接作为兜底（`display.show_wiki_link` 可关）。
7. **存档解析只读**：插件不写、不修、不回传存档，也不能用来改档；写入只发生在插件自己的缓存目录。
8. **报告只覆盖能自证的部分**：怪物图鉴（bestiary）段只用于确认整段对齐，不参与统计；计数器段只展示已确认口径的 10 项，其余下标不猜。
9. **下载校验的残留口子**：SSRF 校验解析一次域名，随后 urllib 建立连接时会再次解析；两次之间 DNS 记录可变，理论上可用 rebinding 把连接指回内网，而 urllib 无法把连接钉在已校验的 IP 上。另外，NAT64 拦截名单只覆盖本地化段 `64:ff9b:1::/48`，well-known `64:ff9b::/96` 不在其中；NAT64 环境需自行评估。插件按「默认拒绝内网 + 只下载用户自己提供的群文件直链」的场景接受这些低风险口子。
10. **解析图依赖 Pillow 与内置字体**：没有 Pillow 时改发文字摘要；内置字体是按插件数据与 GB2312 常用字裁剪的子集，极生僻的人名或符号可能显示为方框，需要更全的覆盖时用 `tools/build_fonts.py --source <NotoSansSC-VF.ttf> --level full` 重新生成。
11. **需要一个能拿到文件的通道**：插件通过 QQ 群文件 / 文件消息的下载链接取存档。私聊或适配器不返回直链、又无法按文件名查群文件时，会提示引用群文件或直接把文件发到聊天里。
12. **文件名只识别一位数的 1~9 号槽位**：1~3 是可用档位；4~9 号会被明确拒绝，不会自动归位。文件名里没有可识别槽位时（例如 `persistentgamedata10.dat`）会按 1 号处理，可能覆盖 1 号缓存；请只上传 1~3 号文件。
13. **缓存过期清理有触发时机**：`cache_ttl_days` 的清理只在插件加载或本插件配置热更新时执行，不是定时任务；平时可以用 `/以撒存档清除` 手动删除。

## 许可与来源

### 许可结论

- **作为发行包以 [CC BY-NC-SA 4.0](LICENSE) 发布**：使用、修改、再分发需要署名，不得用于商业目的，改编产物需以相同许可共享。`LICENSE` 即该协议全文，manifest 的 `license` 字段也是这一项。
- **代码与非 CC 数据以 MIT 开源**：全部 `.py`（含 `tools/`）、游戏本体提取的译名与对照表、`isaac_achievements.json`、`isaac_boss_slots.json` 等。MIT 许可全文如下：

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

> 一句话版：发行包是「非商业 + 署名 + 相同方式共享」的 CC BY-NC-SA 4.0；只想复用代码的人，取 MIT 覆盖的 `.py` 与游戏本体数据即可（含商用）。逐文件来源与许可登记见 `tools/check_licenses.py` 的 `SOURCE_LICENSE_MAP`。

### 数据来源

| 内容 | 来源 | 许可 |
|---|---|---|
| `assets/isaac_items.json`、`assets/isaac_extra.json`、`assets/isaac_challenges_zh.json` | 以撒的结合中文 Wiki（灰机 wiki）；道具图鉴经上游项目整理 | 灰机 wiki 内容通常为 CC BY-NC-SA 系；需署名、非商业，商用前需自行确认 |
| `assets/isaac_achievements_zh.json` | aprisyourlie/IsaacAchievementGuide 整理；数据源自英文 wiki（CC BY-SA 4.0）与灰机 wiki（CC BY-NC-SA 3.0） | 按更严格的 CC BY-NC-SA 对待：署名、非商业、相同方式共享 |
| `assets/isaac_achievements.json`、`assets/isaac_boss_slots.json`、`assets/isaac_entities_zh.json`、`assets/isaac_minibosses_zh.json`、`assets/isaac_items_zh_names.json`、`assets/isaac_players_zh.json`、`assets/isaac_stages_zh.json`、`assets/isaac_curses_zh.json` | 玩家自装游戏的 `achievements.xml`、`entities2.xml` 与官方简体语言包 | 随 MIT 部分提供 |
| 全部代码与 `tools/` 脚本 | 本仓库 | MIT |
| `assets/fonts/` | Noto Sans SC 子集（Google / Noto 项目） | SIL Open Font License 1.1，全文见 `assets/fonts/OFL.txt` |

各参考项目与数据来源的许可说明逐条列在 [存档分析-可行性评估.md](存档分析-可行性评估.md) 第九节。

**为什么整包不是 MIT**：插件内置的 wiki 数据带 NonCommercial 条件，整包不能声明为 MIT。因此仓库按「wiki 数据按 CC 引入 → 整包 CC BY-NC-SA 4.0；代码与非 CC 数据单独 MIT」组织，两部分互不改变。不需要第三方中文成就表时，把 `save.use_wiki_zh_names` 关掉即可（道具图鉴与挑战名仍来自 wiki）。

### 致谢

- **以撒的结合中文 Wiki（灰机 wiki）** — <https://isaac.huijiwiki.com/>：道具 / 饰品 / 卡牌 / 药丸的名称、品质、标签、效果与协同，以及挑战中文名。
- **上游开源项目 [ChenDekang617/isaac-item-recommender](https://github.com/ChenDekang617/isaac-item-recommender)**：其 `knowledge/items.db.json` 是图鉴快照的数据来源，由 `tools/build_data.py` 转换。
- **存档格式**：二进制布局参考了社区公开的格式记录与实现——REPENTOGON（GPL-2.0）的 `SaveSyncing/SaveFile.h`、[isaac.ardor.guru 的格式说明](https://isaac.ardor.guru/guides/save-file-format/)、[Zamiell/isaac-save-viewer](https://github.com/Zamiell/isaac-save-viewer)、[Demorck/Isaac-save-manager](https://github.com/Demorck/Isaac-save-manager)。只取「字节布局」这类事实信息，解析器自行实现，未复制它们的代码或数据文件。
- **BOSS 槽位**：104 个槽位的下标就是游戏 `entities2.xml` 的 `bossID` 属性，由 `tools/archive_reader.py` 与 `tools/build_boss_table.py` 从游戏本体归档中取出；证据链见 [存档分析-可行性评估.md](存档分析-可行性评估.md) 附录 B。
- **官方译名**：怪物 / 小 BOSS / BOSS / 道具 / 角色 / 关卡 / 诅咒的中文名取自玩家自装游戏的官方简体语言包 `resources/packed/repentance_zh.a`，由 `tools/extract_game_strings.py` 提取。
- **成就中文表**：`assets/isaac_achievements_zh.json` 由 aprisyourlie/IsaacAchievementGuide 整理，数据来自英文 wiki 与灰机 wiki；默认叠加，可用 `save.use_wiki_zh_names` 关闭。
- **渲染字体**：[Noto Sans SC](https://fonts.google.com/noto/specimen/Noto+Sans+SC)，按插件实际字符集裁剪，SIL OFL 1.1。

### 仓库信息

- 仓库：<https://github.com/cateyemizuki/cateye_isaac_item>
- 问题反馈：<https://github.com/cateyemizuki/cateye_isaac_item/issues>
- 版本历史：[CHANGELOG.md](CHANGELOG.md)
- 存档格式与实现评估：[存档分析-可行性评估.md](存档分析-可行性评估.md)
- 插件类型 `game`，界面语言仅 `zh-CN`，图标 `gamepad-2`（`_manifest.json`）。
