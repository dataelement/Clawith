# Agent Note: Record product startup and questions in Run transactions

Status: implemented — Run exposes transactional startup and Waiting consumer ports; product routing is composed separately.

## Problem

A fast Main can execute before a separately written product association becomes visible. Recording a human question independently from Waiting can likewise publish a question whose wait rolled back, or lose the question for a committed wait.

## Decision

The [G006 product-input contract](../../../../specs/backend-product-inputs.md) defines `StartConsumer.record_started(transaction, *, run)` and `WaitingConsumer.record_waiting(transaction, *, run, waiting)`. The caller owns the transaction; consumers write only their own product facts and never authorize Run transitions.

Run calls StartConsumer only for the newly inserted Main, after Snapshot and initial History exist in the same transaction. A duplicate source and a Child do not invoke it. Failure rolls back startup, including the product association; Runtime retains its existing admission reconciliation and schedules only after commit. Without a consumer, ordinary Main startup retains its four statements and one transaction.

Run calls WaitingConsumer after a new Main Waiting fact with a nonempty human question is accepted. It shares the Tool-result settlement transaction. Unseen-input suppression, duplicate waits, empty task-result waits and Child questions do not invoke it. A callback failure rolls back both the Tool settlement and wait; explicit retained-settlement retry repeats persistence and the product callback, not the Tool or Model operation.

`RunService.lock_main` supplies the existing Run-before-product lock order. It rejects Children and returns the current Main status without changing lifecycle or refreshing permissions. Product owners inspect that status and lock their own facts afterward in the same transaction.

`verify_main_tool_origin` additionally checks a Running Main, matching call identity/name in the latest successful Model Step and the immutable Snapshot grant. It returns the locked Run view for product ownership checks. Task uses the same verification rather than maintaining a separate correlation rule. Mere text naming a Tool or a caller-supplied step identifier does not authorize a product message.

Product orchestration applies returned transition facts through `RunRuntime.post_commit` after its transaction commits. A work-control Tool can cancel its own Main, so Tool return and terminal commit are not assumed to have a fixed order. Settlement reads persisted terminal status under the same family lock used for the following write and discards a late result instead of attempting another terminal write or retaining an impossible retry. A concurrent cancellation cannot slip between that read and settlement. This does not undo an external effect or mark an uncertain operation successful; it prevents replay after cancellation.

`has_input_reference` checks exact references only in supported-version initial and related input History for the scoped Run. Model text, Tool Results and unknown-version payloads do not grant attachment access. The query tests existence without loading unrelated History bodies; attachment owners still decide source and delegation authorization.

## Alternatives considered

Writing associations and questions after the Run transaction leaves a gap between execution facts and product facts. Making OutcomeConsumer publish replies conflates terminal execution with explicit messages and human-input requests. Neither approach satisfies the approved contract.

## Consequences

Consumer failures can prevent startup or settlement from committing. They cannot expose partially committed product links or questions. Composition owns routing by trusted Run source; no product tables, callback registry, lifecycle state or new transaction manager is added to Run.

## Verification

Real PostgreSQL tests exercise startup rollback, duplicate suppression, Child exclusion, unseen-input suppression, Waiting rollback, callback-before-fast-Model execution and retained-settlement retry without Tool replay. Lock tests observe another transaction blocked until the Main lock is released. Session Tool tests cover self-cancellation and an external cancellation while a Tool returns late, preserving terminal state with no retry memo or repeated Tool call. These tests do not establish complete Group, A2A or transport behavior.
