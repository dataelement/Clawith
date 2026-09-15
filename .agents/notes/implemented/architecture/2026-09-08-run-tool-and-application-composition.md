# Agent Note: Run Tool and application composition

Status: implemented — native Run Tool adapters and explicit provisioning are available; application integration follows the Run execution contract.

## Problem

Task acceptance must not occupy a Tool call until Child completion. Main and Subagent capabilities need different executable views without introducing Task records or allowing model JSON to select another execution scope. Application shutdown must drain these consumers before their database, HTTP and storage resources.

## Decision

Application composition injects the actual Model, Workspace, MCP and Run services into Tool adapters. Explicit provisioning registers code-owned Task, Todo, Need Input and wait-for-tasks definitions and Agent grants; execution never silently grants missing capabilities. Main can delegate, inspect and answer its own Children. Subagent exposes Todo instead of Task and cannot recurse. The executor checks role, definition and trusted Run scope in addition to Tool exposure filtering.

Task delegates a work description and returns acceptance immediately. Its correlation is the Parent's committed Model-Step/Tool-Call identity, not a Task ID. Need Input and wait-for-tasks produce bounded Tool markers; the Run owner applies Waiting only after recording the batch results. An empty Child-wait request with no active Child cannot leave Main waiting for a nonexistent producer. Todo replacement is a bounded planning result retained in History and re-injected by Context, not a completion gate.

Task inspection reads one bounded fragment of serialized Child History at a time through Run's public owner boundary. Returned sequence and character offsets make a large record fully reachable without loading a complete 16 MiB record into a 256 KiB Tool result. The Parent/Child relationship, Tenant, record kind/version and cursor bounds remain checked.

Tool batches use a shared application semaphore so separate per-Run registries cannot multiply the configured concurrency. The summary adapter uses the fixed Model's one-shot summary port, keeps its reasoning/output allowance, and supplies the desired summary text size separately. Context validates the adopted result against its actual remaining budget. Summary generation never creates a fictitious Run or overwrites execution continuation.

Application integration must create Runtime after its execution dependencies, publish it only after startup cleanup, and close it before those dependencies. The controlling operation stays within the single factory; no product routes, legacy fallback or distributed scheduler are introduced by these adapters.

## Alternatives considered

Awaiting Child completion inside Task would keep Tool execution occupied and obstruct Main conversation. Returning an oversized whole Child History page would make large results permanently unreadable. Giving each Run an unrelated semaphore would exceed the platform Tool limit. Reducing Provider output to the desired summary text length can contradict a configured thinking budget.

## Consequences

Product Session/A2A/Group/Trigger/Heartbeat interfaces remain G006. Deferred GitHub/ClawHub import drafts and frontend work are excluded from this implementation. The application E2E uses a fixture product owner; it does not claim those product integrations already exist.

## Verification

The MCP-image application fixture uses actual Market registration, MCP discovery/install, Tool search/execution, Run History, Context counting and Model physical encoding. Only external Provider/MCP HTTP peers are controlled. It verifies one image in the final request, invocation of the input-token endpoint, retained raw Tool Result and metadata, one MCP call and a committed Completed product outcome. The product owner remains a fixture rather than G006 Session or API wiring.

Application-owned Context statistics consume assembly telemetry without content or identity labels. Counters have fixed cardinality, unknown cleared-Tool Token amounts remain explicitly unknown, and returned snapshots cannot mutate the collector. Counter I/O duration is separate from local Context assembly. These observations neither govern execution nor establish full-platform latency qualification.

Actual executor tests check role and scope denial, Task acceptance/resume, bounded Todo, invalid arguments and cancellation. Application E2E verifies a Model-requested Workspace write and a committed fixture-owner final output, not merely a model's success claim. Summary integration tests preserve the 8192-context/2048-output regression and a thinking-budget case, including malformed or oversized summaries. Composition tests observe Runtime/Audit workers exiting before database disposal.
