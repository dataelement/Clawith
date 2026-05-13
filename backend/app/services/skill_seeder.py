"""Seed builtin skills into the global skill registry."""

from loguru import logger
from sqlalchemy import select
from app.database import async_session
from app.models.skill import Skill, SkillFile


BUILTIN_SKILLS = [
    {
        "name": "Web Research",
        "description": "Systematic web searching and information synthesis. Use when: needing factual data from the web, evaluating sources, or cross-referencing claims. NOT for: simple trivia or local file search.",
        "category": "research",
        "icon": "🔍",
        "folder_name": "web-research",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Web Research
description: Systematic web searching, source evaluation, and information synthesis
---

# Web Research

## Overview
Use this skill when you need to find, evaluate, and synthesize information from the web.

**Keywords**: web search, information retrieval, source evaluation, fact-checking, research

## Process

### 1. Define Search Strategy
- Identify key search terms and variations
- Consider different angles and perspectives
- Plan multiple search queries

### 2. Evaluate Sources
- Check source credibility and recency
- Cross-reference claims across multiple sources
- Note publication dates and author expertise

### 3. Synthesize Findings
- Organize information by theme or relevance
- Highlight key findings and consensus views
- Note conflicting information and gaps

## Output Format
- Start with a brief summary of findings
- Provide detailed sections with source citations
- End with confidence assessment and limitations
""",
            },
            {
                "path": "scripts/search_helper.py",
                "content": (
                    "#!/usr/bin/env python3\n"
                    '"""Helper utilities for structured web search."""\n\n'
                    "from datetime import datetime\n\n\n"
                    "def format_search_results(results: list[dict]) -> str:\n"
                    '    """Format raw search results into a structured report."""\n'
                    "    output = []\n"
                    "    for i, r in enumerate(results, 1):\n"
                    "        title = r.get('title', 'Untitled')\n"
                    "        url = r.get('url', '#')\n"
                    "        snippet = r.get('snippet', 'No description')\n"
                    "        output.append(f'{i}. [{title}]({url})')\n"
                    "        output.append(f'   {snippet}')\n"
                    "        output.append('')\n"
                    "    return '\\n'.join(output)\n\n\n"
                    "def assess_source_credibility(url: str) -> dict:\n"
                    '    """Basic heuristics for source credibility."""\n'
                    "    trusted = ['.edu', '.gov', '.org', 'arxiv.org', 'nature.com']\n"
                    "    score = 0.5\n"
                    "    for d in trusted:\n"
                    "        if d in url:\n"
                    "            score = 0.8\n"
                    "            break\n"
                    "    return {'url': url, 'credibility_score': score,\n"
                    "            'assessed_at': datetime.now().isoformat()}\n"
                ),
            },
        ],
    },
    {
        "name": "Data Analysis",
        "description": "Data interpretation and structured reporting. Use when: analyzing CSV/dataset files, finding trends, or generating statistical summaries. NOT for: writing code to build data models.",
        "category": "analysis",
        "icon": "📊",
        "folder_name": "data-analysis",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Data Analysis
description: Data interpretation, pattern recognition, and structured reporting
---

# Data Analysis

## Overview
Use this skill for analyzing data, identifying patterns, and creating structured reports.

**Keywords**: data analysis, statistics, trends, visualization, reporting

## Process

### 1. Data Understanding
- Identify data types, ranges, and distributions
- Check for missing values and anomalies
- Understand the business context

### 2. Analysis Methods
- Descriptive statistics (mean, median, distribution)
- Trend analysis (time-series patterns)
- Comparative analysis (benchmarking, A/B)
- Correlation and relationship discovery

### 3. Reporting
- Lead with key insights and actionable findings
- Use tables and structured formats for clarity
- Include methodology notes for reproducibility

## Output Format
- Executive summary with top 3 findings
- Detailed analysis with supporting data
- Recommendations based on findings
""",
            },
            {
                "path": "scripts/analyze_csv.py",
                "content": (
                    "#!/usr/bin/env python3\n"
                    '"""Utility for quick CSV data analysis."""\n\n'
                    "import csv\nimport statistics\nfrom collections import Counter\n\n\n"
                    "def analyze_column(data: list[dict], column: str) -> dict:\n"
                    '    """Analyze a single column from CSV data."""\n'
                    "    values = [row.get(column) for row in data if row.get(column) is not None]\n"
                    "    if not values:\n"
                    '        return {"column": column, "count": 0, "error": "No data"}\n\n'
                    '    result = {"column": column, "count": len(values), "unique": len(set(values))}\n\n'
                    "    # Try numeric analysis\n"
                    "    try:\n"
                    "        nums = [float(v) for v in values]\n"
                    "        result.update({\n"
                    '            "type": "numeric",\n'
                    '            "min": min(nums), "max": max(nums),\n'
                    '            "mean": round(statistics.mean(nums), 2),\n'
                    '            "median": round(statistics.median(nums), 2),\n'
                    "        })\n"
                    "    except (ValueError, TypeError):\n"
                    "        freq = Counter(values).most_common(5)\n"
                    '        result.update({"type": "categorical", "top_values": freq})\n\n'
                    "    return result\n\n\n"
                    "def quick_summary(filepath: str) -> str:\n"
                    '    """Generate a quick summary of a CSV file."""\n'
                    "    with open(filepath, 'r') as f:\n"
                    "        reader = csv.DictReader(f)\n"
                    "        data = list(reader)\n"
                    "    columns = data[0].keys() if data else []\n"
                    "    return f'Rows: {len(data)}, Columns: {len(columns)}'\n"
                ),
            },
            {
                "path": "examples/sample_report.md",
                "content": """# Sample Analysis Report

## Executive Summary
Analysis of Q4 2024 sales data reveals a 12% increase in total revenue,
driven primarily by the Enterprise segment (+23%).

## Key Findings
1. **Revenue Growth**: Total revenue increased from $2.1M to $2.35M
2. **Top Segment**: Enterprise accounts grew 23% QoQ
3. **Churn**: SMB churn rate decreased from 5.2% to 4.1%

## Detailed Analysis

| Metric | Q3 2024 | Q4 2024 | Change |
|--------|---------|---------|--------|
| Total Revenue | $2.1M | $2.35M | +12% |
| Enterprise | $1.2M | $1.47M | +23% |
| SMB | $0.9M | $0.88M | -2% |
| Churn Rate | 5.2% | 4.1% | -1.1pp |

## Recommendations
1. Increase investment in Enterprise sales team
2. Investigate SMB revenue decline
3. Continue churn reduction initiatives
""",
            },
        ],
    },
    {
        "name": "Content Writing",
        "description": "Professional content creation and tone adaptation. Use when: drafting articles, emails, or marketing copy with specific stylistic requirements. NOT for: casual chat responses.",
        "category": "creation",
        "icon": "✍️",
        "folder_name": "content-writing",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Content Writing
description: Professional content creation, editing, and tone adaptation
---

# Content Writing

## Overview
Use this skill for creating, editing, and polishing written content across formats.

**Keywords**: writing, editing, copywriting, tone, style, proofreading

## Content Types
- **Articles & Blog Posts**: Informative, engaging long-form content
- **Business Communications**: Emails, memos, reports
- **Marketing Copy**: Headlines, descriptions, calls-to-action
- **Documentation**: Technical docs, guides, FAQs

## Guidelines

### Structure
- Hook readers with a compelling opening
- Use clear headings and logical flow
- Keep paragraphs short (3-5 sentences)
- End with a clear conclusion or call-to-action

### Tone Adaptation
- **Formal**: Business reports, official communications
- **Professional**: Client-facing content, documentation
- **Conversational**: Blog posts, social media
- **Technical**: Developer docs, specifications

### Quality Checklist
- [ ] Clear main message
- [ ] Consistent tone throughout
- [ ] No grammatical errors
- [ ] Appropriate length for format
""",
            },
        ],
    },
    {
        "name": "Competitive Analysis",
        "description": "Competitor research and comparison frameworks. Use when: asked to compare companies, products, or perform SWOT/feature matrix analysis. NOT for: general academic research.",
        "category": "research",
        "icon": "⚔️",
        "folder_name": "competitive-analysis",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Competitive Analysis
description: Market competitor research, comparison frameworks, and strategic insights
---

# Competitive Analysis

## Overview
Use this skill for analyzing competitors, market positioning, and strategic opportunities.

**Keywords**: competitors, market analysis, SWOT, positioning, benchmarking

## Frameworks

### SWOT Analysis
| | Helpful | Harmful |
|---|---|---|
| **Internal** | Strengths | Weaknesses |
| **External** | Opportunities | Threats |

### Feature Comparison Matrix
Compare products across key dimensions:
- Core features and capabilities
- Pricing and packaging
- Target audience
- Market positioning
- Technology stack

### Porter's Five Forces
1. Competitive rivalry intensity
2. Bargaining power of suppliers
3. Bargaining power of buyers
4. Threat of new entrants
5. Threat of substitutes

## Output Format
- Competitor overview table
- Detailed per-competitor analysis
- Strategic recommendations
- Key differentiators summary
""",
            },
        ],
    },
    {
        "name": "Meeting Notes",
        "description": "Meeting summarization and follow-up tracking. Use when: given meeting transcripts or rough notes to extract structured action items and key decisions. NOT for: generic document summarization.",
        "category": "productivity",
        "icon": "📝",
        "folder_name": "meeting-notes",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Meeting Notes
description: Meeting summarization, action item extraction, and follow-up tracking
---

# Meeting Notes

## Overview
Use this skill for processing meeting content into structured summaries with clear action items.

**Keywords**: meetings, notes, action items, decisions, follow-up

## Template

### Meeting Summary
```
Meeting: [Title]
Date: [Date]
Participants: [Names]
Duration: [Time]
```

### Key Decisions
- Numbered list of decisions made

### Action Items
| # | Action | Owner | Due Date | Status |
|---|--------|-------|----------|--------|
| 1 | [Task] | [Name] | [Date] | ⬜ Pending |

### Discussion Points
Brief summary of main topics discussed

### Next Steps
- Follow-up meeting date
- Items deferred to next meeting
""",
            },
        ],
    },
    {
        "name": "Complex Task Executor",
        "description": "Structured methodology for decomposing, planning, and executing complex multi-step tasks with progress tracking",
        "category": "productivity",
        "icon": "🎯",
        "folder_name": "complex-task-executor",
        "is_default": True,
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Complex Task Executor
description: Structured methodology for decomposing, planning, and executing complex multi-step tasks with progress tracking
---

# Complex Task Executor

## When to Use This Skill

Use this skill when a task meets ANY of the following criteria:
- Requires more than 3 distinct steps to complete
- Involves multiple tools or information sources
- Has dependencies between steps (step B needs output from step A)
- Requires research before execution
- Could benefit from a documented plan others can review
- The user explicitly asks for a thorough or systematic approach

**DO NOT use this for simple tasks** like answering a question, reading a single file, or performing one tool call.

## Workflow

### Phase 1: Task Analysis (THINK before acting)

Before creating any files, analyze the task:

1. **Understand the goal**: What is the final deliverable? What does "done" look like?
2. **Assess complexity**: How many steps? What tools are needed?
3. **Identify dependencies**: Which steps depend on others?
4. **Identify risks**: What could go wrong? What information is missing?
5. **Estimate scope**: Is the task feasible with available tools/skills?

### Phase 2: Create Task Plan

Create a task folder and plan file in the workspace:

```
workspace/<task-name>/plan.md
```

The plan.md MUST follow this exact format:

```markdown
# Task: <Clear title>

## Objective
<One-sentence description of the desired outcome>

## Steps

- [ ] 1. <First step — verb-noun format>
  - Details: <What specifically to do>
  - Output: <What this step produces>
- [ ] 2. <Second step>
  - Details: <...>
  - Depends on: Step 1
- [ ] 3. <Third step>
  - Details: <...>

## Status
- Created: <timestamp>
- Current Step: Not started
- Progress: 0/<total>

## Notes
<Any assumptions, risks, or open questions>
```

Rules for writing the plan:
- Each step should be completable in 1-3 tool calls
- Use verb-noun format: "Research competitors", "Draft report", "Validate data"
- Mark dependencies explicitly
- Include expected outputs for each step

### Phase 3: Execute Step-by-Step

For EACH step in the plan:

1. **Read the plan** — Call `read_file` on `workspace/<task>/plan.md` to check current state
2. **Mark as in-progress** — Update the checkbox from `[ ]` to `[/]` and update the "Current Step" field
3. **Execute the step** — Do the actual work (tool calls, analysis, writing)
4. **Record output** — Save results to `workspace/<task>/` (e.g., intermediate files, data)
5. **Mark as complete** — Update the checkbox from `[/]` to `[x]` and update "Progress" counter
6. **Proceed to next step** — Move to the next uncompleted step

### Phase 4: Completion

When all steps are done:
1. Update plan.md status to "✅ Completed"
2. Create a `workspace/<task>/summary.md` with:
   - What was accomplished
   - Key results and deliverables
   - Any follow-up items
3. Present the final result to the user

## Adaptive Replanning

If during execution you discover:
- A step is impossible → Mark it `[!]` with a reason, add alternative steps
- New steps are needed → Add them to the plan with `[+]` prefix
- A step produced unexpected results → Add a note and adjust subsequent steps
- The plan needs major changes → Create a new section "## Revised Plan" and follow it

Always update plan.md BEFORE changing course, so the plan stays the source of truth.

## Error Handling

- If a tool call fails, retry once. If it fails again, mark the step as blocked and note the error.
- Never silently skip a step. Always update the plan to reflect what happened.
- If you're stuck, tell the user what's blocking and ask for guidance.

## Example Scenarios

### Example 1: "Research our top 3 competitors and write a comparison report"

Plan would be:
```
- [ ] 1. Identify the user's company/product context
- [ ] 2. Research Competitor A — website, pricing, features
- [ ] 3. Research Competitor B — website, pricing, features
- [ ] 4. Research Competitor C — website, pricing, features
- [ ] 5. Create comparison matrix
- [ ] 6. Write analysis and recommendations
- [ ] 7. Compile final report
```

### Example 2: "Analyze our Q4 sales data and prepare a board presentation"

Plan would be:
```
- [ ] 1. Read and understand the sales data files
- [ ] 2. Calculate key metrics (revenue, growth, trends)
- [ ] 3. Identify top insights and anomalies
- [ ] 4. Create data summary tables
- [ ] 5. Draft presentation outline
- [ ] 6. Write each presentation section
- [ ] 7. Add executive summary
- [ ] 8. Review and polish final document
```

## Key Principles

1. **Plan is the source of truth** — Always update it before moving on
2. **One step at a time** — Don't skip ahead or batch too many steps
3. **Show your work** — Save intermediate results to the task folder
4. **Communicate progress** — The user can read plan.md at any time to see status
5. **Be adaptive** — Plans change; that's OK if you update the plan first
""",
            },
            {
                "path": "examples/plan_template.md",
                "content": """# Task: [Title]

## Objective
[One-sentence description of the desired outcome]

## Steps

- [ ] 1. [First step]
  - Details: [What specifically to do]
  - Output: [What this step produces]
- [ ] 2. [Second step]
  - Details: [...]
  - Depends on: Step 1
- [ ] 3. [Third step]
  - Details: [...]

## Status
- Created: [timestamp]
- Current Step: Not started
- Progress: 0/3

## Notes
- [Any assumptions, risks, or open questions]
""",
            },
        ],
    },
    # ─── Skill Creator (mandatory default) ─────────
    {
        "name": "Skill Creator",
        "description": "Create new skills, modify and improve existing skills, and measure skill performance",
        "category": "development",
        "icon": "🛠️",
        "folder_name": "skill-creator",
        "is_default": True,
        "files": [],  # populated at runtime from skill_creator_content
    },
    # ─── Content Research Writer ──────────────────
    {
        "name": "Content Research Writer",
        "description": "Assists in writing high-quality content by conducting research, adding citations, improving hooks, iterating on outlines, and providing real-time section feedback",
        "category": "writing",
        "icon": "✍️",
        "folder_name": "content-research-writer",
        "files": [],  # populated at runtime
    },
    # ─── MCP Tool Installer (mandatory default) ──────────────
    {
        "name": "MCP Tool Installer",
        "description": "Guide users through discovering, configuring, and installing MCP tools directly in chat — no Settings page required",
        "category": "development",
        "icon": "🔌",
        "folder_name": "mcp-installer",
        "is_default": True,
        "files": [],  # populated at runtime from agent_template/skills/mcp-installer/SKILL.md
    },
    # ─── Market Data (trading agents) ──────────────
    {
        "name": "Market Data",
        "description": "Fetch stock quotes, OHLCV history, and fundamentals via a remote MCP server. Use when a trading agent needs price/financial data on US equities.",
        "category": "trading",
        "icon": "MD",
        "folder_name": "market-data",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Market Data
description: Stock quotes, OHLCV history, and fundamentals for US equities via Smithery MCP
---

# Market Data

## When to Use This Skill

Use when a trading agent needs:
- Real-time or historical price data on US equities (NYSE / NASDAQ)
- Financial statements (income, balance sheet, cash flow)
- Pre-computed technical indicators (RSI, MACD, Bollinger Bands, SMA, EMA, ADX, etc.)
- Quarterly EPS actuals, estimates, and surprises

**Scope (v1)**: US-listed equities only. **Not yet covered**: futures (CL=F, GC=F, ES=F), forex, crypto, international stocks. For these, fall back to `web-research`.

---

## Step-by-Step Protocol

### Step 1 — Check if Shibui Finance MCP is already installed

Look at your tool list. If you have `unlock_financial_analysis` and `stock_data_query` tools, skip to Step 3.

### Step 2 — Install via MCP_INSTALLER

Use the `mcp-installer` skill to install Shibui Finance (free, no API key, no per-call cost):

```
import_mcp_server(
  server_id="shibui/finance",
  config={"smithery_api_key": "<key>"}  # only on first import; reused after
)
```

If the user has not yet provided a Smithery API key, the `mcp-installer` skill explains how to register and obtain one.

### Step 3 — Activate the data session

The Shibui MCP requires a one-time activation per session before SQL queries work:

```
unlock_financial_analysis(...)
```

This returns an access token automatically managed by the MCP — you don't need to pass it in subsequent calls.

### Step 4 — Query data

The primary tool is `stock_data_query`, which takes natural-language prompts or SQL. Examples:

#### Get latest quote
```
stock_data_query(query="Get the most recent close price, daily change %, and volume for AAPL")
```

#### Get OHLCV history
```
stock_data_query(query="Daily OHLCV for TSLA over the past 90 trading days")
```

#### Get fundamentals
```
stock_data_query(query="Latest annual income statement and balance sheet for MSFT, with key ratios PE PB ROE")
```

#### Get pre-computed indicator
```
stock_data_query(query="14-day RSI for NVDA over the past 30 trading days")
```

#### Symbol screening
```
stock_data_query(query="US stocks with market cap > $10B, P/E < 20, and revenue growth > 15% YoY")
```

### Step 5 — Always cite as-of date

Every fetched number ships with the **as-of date** Shibui returns. Include it in your output to the user — never present stale data without timestamp context.

---

## Output Conventions

When you present market data to the user:

- Quote: `**AAPL** $192.45 +1.2% · Vol 48.2M · as of 2026-04-25 close`
- Indicator: `**TSLA RSI(14)** 68.4 (mildly overbought) · as of 2026-04-25`
- Fundamentals: bullet the headline numbers + 1-line interpretation, never dump raw tables

For OHLCV history with many rows, save to `workspace/<task>/<symbol>-history.csv` rather than rendering inline.

---

## What NOT to Do

- Do not present data without an as-of date — stale prices mislead
- Do not extrapolate from one query to another asset class (no futures, FX, crypto via this MCP)
- Do not exceed reasonable query depth — Shibui is free, but courtesy says don't run 100 SQL queries when 5 will do
- Do not fabricate numbers when the MCP can't answer — say "not available via this skill, falling back to web-research"

---

## Fallback (if Shibui MCP not available)

If the user can't / won't install the MCP, downgrade to `web-research`:
- Quotes: search "AAPL stock price now"
- History: search "AAPL daily chart 90 days"
- Fundamentals: search "AAPL 10-Q latest" or company IR page

Always tell the user "I'm using web search instead of structured market data — accuracy and timeliness will be lower."

---

## Asset Class Coverage (clawith roadmap)

| Asset class | v1 (this skill) | v2 plan |
|---|---|---|
| US equities | Yes (Shibui) | — |
| US ETFs | Partial (Shibui) | improve |
| Futures (CME) | No — use web-research | self-built yfinance MCP |
| Forex | No — use web-research | self-built MCP |
| Crypto | No — use web-research | dedicated crypto MCP |
| International stocks | No — use web-research | TBD |
""",
            },
        ],
    },
    # ─── Financial Calendar (trading agents) ──────────────
    {
        "name": "Financial Calendar",
        "description": "Look up earnings dates, FOMC meetings, CPI/NFP/GDP release dates, and other macro events that move markets. v1 uses structured web search; v2 will add dedicated MCP.",
        "category": "trading",
        "icon": "FC",
        "folder_name": "financial-calendar",
        "files": [
            {
                "path": "SKILL.md",
                "content": """---
name: Financial Calendar
description: Earnings calendar + macro events (FOMC, CPI, NFP, central banks) via structured web research
---

# Financial Calendar

## When to Use This Skill

Use when a trading agent needs:
- Upcoming earnings release dates for specific companies (or this week's reporters)
- Federal Reserve FOMC meeting dates and minutes release
- US economic data release schedule: CPI, PPI, NFP, GDP, retail sales, ISM, PCE
- Central bank decision dates (ECB, BoE, BoJ, PBoC)
- Geopolitical / fiscal events (debt ceiling, election dates, OPEC meetings)

---

## Implementation Note (v1)

clawith does **not** ship a dedicated calendar MCP server in v1. Smithery doesn't yet have a robust earnings/macro calendar tool. So this skill is a **structured wrapper around `web-research`** with curated query templates and source preferences. v2 will add a dedicated MCP backed by a free API (likely finnhub or trading-economics).

This means: every calendar query in v1 takes a web round-trip. Cache results in `memory/calendar_<month>.md` so the agent doesn't re-fetch the same Fed schedule three times in one week.

---

## Step-by-Step Protocol

### Step 1 — Check memory first

Before web searching, check `memory/calendar_<YYYY-MM>.md` for the current month. If you've already cached this month's events, use them and only web-search for what's missing.

### Step 2 — Run targeted query (use templates below)

#### Earnings calendar
```
web_research("AAPL next earnings date 2026 site:investor.apple.com OR site:nasdaq.com")
```

For a sector / market scan: `"this week earnings calendar US large cap"` then verify each name against IR sources.

#### FOMC schedule
```
web_research("Federal Reserve FOMC meeting schedule 2026 site:federalreserve.gov")
```

Authoritative source: federalreserve.gov/monetarypolicy/fomccalendars.htm — the calendar page directly.

#### US economic data calendar
```
web_research("BLS CPI release schedule 2026 site:bls.gov")
web_research("Bureau of Economic Analysis GDP release schedule 2026 site:bea.gov")
web_research("BLS Employment Situation NFP schedule 2026 site:bls.gov")
```

#### Central bank decisions
```
web_research("ECB Governing Council meeting schedule 2026 site:ecb.europa.eu")
web_research("Bank of England MPC schedule 2026 site:bankofengland.co.uk")
```

#### Aggregate calendar (lower fidelity, faster)
```
web_research("economic calendar this week high impact events")
```
Trusted aggregators: investing.com/economic-calendar, forexfactory.com/calendar, tradingeconomics.com/calendar

### Step 3 — Persist to memory

After each successful fetch, append to `memory/calendar_<YYYY-MM>.md`:

```markdown
## 2026-04 Calendar (last updated: 2026-04-27)

### FOMC
- 2026-04-30: rate decision + press conference (1 day, both PM EDT)
- 2026-06-12: rate decision

### US Data
- 2026-04-30: GDP advance Q1 (8:30am ET, BEA)
- 2026-05-02: NFP April (8:30am ET, BLS)
- 2026-05-13: CPI April (8:30am ET, BLS)

### Earnings (tracked tickers only)
- 2026-04-30 AMC: AAPL Q2 (consensus EPS $1.57)
- 2026-05-01 BMO: AMZN Q1 (consensus EPS $0.99)
```

### Step 4 — Cite source + confidence

Every event ships with:
- The source URL (preferring official: federalreserve.gov, bls.gov, bea.gov)
- A "confidence" tag: `[official]` for sources directly from the agency, `[aggregator]` for investing.com / forexfactory etc.

---

## Output Conventions

For a single event lookup:
```
**AAPL Q2 earnings** — 2026-04-30 AMC (after market close) · consensus EPS $1.57 [aggregator: nasdaq.com]
```

For a weekly briefing block:
```
**This week (2026-04-28 to 2026-05-02)**
- Tue 4/29 — JOLTS (10am, low impact)
- Wed 4/30 — **FOMC decision + presser** (2pm/2:30pm, very high impact)
- Wed 4/30 — GDP Q1 advance (8:30am, high impact)
- Wed 4/30 AMC — **AAPL Q2** (very high impact)
- Fri 5/2 — **NFP April** (8:30am, very high impact)
```

---

## What NOT to Do

- Do not invent dates when web-research returns ambiguous results — say "I couldn't pin down the exact date, here's the source page to check"
- Do not present aggregator data (investing.com etc.) as authoritative when the user is making a decision — escalate to the official agency source
- Do not over-cache — events get rescheduled. Re-verify FOMC and NFP dates within 7 days of the event
- Do not flag everything as "high impact" — distinguish **very high** (FOMC, NFP, CPI), **high** (GDP, retail sales, ISM, mega-cap earnings), **medium** (sector earnings, Fed speakers), **low** (weekly claims, regional Fed indices)

---

## v2 Roadmap

When clawith builds a dedicated finance-calendar MCP server, this skill will switch to direct API calls:

```
get_earnings_calendar(start="2026-04-28", end="2026-05-02")
get_macro_calendar(start="2026-04-28", end="2026-05-02", min_impact="high")
get_econ_event_consensus(event_id="us-cpi-2026-05")
```

Until then, structured web search is the contract.
""",
            },
        ],
    },
]


async def seed_skills():
    """Insert builtin skills if they don't exist."""
    from app.services.skill_creator_content import get_skill_creator_files
    from pathlib import Path as _Path

    _files_dir = _Path(__file__).parent / "skill_creator_files"
    _template_skills_dir = _Path(__file__).parent.parent.parent / "agent_template" / "skills"

    # Populate skill-creator files at runtime
    for s in BUILTIN_SKILLS:
        if s["folder_name"] == "skill-creator" and not s["files"]:
            s["files"] = get_skill_creator_files()
        elif s["folder_name"] == "content-research-writer" and not s["files"]:
            # Load from downloaded file
            crw_file = _files_dir / "content_research_writer__SKILL.md"
            if crw_file.exists():
                s["files"] = [{"path": "SKILL.md", "content": crw_file.read_text(encoding="utf-8")}]
        elif s["folder_name"] == "mcp-installer" and not s["files"]:
            mcp_file = _template_skills_dir / "mcp-installer" / "SKILL.md"
            if mcp_file.exists():
                s["files"] = [{"path": "SKILL.md", "content": mcp_file.read_text(encoding="utf-8")}]
            else:
                logger.warning("[SkillSeeder] mcp-installer/SKILL.md not found in agent_template/skills/")

    async with async_session() as db:
        for skill_data in BUILTIN_SKILLS:
            result = await db.execute(
                select(Skill).where(Skill.folder_name == skill_data["folder_name"])
            )
            existing = result.scalar_one_or_none()
            is_default = skill_data.get("is_default", False)
            if existing:
                # Update metadata
                existing.name = skill_data["name"]
                existing.description = skill_data["description"]
                existing.category = skill_data["category"]
                existing.icon = skill_data["icon"]
                existing.is_default = is_default
                # Sync files — add missing ones
                from sqlalchemy.orm import selectinload
                res2 = await db.execute(
                    select(Skill).where(Skill.id == existing.id).options(selectinload(Skill.files))
                )
                sk = res2.scalar_one()
                existing_paths = {f.path: f for f in sk.files}
                for f in skill_data["files"]:
                    if f["path"] in existing_paths:
                        # Update content if changed
                        existing_file = existing_paths[f["path"]]
                        if existing_file.content != f["content"]:
                            existing_file.content = f["content"]
                            logger.info(f"[SkillSeeder] Updated {f['path']} in {skill_data['name']}")
                    else:
                        db.add(SkillFile(skill_id=existing.id, path=f["path"], content=f["content"]))
                        logger.info(f"[SkillSeeder] Added file {f['path']} to {skill_data['name']}")
            else:
                skill = Skill(
                    name=skill_data["name"],
                    description=skill_data["description"],
                    category=skill_data["category"],
                    icon=skill_data["icon"],
                    folder_name=skill_data["folder_name"],
                    is_builtin=True,
                    is_default=is_default,
                )
                db.add(skill)
                await db.flush()
                for f in skill_data["files"]:
                    db.add(SkillFile(skill_id=skill.id, path=f["path"], content=f["content"]))
                logger.info(f"[SkillSeeder] Created skill: {skill_data['name']}")
        await db.commit()
        logger.info("[SkillSeeder] Skills seeded")


async def push_default_skills_to_existing_agents():
    """Deploy all is_default skills into the workspace of every existing agent that is missing them.
    
    Called at startup after seed_skills() so existing agents automatically receive new default skills
    like mcp-installer without requiring manual re-creation.
    """
    from pathlib import Path
    from app.models.agent import Agent
    from app.models.skill import Skill, SkillFile
    from sqlalchemy.orm import selectinload
    from app.services.agent_manager import agent_manager

    async with async_session() as db:
        # Load all is_default skills with their files
        default_skills_r = await db.execute(
            select(Skill).where(Skill.is_default == True).options(selectinload(Skill.files))
        )
        default_skills = default_skills_r.scalars().all()
        if not default_skills:
            return

        # Load all agents
        agents_r = await db.execute(select(Agent))
        agents = agents_r.scalars().all()

        pushed = 0
        updated = 0
        removed_legacy = 0
        for agent in agents:
            agent_dir = agent_manager._agent_dir(agent.id)
            skills_dir = agent_dir / "skills"
            legacy_mcp_file = skills_dir / "MCP_INSTALLER.md"
            if legacy_mcp_file.exists():
                try:
                    legacy_mcp_file.unlink()
                    removed_legacy += 1
                except OSError as exc:
                    logger.warning(f"[SkillSeeder] Failed to remove legacy MCP_INSTALLER.md for agent {agent.id}: {exc}")
            for skill in default_skills:
                if not skill.files:
                    continue
                skill_folder = skills_dir / skill.folder_name
                skill_folder.mkdir(parents=True, exist_ok=True)
                for sf in skill.files:
                    fp = (skill_folder / sf.path).resolve()
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    if fp.exists():
                        existing_content = fp.read_text(encoding="utf-8")
                        if existing_content == sf.content:
                            continue  # already up-to-date
                        fp.write_text(sf.content, encoding="utf-8")
                        updated += 1
                    else:
                        fp.write_text(sf.content, encoding="utf-8")
                        pushed += 1
                        logger.info(f"[SkillSeeder] Pushed '{skill.name}' to agent {agent.id}")

        if pushed or updated or removed_legacy:
            logger.info(
                f"[SkillSeeder] Pushed {pushed} new + {updated} updated skill files "
                f"to existing agents; removed {removed_legacy} legacy MCP installer files"
            )
        else:
            logger.info("[SkillSeeder] All existing agents already have up-to-date default skills")
