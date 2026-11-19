# astrbot-plugin-furcon

兽聚（furry convention）查询工具插件，用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)。

所有回答都是一图流。既可以被斜杠命令触发，也可以像叫朋友一样喊机器人名字触发。数据来自 [FEC 兽展日历](https://www.furrycons.cn)。
（安装见[安装](#安装)）。

---

## 功能

| 功能 | 说明 |
|---|---|
| 下次展会倒计时 | 抓 FEC 日历，渲染最近一场展会的倒计时卡片（宣传图 + 场地/主办 + 之后几场） |
| 近期展会一览 | 近期 10 场 + 刚结束 2 场；可按城市/省份过滤 |
| 某地已办展会 | 灰色卡片展示该城市最近办过的展会 |
| 特别关心（定时播报） | 管理员设置某展会 + 时间，每天定时向指定会话播报倒计时 |
| 群开关 | 管理员按群启用/关闭本插件全部功能 |

## 附加功能

| 功能 | 说明 |
|---|---|
| 每日运势 | 今日人品卡，按 QQ + 日期定死，当天缓存复用 |
| 群统计 | 基于 AstrBot 数据库统计本群近 7 天发言活跃（发言榜 + 时段柱状图） |


---

## 安装

### 1. 克隆本仓库

进入 AstrBot 的插件目录，克隆本仓库（目录名以 `astrbot_plugin_` 开头即可，AstrBot 会自动识别）：

```bash
cd <你的 AstrBot 目录>/data/plugins
git clone https://github.com/zincvolt/astrbot-plugin-furcon.git astrbot_plugin_furcon
```

### 2. 安装依赖

```bash
# 在 AstrBot 使用的 Python 环境中
pip install httpx pillow
```

图片字体已内嵌（**Noto Sans SC Bold**，SIL OFL 1.1，见 `fonts/OFL.txt`），想换字体就设置 `FURCON_FONT` 指向任意字体文件；若连内嵌字体一起删了，才会回退到系统字体（可能会显示方块）。

### 3. 配置修改（可选，绝大多数情况可跳过）

插件-应该-会自动推断数据位置，**单机默认安装的 AstrBot 无需任何配置**。仅当你的目录结构特殊时，才需要设置环境变量：

| 环境变量 | 作用 | 默认值 |
|---|---|---|
| `QQNTBOT_ROOT` | 数据根目录，缓存/卡片/开关写在其 `data/` 下 | 自动：向上找 `apps/AstrBot/data` 结构，找不到就用插件自身目录 |
| `FURCON_BOT_DB` | AstrBot 数据库 `data_v4.db` 路径（群统计用） | 自动：`plugins/` 上两级的 `data/data_v4.db` |
| `FURCON_NAME_CALL` | 「喊名字触发」的名字正则 | `(阿灰\|小灰\|灰狼)` |
| `FURCON_ADMINS` | 管理员 QQ，多个用逗号分隔 | `1154487120`（记得改，这个是我的QQ号！） |
| `FURCON_FONT` | 自定义字体文件绝对路径（多个用 `;` 分隔）（虽然好像不支持多字体） | 内嵌的 Noto Sans SC Bold |

PowerShell 示例：

```powershell
$env:QQNTBOT_ROOT = "D:\MyBot"
$env:FURCON_ADMINS = "123456,789012"
$env:FURCON_NAME_CALL = "(团子|阿团)"
```

> 环境变量需在启动 AstrBot 前设置（写进你的启动脚本）。

### 4. 启用功能

重启 AstrBot 让插件加载。然后在**对应群里**用管理员 QQ 发一条：

```
/启用功能
```

每个群需要单独启用；未启用的群只会收到一句提示，不会出卡片。（或者在 `data/furcon_enabled.json` 直接改）

### 5. 验证

```
/兽聚help      → 返回帮助卡片/文本
/下次展        → 返回倒计时图片（需能访问 furrycons.cn）
/运势          → 返回人品卡
```

---

## 触发

一条消息要触发本插件，需要**同时满足两步**：

```
① 触发方式（二选一）
     - 斜杠：消息以 / 或 ／ 开头，例如 /下次展
     - 喊名字：消息里出现机器人名字（默认 阿灰/小灰/灰狼），例如 阿灰 下次展
② 命中词表：消息内容匹配下面某条触发词规则
```

规则细节：

- **不带斜杠、不喊名字 → 不触发**，消息正常走大模型聊天。
- **带斜杠但没命中任何规则 → 本插件完全不接管**，事件继续传给其他插件（不会误触发聊天，应该也不会抢别的插件的 `/指令`）。
- **城市名会被自动提取**（从 `CITY_NAMES` / `PROVINCES` 里匹配），用于城市类查询，例如 `/成都下次展`。
- **群功能开关**：未启用的群只响应帮助命令。
- **管理员功能**：`/启用功能`、`/关闭功能`、`/关心`、`/取消关心` 只有符合 `FURCON_ADMINS` 里的 QQ 能用。

### 特别关心（定时播报）

管理员设置后，插件每分钟检查一次，到点向指定会话播报一次（每天最多一次）（时间使用24h制）（没测试过这个功能）：

```
/关心 海洋之泪 09:00    → 每天 09:00 播报「海洋之泪」这场展会的倒计时
/取消关心               → 关闭播报
```

配置存在数据根目录的 `data/furcon_watch.json`，改文件后无需重启。

---

## -完整-触发词表

# 模糊指令匹配：
展会查询是**组合式**的，在源码里对应
`EVENT_TIME_WORDS` / `EVENT_KIND_WORDS` / `EVENT_EXTRA_MARKERS` / `COMMANDS`。

指令可以拆成这几块，**顺序随意、可省略**：

```
[时序] + [范围] + [地名] + [类型]

时序：上次 / 上一次 / 上回 / 上届 / 办过 / 过去
      下次 / 下一次 / 下一场 / 下场 / 下回 / 接下来 / 还有几天 / 倒计时
范围：近期 / 最近 / 前瞻 / 展会一览
地名：CITY_NAMES / PROVINCES 里的任意城市或省份（如 苏州 / 江苏）
类型：展 / 聚 / 兽聚 / 兽展 / 展会 / 漫展（写法随意，结果一样）
```

判定规则：最早出现的时序词决定走 `city_past` / `city_next`（`next`）/
`city_recent`（`recent`）；没有时序词但有类型词 → `city_list`；
有地名就把地名带上做过滤。**`展` 和 `聚` 完全等价** —— 比如 `/江苏下次展` 和 `/江苏下次聚` 出的是同一张倒计时卡。

固定语法的指令（优先匹配，避免被上面的词误伤）：

| 功能 | 内部 key | 触发词 |
|---|---|---|
| 帮助 | `help` | `兽聚help` `兽聚指令` `兽聚功能` `兽聚用法`（整句） |
| 运势 / 人品 | `fortune` | `运势` `运气` `抽个签` `求签` `吉凶` `今日份` `转运` `人品` `jrrp` `jrys` |
| 群统计 | `stats` | `群统计` `发言统计` `谁最活跃` `水群榜` `活跃度` `话最多` `谁话多` |
| 特别关心 | `watch` | `关心 <展会名> [HH:MM]` `特别关心 …` `关注 …`（管理员） |
| 取消关心 | `watch_off` | `取消关心` `不关心了` `取消关注`（管理员） |
| 启用本群 | `enable_on` | `启用功能` `开启功能` `打开功能` `启用`（整句，管理员） |
| 关闭本群 | `enable_off` | `关闭功能` `停用功能` `禁用功能` `关闭`（整句，管理员） |

实测样例：

```
/下次展              → next（全局最近一场）
/江苏下次聚          → city_next  (城市=江苏，单场卡)
/江苏下次展          → city_next  (与上一行结果一致)
/下一次苏州兽聚      → city_next  (城市=苏州)
/近期展              → recent（带上城市时按城市过滤）
/成都近期聚          → city_recent  (城市=成都)
/苏州上次展          → city_past  (城市=苏州)
阿灰 下次展          → next（喊名字触发）
小灰运势             → fortune
/关心 海洋之泪 09:00  → watch
/苏州乱写指令        → 不触发，事件交给别的插件
今天天气不错         → 不触发
```

> 语义区分：`/下次展` 只出一场（最近的），`/近期展` 出列表。判断依据是
> 句子里有没有「上次 / 下次 / 近期 / 最近」这类词

---

## 修改位置（改代码时看这里）

**以函数名为准**（VSCode：`Ctrl+Shift+O` ）。

### 环境变量 / 配置（不用改代码）

| 想改什么 | 方式 |
|---|---|
| 数据目录 | 环境变量 `QQNTBOT_ROOT` |
| 管理员列表 | 环境变量 `FURCON_ADMINS` |
| Bot触发词 | 环境变量 `FURCON_NAME_CALL` |
| 字体路径 | 环境变量 `FURCON_FONT` 指向字体文件**绝对路径** |
| 群开关 / 定时播报 | 直接编辑 `data/furcon_enabled.json`、`data/furcon_watch.json`（无需重启） |

### 源码符号（改功能时）

打开 `main.py`，按符号名搜索：

| 想改什么 | 符号 | 说明 |
|---|---|---|
| 固定语法指令词 | `COMMANDS` | 每条 `(key, 正则)`，先于组合匹配 |
| 组合词（时序/类型/杂项） | `EVENT_TIME_WORDS` `EVENT_KIND_WORDS` `EVENT_EXTRA_MARKERS` | 展会查询的组合式触发词 |
| 内嵌字体 | `BUNDLED_FONT` / `fonts/` | 文件在 `fonts/NotoSansSC-Bold.ttf`，换字体用 `FURCON_FONT` |
| 认哪些城市 / 省份 | `CITY_NAMES` / `PROVINCES` | 市级用于提取，省级用于识别 |
| 省份展开成哪些城市 | `PROVINCE_CITIES` | `/江苏下次展` 靠这个映射 |
| 机器人名字 / 管理员 | `NAME_CALL` / `ADMINS` | 也可用环境变量覆盖 |
| 帮助文案 | `HELP_TEXT` | `/兽聚help` 原样输出 |
| 运势等级 / 提示语 | `FORTUNE_LEVELS` / `FORTUNE_TIPS` | |
| 卡片样式 | `render_next()` `render_recent()` `render_city()` `render_fortune()` `render_stats()` | |
| 指令匹配规则 | `match_command()` | 斜杠 / 喊名字 / 城市提取 |
| 数据源地址 / 缓存时长 | `SOURCE_URL` / `CACHE_TTL` | |

### 数据文件（直接编辑 JSON）

| 文件相对目录 | 作用 | 改完要重启吗 |
|---|---|---|
| `data/furcon_enabled.json` | 哪些群启用了功能 | 不用，记得保存 |
| `data/furcon_watch.json` | 特别关心的展会和播报时间 | 不用 |
| `data/furcons_cache.json` | 展会数据缓存（6 小时 TTL） | 会自动重新抓取 |
| `data/furcons_cards/` | 生成过的图片（按天缓存） | 删掉会重新生成 |

> 数据文件的实际位置由 `QQNTBOT_ROOT` 决定，见[安装](#安装)。

---

## 依赖

- AstrBot 4.28+
- Python 库：`httpx`、`Pillow`
- 能访问 `furrycons.cn`（抓取日历）与 `images.furrycons.cn`（卡片宣传图）

---

## 许可证

AGPL-3.0-or-later，见 [LICENSE](LICENSE)。
