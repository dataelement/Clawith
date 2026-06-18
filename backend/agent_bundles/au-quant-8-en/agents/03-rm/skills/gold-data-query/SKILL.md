---
name: gold-data-query
description: When and how to call the Shanghai-Gold MCP to fetch the 4 data reports, and how to return them in full via chat reply to the decision lead (includes failure fallbacks)
---

# Skill — gold-data-query

## When to use

Attached to **③ Research Manager**. Used when the decision lead invokes RM with `mode=fetch`.

## MCP tool

- Server: `http://YOUR_DATA_HOST:8581` (address configured by the deployer)
- Tool behavior: batch returns `{market_report, fundamentals_report, news_report, sentiment_report}`; infers pre-market / intraday mode automatically from `anchor_time`
- Call budget: **at most 1 MCP call per decision** (all 4 reports come back together — do not re-invoke)

## Call flow

1. **Verify parameters**: the decision lead's message MUST include `anchor_date` (required) and `anchor_time` (optional)
2. **Call MCP**: invoke the MCP tool once
3. **Validate the return**:
   - Are all 4 report keys present?
   - Does `market_report` contain price fields?
   - Any report empty / null → fallback (see below)
4. **Return the full content in the chat reply** (critical — the decision lead cannot read your workspace):
   ```markdown
   # Shanghai Gold decision data pack — <date>
   anchor_time: <mode inferred from MCP: pre-market / intraday>
   token_estimate: <rough word count>

   ## market_report
   <full content>

   ## fundamentals_report
   <full content>

   ## news_report
   <full content>

   ## sentiment_report
   <full content>
   ```
5. **Optional**: also write the same content to your own `memory/data_<date>.md` as a local archive (so that in `mode=judge` you don't need to re-fetch)

## Failure fallbacks

| Failure | Handling |
|------|------|
| MCP timeout (>8s) | Retry once; if still timing out → explicitly write "[mcp_timeout]" in the chat reply, provide the most recent snapshot from memory (if any), and warn the decision lead: data confidence: degraded |
| MCP returns 401/403 | Check the agent's tool tab — is the MCP enabled? Do NOT retry. Reply "[mcp_auth_failed] please confirm the RM agent has enabled this MCP" |
| Some reports empty | Fill those sections with the placeholder "(no data available)" and tag "[partial: <field>]" |
| All 4 reports empty | Fail-fast back to the decision lead: "[mcp_returned_empty] the whole chain should switch to degraded mode" |

## When to re-query vs when the report is sufficient

This skill only fires once during the `mode=fetch` stage. **Re-calling MCP during the `mode=judge` stage is strictly forbidden** — the data is already in the message the decision lead sends you (cached from the fetch stage; the lead re-passes it).

If during `mode=judge` you feel the data is insufficient, you should:
1. In the investment plan, mark "data confidence: low"
2. Give a conservative target price (base case) + spell out the data limitations
3. Do NOT silently re-call MCP

## Output format contract

**Critical**: the chat reply MUST return the full text of all 4 reports — do not just send a file path or "written to file" notification. The decision lead cannot read your workspace.

## Call example

Input message (from the decision lead):
```
mode=fetch
anchor_date=2026-05-13
anchor_time=09:15:00
```

Output chat reply (to the decision lead):
```
# Shanghai Gold decision data pack — 2026-05-13
anchor_time: 09:15:00 (intraday)
token_estimate: ~15000

## market_report
| date | open | high | low | close | volume |
|------|------|------|------|-------|--------|
| 2026-05-12 | 850.2 | 856.4 | 849.1 | 854.2 | 12345 |
...
Anchor price: 854.20 RMB/gram (09:15 real-time)

## fundamentals_report
Open interest: 156,800 lots (-2.3% WoW)
Basis: +1.2 RMB/gram
...

## news_report
- 2026-05-12: The Fed kept rates unchanged at the May meeting...
- 2026-05-11: The Shanghai Gold Exchange...
... (full text of all 18 news items)

## sentiment_report
sentiment_score: 0.32 (neutral-bullish)
- News sentiment: 9 positive, 3 negative, 6 neutral
- Keyword cloud: central-bank gold purchases, USD weakness, ...

Mode: intraday
```
