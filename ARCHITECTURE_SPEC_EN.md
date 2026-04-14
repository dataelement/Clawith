# Clawith Architecture Specification

This document provides a **comprehensive, deep-dive architectural guide** for developers attempting to understand, modify, or contribute code to Clawith (even as an AI Agent). By reading this specification, you will understand the data flows and operational backbone connecting the system's various modules.

---

## Module 1: System Macro-Architecture & Directory Structure

Clawith employs a fully decoupled frontend-backend architecture, interacting via REST APIs and WebSockets (long-lived connections).

### 1.1 Tech Stack
- **Backend**: Python (3.11+), FastAPI, SQLAlchemy 2.0 (AsyncSession), PostgreSQL (underlying DB), Redis (optional, for partial queue implementations), Loguru (logging system). Core LLM calls are uniformly encapsulated, supporting multiple providers (OpenAI, DeepSeek, Claude, etc.).
- **Frontend**: React 18, Vite, TypeScript, Zustand (global state flow), React Router v6. The UI is deeply customized with a **Linear-Style** aesthetic (dark mode, translucent glassmorphism, grid backgrounds, micro-animation interactions).
- **External Integrations**: Feishu/DingTalk/WeCom (bot Webhook access layer), Slack/Discord channels, and native support for the MCP (Model Context Protocol) plugin system.

### 1.2 Directory Map

To help future development quickly locate core files, here are the most critical code locations:

