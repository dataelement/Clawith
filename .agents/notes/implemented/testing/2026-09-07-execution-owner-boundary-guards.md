# Agent Note: Execution owner boundary guards

Status: implemented — architecture checks distinguish approved S2 schema identities from deleted legacy authorities and protect execution-private modules.

## Problem

The approved S2 schema reuses two ordinary table names that the legacy-deletion guard forbids globally. Meanwhile execution owners introduce private codecs and adapters beyond the persistence filenames covered by the original import checks.

## Decision

The deletion guard permits `agent_triggers` only in `app/modules/trigger/models.py` and `channel_deliveries` only in `app/modules/channel/models.py`. Other legacy facts, classes, paths and alternate table owners remain forbidden. The S2 PostgreSQL gate independently verifies the new ownership graph; the name exception is not proof of schema correctness.

Both module-boundary checks reject cross-owner imports of adapters, continuation, contracts, execution, files, MCP and Skill implementation modules as well as the existing persistence and crypto modules. Same-owner imports remain valid; consumers use `public.py` exports.

The target-tree scanner includes application-side `execution_dependencies` adapters. They may consume only public owner contracts. Owners, infrastructure and Runtime cannot import this composition package, so implementing a Tool bridge cannot introduce a reverse dependency or another business authority. The same legacy-import and singleton checks apply to this package.

## Alternatives considered

Removing the reused names from the global forbidden sets would permit them under unrelated owners. Exact owning-file exceptions retain that protection without renaming tables already specified by the approved contract.

## Consequences

New private implementation filenames must be covered by the architecture guards when introduced. Guard exceptions do not authorize legacy compatibility or product service implementation.

## Verification

Positive and negative fixtures cover each private module, same-owner access, composition public/private imports, reverse dependencies and both approved and misplaced S2 table names. These are static ownership checks, not execution or database evidence; the separate S2 test suite exercises actual PostgreSQL constraints.
