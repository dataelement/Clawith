# G005 Core Runtime contract preflight

Verdict: APPROVE for binding the Run and Context implementation contract. This is design review, not lifecycle implementation or E2E acceptance.

## Approved scope

The user explicitly chose service-wide interruption of all unfinished Main/Subagent Runs, including Waiting. Independent code and architecture review confirmed that ordinary per-Run cancellation can remain distinct, while shutdown/startup family cleanup uses Interrupted without waking Parent or replaying execution.

The implementation contract preserves one Run/History authority, immutable Snapshot, disposable Context projection, normal in-service resume, Parent-first transactions, same-database OutcomeConsumer handoff and bounded ephemeral scheduling. Model-Step read-through positions are History facts, not compaction coverage. Waiting retains lightweight admission accounting so existing resume is never rejected for a new permit.

## Review corrections applied

- Runner, target architecture, capacity, Tool dispatch and Main/Subagent Notes no longer promise executable restart continuation for Waiting Runs.
- Service-wide Interrupted settlement is distinguished from ordinary Parent-to-Child Cancelled propagation.
- Failed initial enqueue releases committed capacity only after Interrupted settlement commits; settlement failure retains the permit without replaying Model or Tool.
- Data remains inspectable across upgrades even when execution is terminated by the chosen operational policy.

## Evidence boundary

Independent architecture review: CLEAR. Independent implementation/security review: APPROVE. Their linked-Note corrections are incorporated. The existing pure ready queue is separately tested and committed; it does not establish Run/Context lifecycle, actual dispatch, core E2E or load acceptance.

The new contract inherits the existing reviewed Audit and G004 amendments. Original Foundation approval artifacts and receipts remain preserved; Run/Context bindings append amendment receipts rather than rewriting history. Deferred GitHub/ClawHub import drafts and frontend prototypes are excluded.
