# 02 - Backend & Runtime Boundary Isolation

> Status: Current implementation baseline.
> Scope: Execution intake, Command Worker, and LangGraph Checkpoint boundaries.

---

## 1. API & Channel Adapters (`backend/app/api/`)

HTTP, WebSocket, webhook, and channel adapters perform authentication, tenant authorization, payload validation, and request persistence.

**Rules**:
- Adapters must convert valid requests into durable commands via `RuntimeCommandIntake`.
- Adapters MUST NOT invoke graph nodes directly, advance graph node execution status, or modify checkpoint tables.

---

## 2. Runtime Command Intake (`backend/app/services/agent_runtime/`)

Shared execution boundary that atomically records:
- The immutable `AgentRun` registry identity.
- A durable `AgentRunCommand` for `start`, `resume`, or `cancel`.
- Stable idempotency and correlation facts.

---

## 3. Command Worker (`command_worker.py`)

The Command Worker claims durable commands from `agent_run_commands`, serializes execution per thread, invokes the LangGraph topology, and handles post-checkpoint reconciliation.

- **Checkpoints are Authoritative**: A committed checkpoint remains authoritative even if product synchronization fails.
- **Reconciliation is Idempotent**: Side-effect synchronization and notification delivery are retryable and idempotent.
