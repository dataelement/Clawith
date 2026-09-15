# Agent Note: Contract-chain pre-push evidence selection

Status: implemented

## Problem

Clawith lacked a shared rule for deciding which checks an outgoing change required. Agents either ran narrow tests without tracing shared consumers or reflexively ran complete Backend, Frontend, browser, and integration suites. Neither behavior proved that the changed contract was complete, and repository-wide ESLint and Prettier baseline failures made indiscriminate full checks especially noisy.

## Decision

The [testing policy](../../../../docs/testing.md) defines what each verification surface proves. The [`clawith-pre-push-checks`](../../../skills/clawith-pre-push-checks/SKILL.md) workflow applies that policy to committed outgoing changes.

The workflow resolves the real Base, groups the committed diff by behavioral intent, traces every affected contract from authoritative owner through producers, persistence, boundaries, consumers, tests, documentation, and Agent Note, and blocks a Push when the chain is incomplete. It selects the narrowest evidence that would fail for the intended regression and expands only across boundaries the change actually reaches.

Local unrelated changes remain outside the outgoing verification scope. A local change that belongs to the same outgoing intent but is not committed makes the outgoing change incomplete and blocks the Push.

The Skill may Push only when the enclosing request already grants that authority. Otherwise it reports `Ready` or `Blocked`. After an authorized Push, remote ref movement, CI, merge readiness, deployment, and live acceptance remain separate facts.

## Alternatives considered

**Run all Backend, Frontend, browser, migration, and integration checks before every Push.** Rejected because cost and environmental noise do not establish contract relevance, and broad green suites cannot compensate for a missing producer or consumer.

**Map changed directories directly to fixed commands.** Rejected because a one-line shared-contract change may require both applications and durable representations, while many multi-file refactors remain local to one behavior.

**Develop a change-scope script before the workflow.** Rejected for the first version because Git already exposes the required committed and local facts; repeated use should identify which discovery steps warrant mechanical extraction.

**Let the Skill Push whenever it reports Ready.** Rejected because verification does not broaden the user's authorization to mutate a remote branch.

## Consequences

Pre-push verification now evaluates complete contract chains rather than file counts. Historical baseline failures remain visible but do not authorize new violations or unrelated cleanup. The Testing Policy remains the single owner of evidence semantics; the Skill applies it to the outgoing change. The workflow requires semantic Agent judgment for contract closure and Agent Note alignment; CI can enforce only the mechanical portions.

## Verification

The Skill and testing policy cross-link each other, use repository-relative paths, distinguish committed outgoing work from unrelated local state, and preserve Push authorization as an external precondition. Existing Drone configuration files remain unchanged, and GitHub Actions retains its current release orchestration. New change-scoped quality evidence runs locally through the pre-push workflow; GitHub Actions may later enforce selected checks remotely when their repository-wide baselines and validators are ready. Pyright, ESLint, Prettier, Markdown, and Agent Note gates remain follow-up work until those prerequisites exist.
