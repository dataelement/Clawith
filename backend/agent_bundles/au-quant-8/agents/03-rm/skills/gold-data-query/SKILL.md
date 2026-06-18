---
name: gold-data-query
description: 何时与如何调用沪金 MCP 获取 4 份数据报告，并通过 chat reply 完整返回给决策主席（含失败兜底）
---

# Skill — gold-data-query

## 适用场景

挂在 **③ Research Manager** 上。当决策主席用 `mode=fetch` 调用 RM 时使用。

## MCP 工具

- 服务器: `http://YOUR_DATA_HOST:8581`（地址由部署方配置）
- 工具行为: 批量返回 `{market_report, fundamentals_report, news_report, sentiment_report}`，按 anchor_time 自动推断盘前/盘中模式
- 调用预算: **每次决策最多 1 次 MCP 调用**（4 报告一次拿完，不重复调）

## 调用流程

1. **核对参数**: 决策主席消息里必须有 `anchor_date`（必填）和 `anchor_time`（可选）
2. **调 MCP**: 调用一次该 MCP 工具
3. **校验返回**:
   - 4 份报告 key 是否都存在？
   - market_report 是否含价格字段？
   - 任何一份为空 / null → 兜底（见下）
4. **在 chat reply 中完整返回所有内容**（关键 — 决策主席读不到你的 workspace）:
   ```markdown
   # 沪金决策数据包 — <date>
   anchor_time: <从 MCP 推断的 mode：盘前 / 盘中>
   token_estimate: <粗估字数>

   ## market_report
   <完整内容>

   ## fundamentals_report
   <完整内容>

   ## news_report
   <完整内容>

   ## sentiment_report
   <完整内容>
   ```
5. **可选**: 同一份内容写到自己的 `memory/data_<date>.md` 作本地档案（供 mode=judge 时不需要再 fetch 时回查）

## 失败兜底

| 故障 | 处理 |
|------|------|
| MCP 超时（>8s） | 重试 1 次；仍超时 → 在 chat reply 中明确写 "[mcp_timeout]"，提供 memory 中最近一次快照（如有）+ 提醒决策主席 data confidence: degraded |
| MCP 返回 401/403 | 检查 agent 工具 tab 是否启用了该 MCP；不重试，回 "[mcp_auth_failed] 请确认 RM agent 已启用该 MCP" |
| 部分报告为空 | 用占位符 "（无可用数据）" 填入对应 section，标注 "[partial: <field>]" |
| 4 份全空 | 给决策主席 fail-fast 返回 "[mcp_returned_empty] 全链应转 degraded 模式" |

## 何时该再查 vs 何时报告够用

本 skill 只在 `mode=fetch` 阶段调用一次。**mode=judge 阶段严禁再调 MCP** — 数据已经在决策主席传给你的消息里了（决策主席从 fetch 阶段缓存了，会再传一遍）。

如果 mode=judge 时你觉得数据不足以判断，应该:
1. 在 investment_plan 中标注 "data confidence: low"
2. 给一个保守目标价（基准情景） + 写明数据局限
3. 不要悄悄再调 MCP

## 输出格式契约

**关键**: 必须在 chat reply 中返回完整的 4 份报告文本，不能只发文件路径或"已写入"提示。决策主席读不到你的 workspace。

## 调用示例

输入消息（来自决策主席）:
```
mode=fetch
anchor_date=2026-05-13
anchor_time=09:15:00
```

输出 chat reply（给决策主席）:
```
# 沪金决策数据包 — 2026-05-13
anchor_time: 09:15:00（盘中）
token_estimate: ~15000

## market_report
| 日期 | open | high | low | close | volume |
|------|------|------|------|-------|--------|
| 2026-05-12 | 850.2 | 856.4 | 849.1 | 854.2 | 12345 |
...
锚定价: 854.20 元/克 (09:15 实时)

## fundamentals_report
持仓量: 156,800 手 (-2.3% WoW)
基差: +1.2 元/克
...

## news_report
- 2026-05-12: 美联储 5 月议息维持利率不变...
- 2026-05-11: 上海黄金交易所...
...（18 条新闻全文）

## sentiment_report
sentiment_score: 0.32 (中性偏多)
- 新闻情绪正面 9 条，负面 3 条，中性 6 条
- 关键词云: 央行购金, 美元走弱, ...

模式: 盘中
```
