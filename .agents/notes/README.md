# Agent Notes

An Agent Note records a durable engineering decision: the problem it addresses, the chosen decision, the alternatives actually considered, the consequences, and the evidence that verifies the result.

Agent Notes do not replace product requirements, current architecture documentation, implementation plans, test reports, incident records, or commit history. They own why an engineering decision exists and what was deliberately given up.

## Path and classification

Every Agent Note uses this path:

```text
{lifecycle}/{class}/yyyy-mm-dd-topic.md
```

The lifecycle is one of:

- `proposed` — the decision is under discussion or implementation and has not become current repository behavior.
- `implemented` — the decision has shipped and the Note describes current repository behavior in the present tense.
- `rejected` — the proposal was declined and remains useful because it prevents a plausible repeated mistake.
- `archived` — a frozen historical snapshot of an implemented decision that no longer needs current-fact maintenance. Archived Notes are not current authority.

The class is one of:

- `architecture` — source structure, ownership, boundaries, runtime vocabulary, or durable execution semantics.
- `bug-fix` — a defect whose cause, contract, or prevention is likely to be revisited.
- `feature` — a product or platform capability decision.
- `process` — development, documentation, review, release, or operational workflow.
- `simplification` — removal, consolidation, or reduction of owned complexity.
- `testing` — test strategy, evidence boundaries, harnesses, or required gates.

## When to write one

A change is non-trivial when it alters observable behavior, architecture, ownership, a shared contract, Runtime semantics, lifecycle, persistence, configuration, compatibility, security, permissions, testing strategy, CI, release behavior, or another engineering decision a maintainer may reasonably revisit.

Update the Agent Note that already owns the decision. Create a new Note only when no current Note owns it or when the decision itself changes. Purely mechanical or strictly local changes with no behavioral, contractual, architectural, or process effect are exempt.

Agent Note work begins when the decision is discovered, not at Push time. The pre-push workflow is the final enforcement point: it inspects the complete outgoing change and blocks the Push when a required owning Note is missing or contradicts the code or commit history.

## Required format

Every active Agent Note begins with:

```markdown
# Agent Note: <title>

Status: proposed | implemented | rejected — <reason>
```

Every Note opens with `## Problem` and includes `## Alternatives considered`. Lifecycle-specific content follows:

- `proposed`: `## Proposal`, then plans, acceptance criteria, risks, and open questions only when they materially help decide or implement the proposal.
- `implemented`: `## Decision`, `## Consequences`, and the relevant verification evidence or named gaps. It describes current behavior, not a migration diary.
- `rejected`: retain the proposal and alternatives; put the rejection verdict on the `Status:` line.
- `archived`: retain `Status: implemented`, add `Archived: YYYY-MM-DD`, and freeze the file permanently.

Alternatives are recorded, never invented. State what each real alternative would have changed and why it lost.

## Updating and superseding decisions

Keep an implemented Note's paths, names, defaults, and mechanisms aligned with the code when the decision itself has not changed. Do not append change history; rewrite stale current facts in place.

Do not edit an existing Note into the opposite decision. Create a new proposed or implemented Note, cross-link both decisions, and retain the old rationale. Archive an implemented Note only when it is no longer useful as current guidance.

Code, the owning Agent Note, and commit history must agree. Code implements the decision, the Note owns durable rationale and the current contract, and the commit records the intent, scope, and verification of the concrete change.
