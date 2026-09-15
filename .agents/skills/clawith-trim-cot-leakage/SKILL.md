---
name: clawith-trim-cot-leakage
description: Audit or remove reasoning-transcript leakage from Clawith comments, JSDoc, Markdown, Agent Notes, prompts, and visible prose. Use for AI-sounding change narration, dead draft references, review dialogue, control-flow walkthroughs, hedged planning residue, or session-relative wording.
---

# Clawith Trim Chain-of-Thought Leakage

Read and apply [`clawith-prose-standard`](../clawith-prose-standard/SKILL.md) first. Require an explicit scope. Never edit archived Agent Notes, recorded fixtures, snapshots, or verbatim evidence.

## The test

For every suspect passage ask: could a reader at current `HEAD`, with no session transcript, review thread, or uncommitted draft, resolve every reference and verify every claim? If not, restate surviving facts from the repository's current perspective and delete the transcript around them. Delete passages with no durable fact.

## Leakage classes

- Dead design-session citations, temporary decision numbers, audit labels, draft sections, or phase names with no committed owner.
- PR, stack, reviewer, or authoring-session narration instead of current behavior.
- “Previously”, “now”, “no longer”, “this version”, or similar change narration in current-state prose.
- Reviewer-addressed defenses such as “this is correct because”; state the invariant or delete the comment when code already shows it.
- Control-flow narration, test walkthroughs, obvious branch proofs, and shortened reasoning summaries.
- Hedges such as “probably fine”, “for now”, or “should be enough” without a real bound or tracked follow-up.

## Preserve sanctioned facts

Keep resolvable issue references, Agent Note and incident evidence, required suppression reasons, empty-catch explanations, measured bounds, runtime old/new lifecycle states, and present-tense regression counterfactuals. Fix false explanations; do not delete required rationale merely because it resembles commentary.

## Workflow

Audit read-only first and judge every hit semantically. Enumerate each passage's propositions before deletion. Fix the owning source before generated or copied prose. Treat model-visible wording as behavior and require its owning verification rather than silently rewriting it.

After editing, reread the complete surface, confirm every remaining reference resolves at `HEAD`, run Markdown/link/prose checks for the touched scope, and report preserved facts, removed leakage, and unresolved borderline cases.
