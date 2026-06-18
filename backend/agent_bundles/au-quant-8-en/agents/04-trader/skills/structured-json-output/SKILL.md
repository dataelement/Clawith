---
name: structured-json-output
description: Structured JSON output spec for Shanghai-Gold futures trades — schema, hard constraints, unit checks, and retry rules on missing fields
---

# Skill — structured-json-output

## When to use

Attached to **④ Trader**. Used when the Trader translates the RM's investment_plan into structured JSON.

> This skill shares its JSON-schema definition with `signal-extraction` (to avoid upstream/downstream drift).

## Five-tuple JSON schema

```json
{
  "action": "buy" | "hold" | "sell",
  "target_price": <float, RMB/gram>,
  "confidence": <float, [0, 1]>,
  "risk_score": <float, [0, 1]>,
  "reasoning": "<string, ≤180 chars in English>"
}
```

## Field spec

| Field | Type | Constraint | Default / fallback |
|------|------|------|------|
| `action` | string | Strictly one of three: `"buy"` / `"hold"` / `"sell"`; **no variants** (e.g. "long", "strong buy") | Not allowed to be missing |
| `target_price` | float | Positive, unit **RMB/gram**, Shanghai Gold reasonable range about **[900, 1500]**; values `>3000` are USD/oz unit errors and MUST be converted (approx 1 USD/oz ≈ 0.23 RMB/gram) | Not allowed to be null / empty |
| `confidence` | float | `[0, 1]` closed interval | Use 0.7 if not explicit |
| `risk_score` | float | `[0, 1]` closed interval | Use 0.5 if not explicit |
| `reasoning` | string | ≤ 180 chars in English; distill the core rationale, do not list the analysis process | Not allowed to be empty |

## Output format contract

The Trader's **chat reply** consists of two parts (the decision lead reads your chat reply directly — you do NOT need to write a file):

1. **Natural-language summary** (≤ 300 chars in English) — a readable rationale for the decision lead and downstream Risk Judge
2. **Exactly one ```json code block** (at the end of the chat) — structured data for the decision lead + signal-extraction parsing

Example chat reply:

```
Based on the RM's investment plan, Shanghai Gold's technicals have broken resistance at 850, open interest is recovering, and central-bank purchases provide support — recommend BUY. Target 880 (base) / 920 (optimistic). Key risk: a more-hawkish-than-expected Fed meeting.

```json
{
  "action": "buy",
  "target_price": 880.0,
  "confidence": 0.72,
  "risk_score": 0.45,
  "reasoning": "Technical breakout above 850 + rebounding open interest + central-bank purchase support; a more-hawkish-than-expected Fed is the primary downside risk."
}
```
```

## Hard constraints & self-check

Before output, the Trader self-checks these 6 items:

1. ✅ Is `action` one of the three strings: buy / hold / sell?
2. ✅ Is `target_price` a positive number in [900, 1500]?
3. ✅ Is `target_price` a numeric value (not a string)?
4. ✅ Are `confidence` and `risk_score` both in [0, 1]?
5. ✅ Is `reasoning` ≤ 180 chars?
6. ✅ Is there exactly one JSON code block?

## Failure retry rules (driven by the decision lead)

After parsing your chat reply, if the decision lead finds an error, they will send you a follow-up message with an additional request:

| Error | Decision lead's follow-up | Trader's response |
|------|------|------|
| target_price is null / missing | "[hard constraint failed] please resend with a positive target_price" | Reissue the JSON; take target_price from the most specific value mentioned in reasoning |
| target_price > 3000 | "[hard constraint failed] target_price unit appears to be USD/oz — convert to RMB/gram (×0.23)" | Multiply the value by 0.23 and reissue |
| action not one of the three | "[hard constraint failed] action must be one of: buy / hold / sell" | Reissue |
| reasoning > 180 chars | "[hard constraint failed] compress reasoning to 180 chars" | Trim non-essential modifiers |
| Two JSON code blocks | "[hard constraint failed] keep only one JSON code block" | Delete the extras |

**Retry limit: 1**. Still failing → the decision lead marks degraded and continues.

## Relationship to upstream / downstream

- **Upstream**: the RM's investment_plan, embedded in the decision lead's message
- **Downstream 1**: the risk debate (⑤⑥⑦) reads your JSON via the decision lead's message
- **Downstream 2**: ⑧ Risk Judge reads your JSON via the decision lead's message as a "hard commitment" input
- **Downstream 3**: ⓪ Decision lead's `signal-extraction` skill checks at the final stage whether the Risk Judge overrides your JSON

## Anti-patterns

❌ Making a risk-decision call on behalf of the Risk Judge in `reasoning` (e.g., "MUST reduce position")
❌ Adding extra fields (e.g., `confidence_level: "high"`)
❌ Putting comments inside the JSON (standard JSON disallows it)
❌ Mixing languages inside one field (e.g., English `action` plus Chinese explanation in `reasoning`)
❌ Giving target_price as a range (e.g., "880-920") instead of a single value
❌ Replying only "written to workspace/trader_output.json" without pasting the JSON in the chat reply — the decision lead cannot read your workspace
