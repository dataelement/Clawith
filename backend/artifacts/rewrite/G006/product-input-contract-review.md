# G006 product-input contract preflight

Contract: `specs/backend-product-inputs.md`.

The user authorized completing G006 and stopping before G007 after confirming the Main-only message outlet, fixed 24-hour human login, Goal stop on Failed, and no recovery of interrupted work. Existing input, authorization, Run and independent-product boundaries remain in force.

Independent architecture review `g005_final_arch_audit`: APPROVE / CLEAR for implementation binding of Session, Run, Tool, minimal Auth, A2A, Group, Trigger, Heartbeat and Channel. Minimal existing-login transport does not approve the separate G007 registration/recovery/SSO product scope. Channel completion requires retained provider coverage rather than a generic adapter alone.

Independent code review `g005_final_code_audit`: APPROVE for preflight. Start and Waiting callbacks share the authoritative Run transaction, message acceptance survives loss of subsequent Tool Result, and source correlation plus Run-before-product lock ordering preserve ownership. Its recommendations are incorporated: A2A source delivery occurs after target settlement in a separate transaction, and Channel must persist uncertain send outcomes without blind replay.

Existing Session and product schemas can be amended within the pre-migration target to realize these contracts without new Task/Goal objects or recovery leases. Original contracts and receipts remain unchanged. This review authorizes implementation scope only; new source, real PostgreSQL/HTTP/WebSocket tests, independent implementation review and stage evidence remain required.
