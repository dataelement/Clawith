---
name: dual-stream-arbitration
description: Risk Judge dual-input arbitration framework — how to weigh the Trader's hard commitment against the risk-debate divergent challenges; output natural language (do NOT output JSON)
---

# Skill — dual-stream-arbitration

## When to use

Attached to **⑧ Risk Judge**. Used when the Risk Judge receives two streams of input and needs to decide how to arbitrate.

## Two input streams (embedded in the decision lead's message)

| Stream | Source | Nature | Form |
|----|------|------|------|
| **A** | ④ Trader (direct) | Hard commitment | Five-tuple JSON (action / target_price / confidence / risk_score / reasoning) |
| **B** | ⑤⑥⑦ Three-perspective risk debate | Divergent challenges | Multi-round natural-language history |

> This is the core architectural change in v2: the Trader's plan no longer reaches the Judge only indirectly through the risk debate — it **also arrives directly**.

## Arbitration framework (4 typical scenarios)

### Scenario 1: Debate uniformly opposes the Trader

**Signal**: all three sides strongly challenge the Trader's action / target_price / confidence

**Handling**:
- Significantly adjust action (e.g., Trader=buy → change to hold / sell)
- Lower the confidence (e.g., Trader=0.75 → 0.4-0.5)
- Set target_price to the more conservative value mentioned in the debate
- Explicitly cite the strongest counterargument from the debate in `reasoning`

### Scenario 2: Debate uniformly supports the Trader

**Signal**: aggressive / conservative / neutral all broadly endorse the Trader's plan; disagreement only on degree

**Handling**:
- Keep the Trader's action and target_price
- Lightly adjust risk_score (per the three-side consensus risk level)
- Confidence may hold or rise slightly
- Emphasize "debate fully supports" in `reasoning`

### Scenario 3: Debate is split three ways

**Signal**: aggressive strongly supports, conservative strongly opposes, neutral on the fence

**Handling**:
- Use the 4 reports embedded in the message as the tie-breaker:
  - Which side do the technicals + capital flows favor?
  - Is there a fresh news catalyst?
  - What's the sentiment lean?
- Give a compromise but stanced action (do NOT default to hold)
- Lower confidence (e.g., 0.5-0.6) to reflect uncertainty
- Cite the strongest aggressive AND conservative arguments + the data-driven tie-breaker in `reasoning`

### Scenario 4: Debate is absent / abnormally short

**Signal**: risk debate history is incomplete (< 2 rounds) or one side is clearly perfunctory

**Handling**:
- Rely primarily on the Trader JSON + the 4 reports
- Significantly lower confidence (≤ 0.5)
- Explicitly tag "[risk_debate_incomplete]" in `reasoning`
- Do NOT pretend the debate was thorough

## Output format contract

The Risk Judge writes the decision into the **chat reply** (not a workspace file — the decision lead cannot read your files). It **MUST contain the following structured elements** (so the decision lead's `signal-extraction` skill can extract them):

```markdown
# Final risk verdict — <date>

## Key argument summary
- Strongest bull-debate point: <quote>
- Strongest bear-debate point: <quote>
- Key risk-debate disagreement: <one sentence>

## Arbitration reasoning
<2-3 paragraphs; cite specific arguments + data; explain why you leaned one way>

## Final recommendation
**Action: buy / sell / hold** (use the explicit literal word, one of the three)
**Target price: <value> RMB/gram**
**Confidence: <0-1>**
**Risk score: <0-1>**

## Refined plan
<adjustments to the Trader's plan>

## Lessons learned
<optional; relevant lessons retrieved from memory/reflections.md>
```

> ⚠️ **NEVER output a JSON code block** — that's the decision lead's `signal-extraction` job. The Risk Judge writes **natural language**, but with **clearly extractable** structured literal values inside (e.g. "Action: buy", "Target price: 880 RMB/gram", etc.).

## Anti-patterns

❌ "Recommend hold — both sides have a point" (hold cannot be the default)
❌ "Recommend further research" (you MUST give an actionable recommendation)
❌ Not citing specific debate quotes → reasoning lacks weight
❌ **Outputting a JSON code block** → poaching the decision lead's work
❌ Fully echoing the Trader JSON while ignoring the debate → that makes the Risk Judge pointless
❌ Replying only "written to workspace/risk_judge.md" → the decision lead cannot read your files; the full decision MUST go in the chat reply

## Protocol with the decision lead

- The Risk Judge writes the complete decision into the chat reply
- The decision lead then runs `signal-extraction` on your chat reply to extract the final JSON
- If the Risk Judge completely fails (retries all time out), the decision lead falls back to `{"action":"hold", "confidence":0.5, "risk_score":0.5, "reasoning":"Risk Judge failed [risk_judge_failed]"}`
