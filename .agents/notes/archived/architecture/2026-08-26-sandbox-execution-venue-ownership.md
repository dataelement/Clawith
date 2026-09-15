# Agent Note: Sandbox Execution Venue Ownership

Status: implemented — each code execution resolves one Sandbox backend and never retries through a separate legacy subprocess path.

Archived: 2026-09-03

## Problem

`execute_code` selects a backend with specific isolation, network, timeout, output, and cancellation semantics. A separate fallback executor could repeat code after an unknown outcome or run it under a policy the caller did not select. Deterministic per-Agent configuration errors must also fail before Session workspace resources are acquired, while result-formatting errors must not obscure the backend's already known execution result.

## Decision

The workspace entry resolves and validates the effective `SandboxConfig` and execution venue exactly once for both `execute_code` and `execute_code_e2b`, before acquiring a Session execution lease, materializing a workspace, flushing output, or dispatching code. The executor consumes that resolved configuration and does not read the configuration store again. Invalid values and configuration-store exceptions return a deterministic typed configuration failure without starting execution or workspace lifecycle work.

The resolved Sandbox backend is the sole execution venue. Pre-dispatch configuration or startup failures return a typed failure. Once `backend.execute` starts, an exception that leaves side effects unprovable returns `sandbox_execution_outcome_unknown`; it never starts a second backend. The platform may still explicitly resolve `execute_code` to the Sandbox subsystem's `subprocess` backend, including its configured isolation policy, but `agent_tools` has no independent legacy subprocess executor.

Result formatting is post-execution presentation, not execution evidence. If a backend formatter raises, the Tool outcome retains the `ExecutionResult` success and exit-code classification, emits a bounded fallback summary, records the formatter exception type in `metadata.formatter_error`, and logs a warning.

## Alternatives considered

**Keep the legacy subprocess executor as an emergency fallback.** Rejected because it changes the selected execution venue and may repeat code whose first outcome is unknown.

**Resolve per-Agent Sandbox configuration after Session workspace setup.** Rejected because deterministic configuration errors must fail before leases, materialization, or dispatch create lifecycle work.

**Treat formatter failure as execution failure or unknown execution.** Rejected because formatting runs after the backend has returned primary execution evidence and does not change whether code ran or its exit status.

## Consequences

An unavailable or invalid configured backend is visible instead of silently running code under a different policy. Timeout, output capture, process cleanup, and cancellation have one owner in the selected Sandbox backend rather than a duplicate `agent_tools` implementation. Deployments that intentionally use local execution continue through the configured Sandbox `subprocess` backend. Formatter failures may reduce summary detail, but callers retain the primary status, exit-code-derived classification, and explicit formatter evidence.

## Verification

`backend/tests/test_sandbox_execution_policy.py` covers configured-backend failure without venue switching, invalid configuration and configuration-store failure at the real workspace entry before lease/materialization/flush/dispatch, missing or invalid E2B configuration at the same boundary, post-dispatch unknown outcomes, and formatter failure with preserved result status and metadata. The typed E2B and content-outcome tests cover explicit cloud venue selection and the no-reexecution rule. Backend Ruff formatting, Ruff checks, Pyright, and the focused Sandbox tests are the required evidence for this boundary.
