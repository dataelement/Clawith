---
name: structured-json-output
description: 沪金期货交易五元组 JSON 输出规范——schema、硬约束、单位校验、缺失重试规则
---

# Skill — structured-json-output

## 适用场景

挂在 **④ Trader** 上。Trader 把 RM 的 investment_plan 翻译成结构化 JSON 时使用。

> 本 skill 与 `signal-extraction` 共用同一份 JSON schema 定义（避免上下游漂移）。

## 五元组 JSON Schema

```json
{
  "action": "买入" | "持有" | "卖出",
  "target_price": <float, RMB/克>,
  "confidence": <float, [0, 1]>,
  "risk_score": <float, [0, 1]>,
  "reasoning": "<string, ≤120 字中文>"
}
```

## 字段规范

| 字段 | 类型 | 约束 | 默认 / 兜底 |
|------|------|------|------|
| `action` | string | 严格三选一: `"买入"` / `"持有"` / `"卖出"`；**禁止英文**、禁止变体（如"买"、"long"、"strong buy"） | 不允许缺失 |
| `target_price` | float | 正数，单位 **RMB/克**，沪金合理区间约 **[900, 1500]**；`>3000` 视为美元/盎司单位错误必须换算（约 1 美元/盎司 ≈ 0.23 元/克） | 不允许 null/空 |
| `confidence` | float | `[0, 1]` 闭区间 | 未明确时取 0.7 |
| `risk_score` | float | `[0, 1]` 闭区间 | 未明确时取 0.5 |
| `reasoning` | string | ≤ 120 字中文，提炼核心理由，不罗列分析过程 | 不允许空 |

## 输出格式契约

Trader 的 **chat reply** 由两部分组成（决策主席直接读你的 chat reply，不需要你写文件）:

1. **自然语言摘要**（≤ 200 字）— 给决策主席和后续 Risk Judge 看的可读理由
2. **唯一一个 ```json 代码块**（在 chat 末尾）— 给决策主席 + signal-extraction 解析的结构化数据

示例 chat reply:

```
基于 RM 投资计划，沪金技术面突破阻力位 850，资金面持仓量回升，建议买入。
目标价 880（基准）/ 920（乐观）。风险点：美联储议息超预期鹰。

```json
{
  "action": "买入",
  "target_price": 880.0,
  "confidence": 0.72,
  "risk_score": 0.45,
  "reasoning": "技术面破位 850 + 持仓回升 + 央行购金支撑；议息超预期鹰为主要下行风险。"
}
```
```

## 硬约束 & 自检

Trader 输出前自检以下 6 条:

1. ✅ `action` 是中文三选一之一？
2. ✅ `target_price` 是正数且在 [900, 1500]？
3. ✅ `target_price` 是数值不是字符串？
4. ✅ `confidence` 和 `risk_score` 都在 [0, 1]？
5. ✅ `reasoning` ≤ 120 字？
6. ✅ JSON 代码块只有一个？

## 失败重试规则（由决策主席驱动）

决策主席解析你的 chat reply 后若发现错误，会再给你发一条消息追加要求:

| 错误 | 决策主席追加消息 | Trader 应对 |
|------|------|------|
| target_price 为 null / 缺失 | "【硬约束未通过】请重写一份带正数 target_price 的回复" | 重新出 JSON，target_price 取 reasoning 中提到的最具体数值 |
| target_price > 3000 | "【硬约束未通过】target_price 单位疑似美元/盎司，请换算为人民币/克（×0.23）" | 把数值乘以 0.23 后重出 |
| action 是英文 | "【硬约束未通过】action 必须中文" | 重写 |
| reasoning > 120 字 | "【硬约束未通过】reasoning 压缩到 120 字" | 砍掉非关键修饰 |
| 两个 JSON 代码块 | "【硬约束未通过】只保留一个 JSON 代码块" | 删除多余 |

**重试上限: 1 次**。仍失败则决策主席记 degraded 并继续。

## 与上下游的关系

- **上游**: RM 的 investment_plan，通过决策主席消息嵌入
- **下游 1**: 风险辩论场（⑤⑥⑦）通过决策主席消息读到你的 JSON
- **下游 2**: ⑧ Risk Judge 通过决策主席消息读到你的 JSON 作为"硬承诺"输入
- **下游 3**: ⓪ 决策主席的 signal-extraction skill 在最终阶段会比对 Risk Judge 是否覆盖了你的 JSON

## 反模式

❌ 在 reasoning 里替 Risk Judge 拍板风险决策（"建议必须减仓"）
❌ 加多余字段（如 `confidence_level: "high"`）
❌ JSON 内放注释（标准 JSON 不允许）
❌ 用英文 action 然后在 reasoning 里中文解释
❌ target_price 给区间（如 "880-920"）而非单值
❌ 只回"已写入 workspace/trader_output.json"而不在 chat reply 里贴 JSON — 决策主席读不到你的 workspace
