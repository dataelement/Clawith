# Contract: Repair Budget and Execution Lifecycle

## Tool Repair Episode

- `same_fingerprint_failures` reaches 10: pause immediately after recording the 10th failure; do not invoke model step 11 for that loop.
- `total_failures` reaches 10 for the same Tool episode: pause immediately; do not invoke the next model step.
- Generic Tool protocol repair, `write_file` protocol repair, and safe-read replay retain their current independent counters but each uses a limit of 10; counter unification is deferred.
- Changing fingerprint resets only the consecutive counter.
- Success of the same Tool, new Run, or explicit user correction resets the Tool episode.
- Success of another Tool does not reset it.

Global `model_turn_limit`, Provider transport retry, Command retry, Receipt safe-read attempt and Verifier episode are independent budgets with independent stop reasons.

## Verifier Episode

Verifier attempts belong to a fingerprinted current issue. A passing verification closes the episode. A materially new issue begins at zero; historical repair attempts do not consume its budget.

## Deadline / Cancel / Lease

| Control | Meaning | Must not imply |
|---|---|---|
| Operation deadline | Maximum wait for one handler/provider operation | Receipt ownership loss or proof no write occurred |
| Durable cancel | User/platform intent to stop the Run | Automatic rollback of an external write |
| Receipt lease | Which Worker may execute/settle the Receipt | Handler completion deadline |

Rules:

1. deadline precedence is explicit call value, then Tool policy default, capped by Tool policy maximum;
2. cancel propagates to supported subprocess/network/SDK operations and otherwise stops waiting with capability telemetry;
3. long Handler renews lease while owning it and fences before side effect/settlement;
4. lease loss prevents stale owner settlement;
5. deadline/cancel/disconnect after a possible write yields unknown/reconcile unless a stable provider/business receipt proves outcome;
6. unknown write cannot be automatically replayed by model, Command retry or Worker restart.
