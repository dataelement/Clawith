---
name: clawith-prose-standard
description: Write, review, restore, or trim Clawith Markdown, Agent Notes, AGENTS instructions, code comments, prompts, diagnostics, and user-visible strings while preserving complete contracts and removing repetition or decorative prose.
---

# Clawith Prose Standard

Require an explicit scope. Review tasks report findings without editing; write or fix tasks apply clear changes. Never edit archived Agent Notes.

## Preserve complete contracts

Before editing, identify every actor, action, condition, ordering rule, modality, negative guarantee, exception, owner, side effect, failure mode, consequence, and quantitative bound. Remove words only when every relevant proposition survives and the result is clearer.

Types define structure. Owning prose defines non-obvious behavior, failures, side effects, ownership, timing, cancellation, durability, limits, and safe use. Keep one authoritative explanation and link it elsewhere; do not copy architecture or another module's contract.

## Write for the owning surface

- **AGENTS instructions:** concise behavioral guardrails, explicit scope, and links to owning detail.
- **Agent Notes:** Problem, real decision or proposal, actual alternatives, consequences, verification, and named gaps; no invented rationale.
- **Public interfaces and comments:** non-obvious caller or maintainer contract, not code restatement or control-flow narration.
- **Tests:** only non-obvious fixture, platform, real-entry, or observation rationale.
- **Prompts, Tool schemas, diagnostics, and visible strings:** task-relevant concepts from the model or user's perspective; wording is behavior.
- **Reference documentation:** current facts and contracts, not change history or implementation diaries.

Write directly and name the actual actor, file, API, operation, state, or behavior. Prefer exact terms over metaphors. One prose paragraph occupies one physical source line; use paragraph breaks for separate ideas and preserve lists, tables, and code blocks.

## Workflow

Read the owning code or contract before judging prose. Classify each passage as keep, add, trim, restore, restructure, move-to-owner, or defer. Update the owner before derivative text, then inspect analogous passages learned from the same rule.

Verify relative links, Markdown formatting, changed code examples, model-visible behavior, and the relevant repository gates. Report scope, changes, deliberate keeps, deferred cases, and checks actually run.
