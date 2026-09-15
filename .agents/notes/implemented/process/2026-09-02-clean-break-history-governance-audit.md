# Agent Note: Clean-break history governance audit

Status: implemented — preserves audited local commit identities under three closed historical exception sets

## Problem

The clean-break sequence contains readable Lore fields that Git does not recognize as one native trailer block, and ten non-trivial G001/G002 commits did not update an owning Agent Note in the same commit. Rewriting the sequence would repair those historical records but would also replace more than seventy local commit identities and weaken the traceability already attached to review, test, and checkpoint evidence. The original scan began after the immutable reference commit and therefore omitted the two malformed foundation commits `ea6acc19` and `8ed4ae2f`. G002 closeout later found one additional malformed Lore block in `04315255`; repairing it in place would replace 43 established descendant commit identities.

This audit does not make the affected commits compliant with the [Lore protocol](../../../../AGENTS.md#lore-commit-protocol) or the [Agent Note alignment rules](../../README.md). It records the exact exception so current and future work does not mistake preserved history for retroactive compliance.

## Decision

The repository preserves the existing SHAs in `8ed4ae2f..1f26bf5c`. The user approved this as a one-time historical exception on 2026-09-02. The original exception covers only the 38 malformed Lore trailer blocks and the ten missing same-commit Agent Note updates listed below. The corrected outgoing-range audit adds the two foundation commits omitted by the original range expression. G002 closeout separately preserves `04315255` under the same no-history-rewrite decision. None of these closed exception sets waives any code, architecture, test, security, or checkpoint requirement.

The technical G001 gates and their recorded results remain valid because preserving commit identities changes neither their trees nor the evidence produced by those gates. This decision does not convert source or test evidence into CI, deployment, or live-system evidence.

Every commit created after this audit must separate the body from one contiguous trailer block with exactly one blank line. Individual trailers must not be separated by blank lines. Authors should provide the full message through a message file or Git trailer tooling and verify the result with `git show -s --format=%B <sha> | git interpret-trailers --parse`. Recording the two pre-audit foundation commits and the already-created `04315255` defect does not reopen any exception or authorize another malformed Lore block or missing same-commit owning Note.

## Foundation Lore exceptions omitted by the original range

The complete outgoing range begins at parent `2059dceb`, so it includes two malformed foundation commits that the original `8ed4ae2f..1f26bf5c` scan could not include:

- `ea6acc193f3d11ad1289279700f4c5e39c1ccc1c` — Establish the clean-break agent execution architecture (9 labeled; 1 parsed)
- `8ed4ae2fa8afd84b3344522ffe18acb153b607d6` — Establish an implementation-ready clean-break backend contract (7 labeled; 1 parsed)

These commits predate the audit and establish the immutable reference boundary used by later evidence. They remain noncompliant historical messages; this correction records their exact scope without changing their trees or descendants.

## Malformed Lore trailer audit

The audited range contains 71 linear, non-merge commits after `8ed4ae2f` through `1f26bf5c`. For each message, the audit counted lines using the Lore vocabulary in the root instructions and compared that count with the non-empty output lines from `git interpret-trailers --parse`. The following 38 commits contained labeled Lore lines that were not all parsed; Git recognized only the final one-line trailer paragraph in each message:

- `5a0616c9700d501e9b56b600982dedbf43889568` — Make the 50-Agent capacity gate reproducible (8 labeled; 1 parsed)
- `8cec3e3a0377754831accf9daf6411ee9d4c6174` — Prevent unapproved owners from entering rewrite phases (7 labeled; 1 parsed)
- `ee56f5b8507f7e1c735e106f0f3167ff2ebf8617` — Freeze legacy surfaces before the backend clean break (7 labeled; 1 parsed)
- `7abad63f2238a916289e9ba50222cfe66b419771` — Make coverage ownership follow the canonical ledger (7 labeled; 1 parsed)
- `5646a4c3df72978bf76c8cb72f3f8ac1fb9a3951` — Keep Backend load evidence comparable (8 labeled; 1 parsed)
- `bfffbe1143a45103699654a1f5ba7d2270e85c13` — Close the S3 owner approval bypass (7 labeled; 1 parsed)
- `8b364034dc6fc16ce0acd01294445fa5be947224` — Freeze every legacy surface before target replacement (8 labeled; 1 parsed)
- `1b13003b8812d3cd0107fe9cc41a1ef31dec5367` — Make target ownership violations fail before implementation (9 labeled; 1 parsed)
- `9c0f1a01bdf0cb6a19165bf906f496e65e938e06` — Make target startup incapable of reviving legacy persistence (9 labeled; 1 parsed)
- `18ace9cc260c4e5e2a8c175c253c3c332becc282` — Make startup authority closed under review (8 labeled; 1 parsed)
- `974a4e9e5cb7be585412aa7096e675b99493a153` — Prevent legacy Context import identity from returning (8 labeled; 1 parsed)
- `18c64acb4ba8e43f62d3099508887f52ad4e9a17` — Prevent structured Experience import identities from returning (8 labeled; 1 parsed)
- `f679bea1cc0201aa7ba55f58e8cffc1c22af5942` — Prevent old Model and LLM import identities from returning (8 labeled; 1 parsed)
- `dbca6d3e1ef412869f176363bfb988ebbdf36805` — Prevent persistent Task authority from returning (8 labeled; 1 parsed)
- `c246c26511a147cfb2c94ec8556ac23f21d609d2` — Prevent legacy Tool authority from returning (9 labeled; 1 parsed)
- `7bd51e8e0afa74486bc5fedcbd25a4ec594452c2` — Prevent OpenClaw Gateway authority from returning (9 labeled; 1 parsed)
- `3e69d287f21a5a4a9e134f4fa63087909226da5d` — Prevent old Agent Credential authority from returning (9 labeled; 1 parsed)
- `efbed07608b619a7d8dd432c1b5a9cddad80d70d` — Close the deleted Credential DAO export surface (8 labeled; 1 parsed)
- `e95f1abfd2247721df040831a14de26f16f4d3dd` — Require Credential readiness before SSO (7 labeled; 1 parsed)
- `0225007755290c783a58ffafb06294bed6d3f9b2` — Prevent the overloaded Agent aggregate from returning (9 labeled; 1 parsed)
- `0a365ccf031b13405b479e9dcf7bf78ec2aee961` — Keep Phase 1 fixtures aligned with owner readiness (7 labeled; 1 parsed)
- `37ccb2776b31c9f9c51e24d01c04b90b1cdd2510` — Prevent the old Identity and Tenant aggregate from returning (9 labeled; 1 parsed)
- `4151fea7e702358bdd110c0bc6102ad22a6cff76` — Prevent legacy Auth orchestration from returning (10 labeled; 1 parsed)
- `373566ec3b7cad11ca9a5e9895cb9b64dc832fdf` — Keep SSO tests with their surviving owner (10 labeled; 1 parsed)
- `1700f7479451d81489e14c4bf5202503ebe04b01` — Close Auth package-export imports in tests (8 labeled; 1 parsed)
- `439c75a635509df3710c365af01eb6db628ac957` — Prevent the old SSO authority from returning (9 labeled; 1 parsed)
- `b8111b97e0b187c47af98d3ce225f5da87a3d2fb` — Keep mixed Google Workspace entry ownership explicit (8 labeled; 1 parsed)
- `39f3e587d24341504654de10d7430264223b4bea` — Prevent the overloaded Organization aggregate from returning (10 labeled; 1 parsed)
- `6e56ded0006796c840ef5bf55ca9b9480bc0f7a3` — Stop publishing Tenant Knowledge into Agent files (10 labeled; 1 parsed)
- `89cf50d4a5d689d543a429a7845d39e26d30eac6` — Prevent the old Invitation persistence contract from returning (10 labeled; 1 parsed)
- `8b660f86e968c99c7bc202953d4e031c8dc26bb3` — Prevent legacy Onboarding state from returning (10 labeled; 1 parsed)
- `c43bfb8e1a2e1801c711fdde71fed03b793478ae` — Prevent the old Directory authority from returning (10 labeled; 1 parsed)
- `ed5daff25b0bb66165e77773aee6456e158a8895` — Prevent the old Focus authority from returning (9 labeled; 1 parsed)
- `8a5d6761eab1c5d13f0fd354d776162a0b66aa1e` — Prevent the old Notification authority from returning (10 labeled; 1 parsed)
- `0acf621e4a494ec33d77c6717e2f13c2e5d4c7aa` — Close the remaining Notification deletion gaps (10 labeled; 1 parsed)
- `1608799c1b788d5130e0565c219bb62d8b9b8726` — Keep rewrite evidence cumulative at every Goal (7 labeled; 1 parsed)
- `9c2f7b20b9fc43b73f85f954ac94a1463011666e` — Keep Goal authority recoverable from Git (7 labeled; 1 parsed)
- `eb28b017489be896466b0934970a2192a165e026` — Keep Goal checkpoint rationale with its gate (7 labeled; 1 parsed)

The fields remain readable as ordinary commit-message text. Native trailer consumers do not receive the unparsed fields and must not infer that readable labels are equivalent to parsed trailers.

## G002 closeout Lore exception

Final G002 history verification found one additional malformed commit outside the original audited range:

- `0431525597f3ed9bac10fbbf9b1ef30ebebcdde7` — Remove the unapproved legacy OKR authority (8 labeled; 1 parsed)

The repository preserves this SHA because 43 later commits already reference the resulting history and evidence. This is a recorded noncompliance, not retroactive compliance. The exception contains no additional missing-Note case and ends at this one commit.

## Missing same-commit Agent Note audit

The following ten non-trivial commits contain no path under `.agents/notes/` in their own tree diff even though each changes a decision governed by the Agent Note rules:

- G001 `8cec3e3a0377754831accf9daf6411ee9d4c6174` establishes the owner and product readiness authorities.
- G001 `ee56f5b8507f7e1c735e106f0f3167ff2ebf8617` establishes coverage lifecycle and immutable-reference contracts.
- G001 `0184b7f58b9687b33c72d7bcb4f3cc8788ac709d` establishes architecture and governance gates.
- G001 `7abad63f2238a916289e9ba50222cfe66b419771` changes coverage approval to use the canonical owner ledger and closes an approval bypass.
- G001 `bfffbe1143a45103699654a1f5ba7d2270e85c13` changes S3 readiness enforcement and closes the owner approval bypass.
- G002 `f9fbc3cb4b9a5d4b3d01d9783e0d76378bb97d0f` establishes the target module ownership package boundary.
- G002 `983e11275743a74f92b3af0f487161dcfaaa8465` changes the database secret-handling contract.
- G002 `1b13003b8812d3cd0107fe9cc41a1ef31dec5367` establishes target import-boundary enforcement.
- G002 `18ace9cc260c4e5e2a8c175c253c3c332becc282` changes the startup and migration enforcement strategy.
- G002 `e95f1abfd2247721df040831a14de26f16f4d3dd` changes the owner dependency contract so SSO requires Credential readiness.

Later Notes and fixes can describe the current contract, but they cannot satisfy the historical requirement that the owning Note travel in the same commit as each non-trivial decision. This audit is the sole record of that exception; it is not a substitute owning Note for the ten decisions.

## Alternatives considered

**Rewrite the affected commits and every descendant.** Rejected because correcting the historical messages and co-locating Notes would replace the established SHA chain used by existing review and test evidence. The benefit of retroactive formatting did not justify that traceability loss.

**Declare the readable Lore labels and later Notes retroactively compliant.** Rejected because Git does not parse the separated fields as trailers and later files were not part of the ten commits. Such a declaration would contradict the repository's native-trailer and same-change alignment rules.

**Preserve the SHAs without an explicit audit.** Rejected because future reviewers and automation would repeatedly rediscover the discrepancies without a durable scope, count, or prevention rule.

## Consequences

History-based tooling will parse incomplete Lore metadata for the two foundation commits, the 38 original commits, and the one G002 closeout commit. Reviewers must consult this audit when a native trailer query disagrees with the readable message text; they must not synthesize missing parsed values.

The ten listed commits permanently lack atomic code/Note alignment. Current owning Notes remain authoritative for current decisions, while these commits remain historical evidence of their own trees and messages. Neither source repairs the other's historical gap.

The foundation exception contains only `ea6acc19` and `8ed4ae2f`; the original exception has a closed end at `1f26bf5c`; the G002 closeout exception contains only `04315255`. A new malformed block or missing required Note is a current defect and remains blocking under the normal pre-push and review rules.

## Verification

The following commands were run from the repository root. They reported 71 commits, zero merge commits, 38 commits whose labeled and parsed counts differ, and no `.agents/notes/` path in any of the ten named commit diffs:

```bash
git rev-list --count 8ed4ae2f..1f26bf5c
git rev-list --merges --count 8ed4ae2f..1f26bf5c

for sha in $(git rev-list --reverse 8ed4ae2f..1f26bf5c); do
  msg=$(git show -s --format=%B "$sha")
  labeled=$(printf '%s\n' "$msg" | awk '/^(Constraint|Rejected|Confidence|Scope-risk|Reversibility|Directive|Tested|Not-tested|Related): /{n++} END{print n+0}')
  parsed=$(printf '%s\n' "$msg" | git interpret-trailers --parse | awk 'NF{n++} END{print n+0}')
  if [ "$labeled" -ne "$parsed" ]; then
    printf '%s\t%s\t%s\n' "$sha" "$labeled" "$parsed"
  fi
done

for sha in 8cec3e3a ee56f5b8 0184b7f5 7abad63f bfffbe11 f9fbc3cb 983e1127 1b13003b 18ace9cc e95f1abf; do
  git diff-tree --no-commit-id --name-only -r "$sha" | rg '^\.agents/notes/' || true
done
```

The complete outgoing scan used the same labeled-versus-parsed comparison over `2059dceb..HEAD` and found 41 mismatches: the two foundation commits above, the 38 original entries, and `04315255` (8 labeled; 1 parsed). The G002 closeout scan found no other post-audit mismatch, and `git rev-list --count 04315255..689d89a4` reported 43 descendants when the preservation decision was recorded. The commit trees were not rewritten. This audit did not substitute history inspection for G001 or G002 technical gates; those gates have separate tracked checkpoint evidence.
