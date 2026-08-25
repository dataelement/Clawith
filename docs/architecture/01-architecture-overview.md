# 01 - Clawith Architecture Overview

> Status: Current implementation baseline.
> Scope: System topology, boundary principles, and core components.

---

## 1. System Purpose & Topology

Clawith is a multi-tenant enterprise Agent application platform. It exposes direct chat, group chat, tasks, triggers, heartbeats, and Agent-to-Agent entry points while executing all durable Agent logic through a shared, isolated runtime.

```text
Web / Channel / Task / Trigger / Heartbeat / A2A
                      │
                      ▼
            RuntimeCommandIntake
       AgentRun + AgentRunCommand (DB)
                      │
                      ▼
               Command Worker
          thread-serialized execution
                      │
                      ▼
            Clawith Agent Kernel
     (context -> model -> tool -> verify)
                      │
                      ▼
                 LangGraph
        PostgreSQL Durable Checkpoint
```

---

## 2. Separation of Four Kinds of Facts

To maintain durable execution stability, Clawith strictly decouples four distinct concerns:

| Fact Type | Owner | Description |
|---|---|---|
| **Product Records** | Product DB Tables | Tenants, Users, Agents, Sessions, Groups, Permissions. |
| **Accepted Command Inbox** | `agent_run_commands` Table | Accepted `start`, `resume`, and `cancel` inputs. |
| **Execution Lifecycle** | LangGraph Checkpoint | PostgreSQL durable checkpoint state. |
| **User Delivery** | Product Reconciler | Idempotent message delivery & external notifications. |

> **INVARIANT (C1)**: Product projections must **NEVER** become a second Agent execution state machine. API endpoints and product services must not mutate checkpoint lifecycle fields directly.
