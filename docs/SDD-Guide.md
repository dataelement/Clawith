# SDD Guide — Spec-Driven Development Workflow

> Authoritative guide for feature development and document archiving in Clawith.
> Root `AGENTS.md §4` contains the quick-reference flow; this document is the full specification.
> Architecture laws every feature must obey → [`docs/constitution.md`](constitution.md).

---

## 1. Pick the Track (流程分级)

Not every change requires the full SDD pipeline. Match track to scope:

| Track | When to Use | Required Steps |
|---|---|---|
| **Hotfix / Trivial** | Bug fix, copy/text change, dep bump, ≲ 1 file of logic, **no contract change** | Branch → fix → `arch-guard.sh` pass → unit test → Code Review → merge. No spec/design docs. |
| **Small Feature** | Single module, no cross-feature contract change, low uncertainty | Lightweight `spec.md` (Acceptance criteria) → implement → test → review. |
| **Full SDD Track** | New capability, cross-module change, new/changed API contract, or touches a constitution clause | Full pipeline (§2) with mandatory ★ pause points. |

---

## 2. Full SDD Pipeline

```text
1. Spec Discovery (explore codebase, clarify intent) → ★ User Confirms
2. spec.md    (What & Acceptance Criteria)          → ★ User Confirms
3. design.md  (How & Gotchas & Constitution Check)   → ★ User Confirms
4. tasks.md   (Task breakdown & execution log)
5. Branch feat/{NNN}-{name}
6. Implement wave-by-wave & run tests
7. Run scripts/arch-guard.sh & test suite
8. Code Review & Merge
```

---

## 3. Pause Points (★) — Human-in-the-Loop

★ represents a **mandatory stop where the agent must pause and wait for user confirmation**.

- **Fixed ★**: After Spec Discovery, after `spec.md`, after `design.md`.
- **Dynamic ★ (Deviation Re-confirmation)**: During implementation, if a technical discovery invalidates a previously agreed-upon spec or design decision, **stop and re-confirm with the user**.

---

## 4. Document Roles & Archiving Principles

| Document | Purpose | Location / Update Rule |
|---|---|---|
| **`spec.md`** | What & acceptance criteria | Archived under `docs/features/v{X.Y.Z}/{NNN}-{name}/` |
| **`design.md`** | Why this How + today's-state snapshot + Known Gotchas | Overwrite in-place for current state; keep decision reasons and gotchas |
| **`tasks.md`** | What was done, in what order | Appended running log during feature execution |

### Key Archiving Rules:
1. **Single Source of Truth**: Laws in `constitution.md`, subsystem architecture in `docs/architecture/`, feature deliverables in `docs/features/`.
2. **Overwrite-in-Place for Architecture**: `docs/architecture/` files always reflect today's latest system snapshot.
3. **Keep Decision Reasons & Gotchas**: Record *why* alternatives were rejected and known traps in `design.md` so future developers do not repeat failed technical attempts.
