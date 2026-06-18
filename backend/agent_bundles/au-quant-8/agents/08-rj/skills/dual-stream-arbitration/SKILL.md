---
name: dual-stream-arbitration
description: Risk Judge 双流入裁决框架——如何权衡 Trader 的硬承诺 vs 风险辩论的发散质疑，输出自然语言（不要输出 JSON）
---

# Skill — dual-stream-arbitration

## 适用场景

挂在 **⑧ Risk Judge** 上。Risk Judge 同时接收两路输入时使用本 skill 决定如何仲裁。

## 两路输入（决策主席消息嵌入）

| 流 | 来源 | 性质 | 形式 |
|----|------|------|------|
| **A** | ④ Trader（直通） | 硬承诺 | 五元组 JSON（action/target_price/confidence/risk_score/reasoning） |
| **B** | ⑤⑥⑦ 风险三方辩论 | 发散质疑 | 多轮自然语言 history |

> 这是 v2 的核心架构变化：Trader 的计划不再只通过风险辩论场间接到达 Judge — 它**同时直通**。

## 裁决框架（4 种典型场景）

### 场景 1: 辩论一致反对 Trader

**信号**: 三方都强烈质疑 Trader 的 action / target_price / confidence

**处理**:
- 显著调整 action（如 Trader=买入 → 改为持有 / 卖出）
- 把 confidence 调低（如 Trader=0.75 → 改到 0.4-0.5）
- target_price 取辩论中提到的更保守数值
- reasoning 明确引用辩论中反对最强的具体论点

### 场景 2: 辩论一致支持 Trader

**信号**: 激进 / 保守 / 中性都基本认同 Trader 计划，分歧只在程度

**处理**:
- 沿用 Trader 的 action 和 target_price
- 微调 risk_score（按三方共识的风险等级）
- confidence 可保持或微升
- reasoning 强调"辩论充分支持"

### 场景 3: 辩论三方分裂

**信号**: 激进强烈支持，保守强烈反对，中性骑墙

**处理**:
- 用消息里附带的 4 份报告数据作 tie-breaker:
  - 技术面 + 资金面偏哪边？
  - 新闻面是否有新增催化？
  - sentiment 倾向？
- 给一个折中但有立场的 action（不要持有当默认）
- confidence 调低（如 0.5-0.6）反映不确定
- reasoning 同时引用激进 / 保守的最强论点 + 数据决断依据

### 场景 4: 辩论缺席 / 异常短

**信号**: 风险辩论 history 不完整（< 2 轮）或某方明显敷衍

**处理**:
- 主要依赖 Trader JSON + 4 份报告
- confidence 显著调低（≤ 0.5）
- reasoning 明确标注 "[risk_debate_incomplete]"
- 不要假装辩论充分

## 输出格式契约

Risk Judge 把决策写到 **chat reply**（不是 workspace 文件 — 决策主席读不到你的文件），**至少包含以下结构化要素**（便于决策主席的 signal-extraction skill 抽取）:

```markdown
# 最终风险裁决 — <date>

## 关键论点总结
- 多头辩论中最强: <引用>
- 空头辩论中最强: <引用>
- 风险辩论场关键分歧: <一句话>

## 仲裁理由
<2-3 段，引用具体论点 + 数据，说明为什么倾向某一边>

## 最终建议
**Action: 买入 / 卖出 / 持有**（明确字面词，三选一）
**目标价: <数值> 元/克**
**Confidence: <0-1>**
**Risk score: <0-1>**

## 完善后的计划
<在 Trader 计划基础上做的调整说明>

## 经验教训
<可选，从 memory/reflections.md 中检索到的相关教训>
```

> ⚠️ **绝对不要输出 JSON 代码块** — 那是决策主席的 signal-extraction 工作。Risk Judge 写**自然语言**，但其中含**清晰可抽取**的结构化字面值（"Action: 买入"、"目标价: 880 元/克" 等）。

## 反模式

❌ "建议持有 — 双方都有道理"（持有不能当默认）
❌ "建议进一步研究"（必须给可操作建议）
❌ 不引用具体辩论原文 → reasoning 没说服力
❌ **输出 JSON 代码块** → 抢决策主席的活
❌ 完全沿用 Trader JSON 而忽略辩论 → 那 Risk Judge 没意义
❌ 只回"已写入 workspace/risk_judge.md" → 决策主席读不到你的文件，必须把决策完整写在 chat reply 里

## 与决策主席的协议

- Risk Judge 把完整决策写在 chat reply 里返回
- 决策主席接着自己执行 signal-extraction skill（基于你的 chat reply），抽取最终 JSON
- 若 Risk Judge 完全失败（重试均超时），决策主席兜底输出 `{"action":"持有", "confidence":0.5, "risk_score":0.5, "reasoning":"Risk Judge 失败 [risk_judge_failed]"}`
