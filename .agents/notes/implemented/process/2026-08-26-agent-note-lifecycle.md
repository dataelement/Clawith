# Agent Note: Agent Note lifecycle and decision alignment

Status: implemented

## Problem

Clawith requires non-trivial changes to preserve their engineering rationale, but the repository had no durable location, lifecycle, classification, or format for those decisions. Commit messages alone describe one concrete change and ordinary documentation describes current facts, so neither reliably preserves the alternatives and consequences that future maintainers may revisit.

## Decision

Engineering decisions live under `.agents/notes/{lifecycle}/{class}/` using the rules in [the Agent Notes README](../../README.md). The lifecycle is proposed, implemented, rejected, or archived; the closed class set is architecture, bug-fix, feature, process, simplification, and testing.

Non-trivial work creates or updates its owning Note while the decision is made. The pre-push workflow is the final enforcement point and must reject an outgoing change whose required Note is missing or inconsistent with the code and commit history.

Implemented Notes stay aligned with current factual realization without accumulating change narration. Decision reversals use a new cross-linked Note; archived Notes are frozen and are not current authority.

## Alternatives considered

**Keep rationale only in commit messages.** Rejected because one decision may span several commits and later factual updates, while commit history is a poor current owner for alternatives and consequences.

**Store decisions under ordinary `docs/`.** Rejected because project and architecture documentation own current human-facing facts, while Agent Notes have a separate decision lifecycle and maintenance contract.

**Generate Notes automatically at Push time.** Rejected because a script cannot reliably determine engineering intent or invent real alternatives, and generated prose would turn an enforcement checkpoint into the source of the decision.

## Consequences

Non-trivial changes now carry a reviewable decision record alongside code and commit history. The repository still needs pre-push, review, and CI checks for semantic presence, classification, format, and archived-note immutability.

## Verification

The initial tree includes the canonical README, scoped instructions for active and archived Notes, and this implemented process decision. Mechanical gates and the pre-push integration remain explicit follow-up work.
