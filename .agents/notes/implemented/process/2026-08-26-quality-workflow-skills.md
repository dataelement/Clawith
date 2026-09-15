# Agent Note: Repository quality workflow Skills

Status: implemented

## Problem

Clawith's repository instructions define contract-chain completion, evidence boundaries, dead-code removal, prose quality, and review expectations, but standing rules alone do not tell an agent how to apply those decisions to a concrete scope. Copying DeepSeek Harness workflows verbatim would introduce Cordis, pnpm, snapshot, stack, and bilingual-document assumptions that Clawith does not use.

## Decision

Repository-owned workflows live under `.agents/skills/` and link to the authoritative root instructions, [testing policy](../../../../docs/testing.md), and [Agent Note rules](../../README.md) rather than duplicating those sources.

The first workflow set contains `clawith-pre-push-checks` for committed outgoing contract chains and evidence selection, `clawith-code-review` for independent correctness and architecture review, `clawith-find-simplifications` for deletion/reuse/ownership repair, `clawith-prose-standard` for complete contract-focused prose, and `clawith-trim-cot-leakage` for removing session-relative reasoning while preserving facts.

Each Skill has one narrow trigger and workflow. Pre-push may publish only when the enclosing request already authorizes Push. Review is read-only unless fixes are separately authorized. Simplification requires an explicit scope and evidence of current owners and consumers. Prose and CoT workflows never edit archived Agent Notes.

## Alternatives considered

**Copy the DSH Skills without adaptation.** Rejected because their package graph, test commands, GitHub Stack workflow, Snapshot Harness, i18n, and archive sealing are not Clawith contracts.

**Create one repository-quality mega-Skill.** Rejected because review, pre-push, simplification, and prose have different triggers, permissions, evidence, and stopping conditions; loading all instructions for every task would waste context and blur authority.

**Create Archive, Defensive Pattern, documentation-site, i18n, Snapshot, and generated-catalog workflows immediately.** Rejected because the repository does not yet have current consumers or mechanical infrastructure for those systems. Add them when concrete notes, incidents, publication requirements, or harnesses justify the ownership cost.

## Consequences

Agents can apply the repository's quality rules through small task-specific workflows without importing DSH-specific machinery. The selected Skill directories and Agent Note tree must be tracked despite the broader `.agents/` ignore rule. Testing and pre-push form the initial development loop; CI and mechanical Agent Note/Markdown gates remain follow-up enforcement work.

## Verification

Every Skill passes the Skill Creator validator, has matching UI metadata, uses repository-relative links, contains no template TODOs, and follows one-physical-line-per-paragraph Markdown formatting. Separate code and architecture perspectives evaluate the complete change before merge readiness; independent reviewers are required for the high-risk surfaces named by the review Skill and used when available elsewhere.
