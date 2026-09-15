---
name: clawith-pre-push-checks
description: Use before pushing or force-pushing a Clawith branch, before claiming that an outgoing change passed its required checks, and again after a rebase, merge, or conflict resolution changes the effective diff.
---

# Clawith Pre-push Checks

Use this Skill to determine whether the complete committed outgoing change closes every affected contract chain and whether the selected evidence proves those contracts at their owning boundaries.

The Skill does not grant Push authority. When the enclosing user request or workflow already authorizes a Push, a `Ready` result permits the Push procedure below. Otherwise, stop after reporting `Ready` or `Blocked`.

## Inspect the outgoing change

Confirm the repository, branch, worktree, and remote state before selecting checks:

```sh
git rev-parse --show-toplevel
git status --short --branch
git remote -v
git branch -vv
```

Resolve the real target and base from the current pull request or branch configuration. When a pull request exists, query its current base instead of assuming `main` or `develop`. Fetch the verified remote ref before computing scope.

```sh
gh pr view --json baseRefName,headRefName,headRepository
git fetch <remote> <base>
git merge-base HEAD <remote>/<base>
```

Inspect committed outgoing work and local worktree state separately:

```sh
git log --oneline <merge-base>..HEAD
git diff --name-status <merge-base>...HEAD
git diff --cached --name-status
git diff --name-status
git ls-files --others --exclude-standard
```

The outgoing Push contains committed changes only. Local changes that are unrelated to the outgoing intent remain outside verification scope and must not be staged or modified. Return `Blocked` when a staged, unstaged, or untracked path belongs to the outgoing intent but has not been committed.

If no pull request or upstream target exists, resolve the intended target from the enclosing task or repository state before continuing. Do not guess a base.

## Trace affected contract chains

Group the committed diff by behavioral intent, not only by directory. For each changed behavior or shared contract, trace:

```text
Authoritative owner
→ Producers and mutation points
→ Persistence or durable representation
→ API, event, Tool, worker, process, or integration boundary
→ Backend, Frontend, model, or external consumers
→ Error, cancellation, compatibility, and unknown-value behavior
→ Owning tests, documentation, and Agent Note
```

Use repository search, imports, schemas, event names, API routes, model and Tool contracts, persistence models, and tests to find real producers and consumers. Do not infer a complete chain from filenames alone.

A contract chain is closed only when every affected participant changes with the contract, is verified to remain compatible, is deliberately removed with its obsolete paths, or is explicitly outside the change under an owning documented contract.

Return `Blocked` when a changed authoritative fact or shared contract has an unresolved producer, consumer, persisted representation, error path, test, or owning document. Running broader tests does not compensate for an incomplete implementation chain.

## Check Agent Note alignment

Use [the Agent Note rules](../../notes/README.md) to decide whether the outgoing change is non-trivial. Search active Notes before accepting a newly created Note:

```sh
rg -n "<contract|symbol|feature|decision term>" \
  .agents/notes/proposed .agents/notes/implemented \
  --glob '*.md' \
  --glob '!AGENTS.md'
```

For every non-trivial change, require one owning Agent Note in the outgoing commits. Update an existing owner when the decision is unchanged; create a new cross-linked Note when the decision changes.

Check the three records together:

```text
Code
→ implements the decision

Agent Note
→ owns the durable rationale, current contract, alternatives, and consequences

Commit history
→ records the concrete intent, scope, and verification of this change
```

Return `Blocked` when a non-trivial change has no owning Note; a duplicate Note replaces an existing owner; code, Note, and commit intent describe different contracts; an implemented Note retains stale proposal wording or mechanisms; a reversal rewrites the old Note rather than superseding it; a rejected or archived Note is treated as current authority; or the Note omits a real alternative or invents one that was not considered.

A `proposed` Note may accompany design or work that is not yet the current implementation. Code presented as complete or ready to merge requires the owning Note to describe that implemented behavior in the present tense.

## Apply the testing policy

Read and apply [the repository testing policy](../../../docs/testing.md). The policy is the sole owner of what each evidence surface proves, full-suite triggers, historical-baseline treatment, and failure rules; do not restate or replace those decisions in this Skill.

For each affected contract chain, record the selected commands, the owning behavior each command proves, and why no broader boundary is required. Run the selected checks, read their complete results, and return `Blocked` when a required check fails or a required verification surface remains unavailable.

Do not repeat a passing check solely because a Commit or Push follows. Rerun it only when the effective diff, environment, dependency graph, generated output, or owning contract has changed.

## Push authorization and procedure

Push only when the enclosing user request or workflow already authorizes publishing the branch. Otherwise stop after reporting `Ready` or `Blocked`.

Before an ordinary Push:

1. Require every selected check to pass. A required verification gap remains `Blocked`.
2. Require every change belonging to the outgoing intent to be committed. Preserve unrelated local modifications without staging or including them.
3. Fetch the current remote branch and confirm that the expected remote head has not moved.
4. Push the current `HEAD` to the intended remote branch.
5. Verify that the remote branch resolves to the same commit as local `HEAD`.

For an authorized history rewrite, record the observed remote commit and use an exact `--force-with-lease`; never use raw `--force`. Abort when the remote moved.

After Push, inspect the pull request checks and commit statuses. Report pending checks as pending. A successful `git push` proves only that the remote ref moved; it does not prove CI, merge readiness, deployment, or live acceptance.

Do not create empty commits, rewrite history, retarget branches, or toggle pull request state merely to provoke CI without first identifying why the expected check did not run.

## Report

Return one final status:

- `Ready` — the committed outgoing change closes every affected contract chain and the selected evidence passed, but no Push was authorized.
- `Blocked` — a contract-chain gap, Agent Note mismatch, relevant check failure, unresolved target, or required verification gap prevents Push.
- `Pushed` — an authorized Push completed and the remote branch matches local `HEAD`.
- `Pushed, CI pending` — the remote branch matches, but required remote checks are not terminal.
- `Pushed, CI failed` — the Push completed, but a required remote check failed.
- `Pushed, CI not observed` — the remote branch matches, but no authoritative remote check was available or configured for observation.

Report the verified base and local `HEAD`; outgoing behavioral intents; affected contract chains and owners; owning Agent Notes; commands actually run and exact results; relevant checks not run and why; unrelated local changes only when they could affect handoff; remote branch and commit after Push; and CI, merge, deployment, and live-acceptance status as separate facts.

Do not report broader success than the collected evidence supports. Keep source facts, local test evidence, remote CI, deployment, and live-system acceptance separate.