#### Backend (`/backend/app/`)
- `api/`: REST API routes and controllers.
  - `websocket.py`: **The most critical file!** Controls LLM streaming output, the Tool-calling Loop, and agent heartbeat mechanics.
  - `gateway.py`: **Edge Node Gateway**. Responsible for authentication, command dispatch (`poll`), and result return (`report`) for OpenClaw Agents (agents running locally on users' machines).
  - `triggers.py`: Frontend settings interfaces for the Aware Engine.
  - `feishu.py` / `discord_bot.py`: Message entry points (Webhooks/Gateways) for third-party IM software.
- `models/`: SQLAlchemy database ORM entities (see Module 2).
- `services/`: Core business logic layer.
  - `agent_tools.py`: **The Agent Tool Hub**. Contains core sandbox file operations (`write_file`, `read_file`), Agent A2A communication interception logic, and Feishu message dispatching logic.
  - `agent_context.py`: Assembles the LLM context (stitching together `soul.md`, system-level Prompts, etc.).

#### Frontend (`/frontend/src/`)
- `components/`: Reusable UI components.
- `pages/`: Complete view layers.
  - `AgentDetail.tsx`: The primary user-facing interface. Contains Agent settings, relationship chains, trigger panels, and the crucial **WebSocket real-time conversation rendering logic (A2A bubble alignment calculations also happen here)**.
  - `Plaza.tsx`: The discovery page for finding and "hiring" public Agents on the platform.
  - `Layout.tsx`: Global structural wrapper.
- `services/api.ts`: Encapsulates all outbound Axios requests to the backend.
- `stores/`: Zustand state repositories, such as `useAuthStore` (permission routing), responsible for seamless Client State management.
- `index.css`: The singular theme and atomic CSS file for the project, defining the color scale and Linear-Style UI across the entire site.

---

## Module 2: Core Data Models (Database Schema)

The fastest way to understand how Clawith operates is through its underlying relational data mapping (`backend/app/models/`). Here are the crucial table structures maintaining the ecosystem:

### 2.1 Tenant & Organization Security (Tenant Isolation)
All core entities contain a `tenant_id` to enforce physical isolation between different enterprises within the SaaS architecture.
- `User` (`user.py`): Real human users, possessing `super_admin` or standard permissions.
- `Tenant` (`tenant.py`): The tenant entity managing data isolation spaces.
- `OrgDepartment` & `OrgMember` (`org.py`): Clones of corporate organizational structures. The system actively syncs corporate directories from sources like Feishu and caches them here. When an Agent dispatches an outgoing message, it matches names against this table to retrieve the target's `feishu_open_id`.

### 2.2 Core Operational Entities (Agent, Session, Messages)
- **`Agent`** (`agent.py`): The "Digital Employees" of the platform.
  - Key fields: `agent_type` (`native` platform-hosted or `openclaw` externally registered), `heartbeat_enabled` (whether periodic sleep/wake is active), `autonomy_policy` (a dictionary of L1-L3 level autonomous operation authorizations).
- **`Participant`** (`participant.py`): **Crucial Table! The multi-party communication routing anchor.** Anyone capable of speaking on this platform receives a participant ID (with `type` distinguishing between `user` and `agent`). Its existence allows Agents not only to converse with humans but to initiate multi-party or A2A (Agent-to-Agent) group chats with other Agents.
- **`ChatSession`** (`chat_session.py`): Bundles multiple messages into entities with coherent context.
- **`ChatMessage`** (`audit.py`): Every LLM request/response, and even every tool invocation (`tool_call`), is fully snapshot and stored here.

### 2.3 M2M Collaboration & Discovery (Relationships & Plaza)
To prevent any two Agents in the system from arbitrarily communicating and spamming each other, the system enforces strict access control:
- **`AgentAgentRelationship`** (`org.py`): **The A2A (Agent-to-Agent) bidirectional relationship table**. Underlying cross-boundary file transfers (`send_file_to_agent` in `agent_tools.py`) are strictly prohibited unless a correlative record pointing from `agent_A` to `agent_B` (or vice versa) exists in this table.
- **`Plaza`** (`plaza.py`): Marketplace records. Once a public Digital Employee goes through the "hire" button flow, the system automatically establishes an `AgentRelationship` association between the operator and the Agent in the background, unlocking collaboration rights.

### 2.4 Pulse Engine & Edge Computing (Pulse & Gateway API)
- **`AgentTrigger`** (`trigger.py`): This table constitutes the heart of the **Aware Engine**. It records configurations such as `cron` routine wake-ups and `poll` API monitoring. Background daemon processes periodically sweep this table; once conditions are met, they bypass human input to inject system pulses directly into `websocket.py`, awakening the Agent.
- **`GatewayMessage`** (`gateway_message.py`): A pending queue exclusively allocated for `openclaw` types. Because remote machines are not in the Clawith server room, when the system has communications targeting that machine, it writes to this table. The remote Mac computer retrieves the information via the `poll` interface; after finishing its local LLM computations, it writes the result back through `report`, eventually triggering a WebSocket reverse notification to the frontend.

---

## Module 3: Native Core Engine

Clawith's most complex core business logic is centralized within **`backend/app/api/websocket.py`**. Understanding this file means understanding the entire thought and action flow of Native Agents.

### 3.1 Lightning-Fast Connection & On-Demand Auth
When a user opens a single Agent's page in the browser:
1. The frontend initiates a `ws://.../ws/chat/{agent_id}` request carrying a JWT Token.
2. The backend **immediately Accepts** the connection (for lightning-fast visual response) before performing asynchronous interception for Token and Agent permissions (expiration validation, etc., via `check_agent_access`, `is_agent_expired`).
3. If no existing `session_id` is matched, it automatically allocates one via UUID5 or fetches the last `ChatSession` between the user and that Agent, loading up to 20 history messages as context (`history_messages`). **Important Detail**: If the extracted history contains `role="tool_call"` records, the system restructures the JSON back into OpenAI's native concurrent Assistant+Tool_Calls format, preserving the LLM's coherent memory of its tool usage.

### 3.2 The Tool-Calling Loop
When a user sends a message (`[WS] Received:...`), the system does not simply invoke the LLM once and return text. Instead, it enters a deep polling circuit allowed up to **50 iterations**:
```python
# /backend/app/api/websocket.py: call_llm()
for round_i in range(_max_tool_rounds):
    # Dynamically inject tool limitation warnings
    # ...
    # Stream-call the LLM to obtain thought processes and Tool Calls
    response = await client.stream(...)
    
    # Exit condition evaluation
    if not response.tool_calls:
        # No tools called; final text answer generation complete. Exit Loop and return to frontend.
        return response.content

    # Execute Tool Call (Reflection call to executor)
    result = await execute_tool(tool_name, args, ...)
    # Reassemble results and proceed to the next round
```
- **Resource Protection Warning Mechanism**: To prevent the LLM from entering infinite loops by stubbornly retrying a failing tool, the system incorporates a **pre-terminal life-cycle warning**. At `_warn_threshold_80` (when 80% of round limits are exhausted), the system preemptively injects a `SystemMessage` telling the model *"You have used x/50 calls; please save your progress to focus.md immediately"*, preventing long-running tasks from dying abruptly.
- **Hard Parameter Validation**: For high-risk required-argument functions like `write_file` or `delete_file`, if the LLM (like Claude) issues a tool call declaration with empty `args`, the system does not execute and throw an environment error. Instead, it **intercepts execution** and returns an error message within the context urging the model to correct it immediately, dramatically improving fault tolerance.

### 3.3 Token Deduction & Long Text Estimation
During streaming output, providers (like certain open-source frameworks) might not return `usage` token counts. `_accumulated_tokens` will trigger `estimate_tokens_from_chars()`, replenishing deductions via Chinese/English string estimation ratios to ensure the user's daily/monthly Agent Quota is accurately billed at all times.

---

## Module 4: Edge Computing & Ecosystem Extension (OpenClaw Gateway)

To allow the Clawith ecosystem to embrace intelligent agents running on local laptops, Raspberry Pis, or even other proprietary environments, the system introduces the `OpenClaw` Edge Node Protocol.

### 4.1 X-Api-Key Gateway Auth
- Local devices calling the gateway (defined in **`backend/app/api/gateway.py`**) do not use JWT User Tokens; rather, they use the exclusive `X-Api-Key` issued when the Edge Agent was created.
- Upon entry, the system undergoes dual verification: supporting plaintext (new version) or `hashlib.sha256` (legacy compatibility) reverse lookups against the `agents` table to verify legitimacy.

### 4.2 Poll-Report-Send Messenger Mechanism
An OpenClaw Node is essentially a local daemon process executing an infinite loop script forever:
1. **Poll**: `/gateway/poll` endpoint. The local Agent interrogates this endpoint every few seconds asking if there are any `GatewayMessage`s targeting its `id` with `status='pending'`. If so, it marks them as `delivered` and takes away the packaged context history.
2. **Local Computation**: The OpenClaw Node, detached from Clawith computing power, can assemble Prompts locally and offload them to an Ollama instance or third-party LLM running on the local machine.
3. **Report**: `/gateway/report` endpoint. After local results are computed, they are sent to this endpoint bearing the initial `message_id` and the `result`. Upon receipt, the Gateway:
   - Updates the initial `GatewayMessage` status to `completed`.
   - **Core Flow**: Morphs it into a `ChatMessage(role='assistant')` and forcibly shoves it into the user's `ChatSession` database.
   - Invokes the WebSocket Manager to trigger an `await manager.send_message({"type": "done", "content": body.result})` forcefully streaming to the end user gazing at the online interface!
4. **Send (Proactive Communication)**: `/gateway/send-message`. When a local Agent suddenly wants to reach a headquarters person or another Agent (i.e. an A2A scenario). This interface detects whether `body.target` is Human (triggering Feishu dispatch) or Native Agent (triggering a massively long asynchronous LLM pushstream labeled `_send_to_agent_background`).

---

## Module 5: Aware & Pulse Engine

Defined within `backend/app/models/trigger.py` and `backend/app/api/triggers.py` lies the core that liberates Agents from "passive dialogue boxes" into "autonomous workers": the **Pulse Engine**.

### 5.1 Trigger Core Structure (`AgentTrigger`)
Each Agent can set up a series of triggers targeting itself (rendered identically in the frontend Aware page panel):
- `type`: `cron` (Cron scheduling), `interval` (fixed interval scanning), `poll` (pulling and comparing against external APIs), `on_message` (letters from specific individuals).
- `config`: Houses JSON expressions customized by Type (e.g., croniter's `'0 9 * * 1-5'`).
- `cooldown_seconds`: Anti-bounce cooldown periods avoiding polling storms.

### 5.2 How Does the Trigger Chain Flow?
1. The backend runs a periodically ticking Scheduler Task (Pulse Emitter).
2. The system sweeps tables locating `AgentTrigger`s hitting execution time.
3. The system fabricates a `SystemMessage` masquerading as human-triggered input. Example: `[System Trigger]: Your set time trigger "Daily Data Report" has expired. Please execute the initially designated goal immediately.`
4. It forcefully pushes this request into the corresponding `agent_id` WebSocket flow / or generates a new `ChatSession`, prompting the native core execution engine (`call_llm`) to spin up and invoke tools to generate the report.

---

## Module 6: Multi-Agent A2A Collaboration

A2A (Agent-to-Agent) communication is Clawith's trump card logic moat. By simulating peer-to-peer relationships, it allows models to toss requirements back and forth as if they were working inside human chatting software.

### 6.1 Strict Border Control for A2A (Relationship Check)
All foundational A2A interactive capabilities are centralized in `backend/app/services/agent_tools.py`:
- `send_message_to_agent(target_name, message)`
- `send_file_to_agent(target_name, filename, explanation_message)`

**Interception Logic:**
```python
# When an Agent invokes send_message_to_agent:
# 1. Fuzzy search target_name (within identical tenant_id)
# 2. Block evaluation:
rel_forward = select(AgentAgentRelationship).where(agent_id=src, target=dst)
rel_backward = select(AgentAgentRelationship).where(agent_id=dst, target=src)
if not (rel_forward or rel_backward):
    # Respond to LLM reporting: "Permission restricted: you are not on the same team/authorization not acquired"
```
This meticulous design prevents scenarios utilizing Prompt Injection to instruct an Agent to randomly cast nets and scan other confidential exclusive Agents within the company.

### 6.2 Frontend A2A Bubble Rendering Revolution
In typical ChatUIs, `role: "user"` usually warrants blue backgrounds aligned right, while `role: "assistant"` yields grey backgrounds aligned left. But what if two Agents (A and B) are conversing?
- The `Participant` model single-handedly redeems this paradox:
- Within `frontend/src/pages/AgentDetail.tsx`, an exclusive `isSender` conditional function operates.
- If we are viewing Agent A's history: as long as `message.participant_id` belongs to A itself, render it on the right side! This holds regardless of whether the content is recorded in the DB as `role="assistant"` (because it is the responding end commanded by others) or `role="user"` (because it proactively initiated `_send_to_agent_background` awakening the other's conversation).
This guarantees that within management scopes, **"I" always speak on the right side; "The opposite end" is perpetually on the left.**

---

## Module 7: Omni-Channel Integration

Clawith architecturally refuses to solidify frontend chatting as the foremost priority. The Web UI is merely one of its multitudinous "monitors". The system devised a generalized `ChannelConfig` to uniformly consolidate messages flowing from terminal IM software.

### 7.1 Protocol Transformation: Webhook to ChatMessage
Taking Feishu as an example (`backend/app/api/feishu.py`):
1. **Event Recept**: Receives encrypted Webhook POST requests (`im.message.receive_v1`) from the Feishu Open Platform.
2. **Identity Mapping**: Utilizing the incoming `open_id`, queries the `OrgMember` table to reverse search the correlated `User` record bound to that employee within Clawith.
3. **Dispatch to Endpoint**: Generates a native `ChatMessage(role='user', source_channel='feishu')` outfitted with standardized context. It is then dumped into the underlying LLM execution pool to be computed exactly as if it came from the Web interface.
4. **Packet Wrapping for Return**: After model computations conclude realizing text or Markdown, underlying tools (`send_feishu_message`) or lifecycle hooks render it into rich text in reverse and send it back to Feishu.

This paradigm ensures that regardless of Slack, Discord, or Personal WeChat, backend large model execution logic demands absolutely zero modifications, altogether reusing the tenets of **Module 3**.

---

## Module 8: Frontend Architecture & Real-Time Flow

As a high-tier collaborative whiteboard, the Frontend (`frontend/`) relentlessly pursues rendering efficiency and real-time feedback in engineering management.

### 8.1 Core Technology Nuances
- **Build & UI Frameworks**: `Vite` buildup engine + `React 18`. In terms of aesthetic design, structurally adheres to **Linear-Style** beauty (aberrantly dark backdrops, hairline contours, translucent gaussian blurred frosted glass panels, Lucide vector icons). Primary constraints are globally defined inside `index.css` atomic variables.
- **Global State Control**: Abandons heavy-duty frameworks like Redux in favor of the lightweight `Zustand` to script Hook Stores (rooted at `frontend/src/stores/`).
  - Example: `useAuthStore` manages JWT state persistence, User Authority, Multilingual Preference Locales (i18n).

### 8.2 The Typewriter Rendering Challenge Over WebSockets (`AgentDetail.tsx`)
At the `AgentDetail.tsx` page, the system battles extreme rendering pressure: outputs from the large model might be split into hundreds of minuscule Tokens. How do we achieve 60-FPS butter-smooth typewriter effects without throttling?
1. **Data Slice Interception**: 
   `WebSocket` events flooding from backend encompass multiple typologies: `chunk` (Textography), `tool_call` (Tools executing image-text), `think` (Deep thinking trajectories).
2. **Incremental Referencing (Refs vs State)**: 
   The system forbids indiscriminately stuffing every single `chunk` cleanly into React's `useState`, avoiding triggering hundreds and thousands of comprehensive global repaints. Instead, it maintains a live queue pertaining to the active message currently generating, administering localized state assemblages or downloading renders via Throttle mechanisms.
3. **Markdown Rich-Text Cleansing**:
   Employs `react-markdown` executing ultimate filtration presentations. Imposing Copy buttons over code snippet blocks; demonstrating imagified placeholder renders mapped against localized hyperlinks.

---
**[The End] Architecture Document Completion.**

> Clawith Architecture Document Engine Edition. 
> This document currently engulfs all core logic within the system. Whether pivoting underlying engine circuits, appending new Database tables, or drafting novel outbound calling channel pipelines, please persistently harbor reverence toward the **Workspace/Tenant isolation barriers** alongside **Relationship object strictly bound** constraints.
