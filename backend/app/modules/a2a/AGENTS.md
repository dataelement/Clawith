# A2A owner

`temp_files.py` owns the existing request's bounded temporary-file manifest, pending publication, frozen returns, source save receipts and cleanup claims. Target/Child operations never gain the source Workspace; source saves require the current delivery Main and its captured output. Physical CAS uses an owner-defined port without transactions across I/O. Unsaved returns survive either Agent's termination. See [temporary files](../../../../.agents/notes/implemented/architecture/2026-09-09-a2a-temporary-files.md).

`public.py` owns request acceptance, independent target association and pending source delivery. All writes use the caller's TransactionContext; source/target Run facts remain behind Run's public API. Source Model Tool correlation is validated before a new request and deduplicates by step/call identity.

Target callbacks lock target Run then the A2A request, never the source Run. Source delivery is a separate transaction: append through Run, then acknowledge the exact delivery key. An old acknowledgement cannot consume a newer outcome. Explicit answers acquire both independent Main locks in UUID order before the request lock. No transaction holds an external operation.

Notify has no source result delivery. Consult and task_delegate return immediate acceptance and retain target outcomes for asynchronous delivery. A terminal source does not cancel or resume its independent target. No implicit Workspace or Credential transfer belongs in the payload. The [product contract](../../../../specs/backend-product-inputs.md) controls this slice.

Original source attribution never changes. Explicit same-Agent, same-Session or same-Group-topic takeover may replace the delivery recipient only after that recipient is terminal. Inspecting another request requires the same public product association checks. Wait Tools return a marker after owner validation; application composition maps it to Runner's generic related-input wait after Tool settlement. Request tracking remains bounded current-process bookkeeping, not automatic recovery. See [A2A delivery](../../../../.agents/notes/implemented/architecture/2026-09-09-a2a-request-and-result-delivery.md).

`input_visibility` traces at most sixteen immutable source associations and returns only the original visibility subject and optional Group topic. It does not grant source Workspace access, read Secret bytes, change receiver output or infer public visibility on invalid ancestry.

Answer attachment grants are source-authorized related-input facts for the exact A2A request. Target reads must match both `a2a_answer` kind and request owner when consulting Run History; generic reference presence is not delegation, and the original accepted input stays immutable.
