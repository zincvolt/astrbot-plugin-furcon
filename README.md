# astrbot-plugin-furcon

兽展（furry convention）工具插件，用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)。

所有回答都是一张图片卡片，不带多余文字。既可以被斜杠命令触发，也可以像叫朋友一样喊机器人名字触发。

## 功能

- **下次展会倒计时**：抓 [FEC 兽展日历](https://www.furrycons.cn) 的数据，渲染倒计时卡片。
- **每日运势**：`/运势`、`/jrrp`、`/jrys`、`/人品` 生成今日人品卡（当天缓存，重复问复用同一张）。
- **群统计**：基于本地数据库统计群内发言数据。
- **展会播报**：后台定时器按配置向指定群广播临近展会。
- **帮助**：`/兽聚help`。

触发方式示例：`/下次展` 或 `阿灰 下次展`。

卡片按"日期 + 查询"做本地缓存，重复提问不会重复渲染，也不会重复请求上游站点。FEC 站点是 Next.js，事件列表嵌在 `__NEXT_DATA__` 中，插件解析它并缓存数小时。

## 依赖

- AstrBot 4.28+
- `httpx`、`Pillow`（PIL）及中文字体
- 能访问 furrycons.cn（抓日历）

## 许可证

AGPL-3.0-or-later，见 [LICENSE](LICENSE)。
