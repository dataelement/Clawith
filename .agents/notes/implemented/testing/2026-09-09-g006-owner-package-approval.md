# Agent Note: Bound G006 owner packages to approved implementation contracts

Status: implemented — the owner-package gate recognizes G006 implementation approval and validates each allowed source path.

## Problem

The package gate recognized Run implementation only through the original core-runtime contract path and treated all S2 product owners as schema-only. An approved G006 amendment therefore made existing Run implementation appear unauthorized while preventing the approved product services from being implemented.

## Decision

`backend/tests/architecture/test_owner_package_skeleton.py` derives implementation eligibility from the owner manifest's approved state, owner identity, implementation phase and recognized implementation contract. Run and Context retain phase-4 eligibility under the core-runtime or product-input contract; the six product-input owners require phase 5 and the product-input contract. Schema approval alone still permits only the established schema files.

Allowed service files remain explicitly enumerated by owner. Channel's provider directory permits only named provider files; recursive inspection rejects unknown files, unexpected directories and symlinks. A directory named after an allowed source file cannot hide additional implementation. Unapproved owners and S3 services cannot use the G006 approval sets to bypass their gates.

## Alternatives considered

Retaining the original contract-filename check would reject approved amendments. Allowing every file under an approved package or provider directory would remove the source-boundary check. The gate instead recognizes the approved implementation contracts while retaining a finite path roster.

## Consequences

G006 transport lives under `app/api/product_inputs/`; deleted legacy `app.api.auth`, `app.api.groups` and `app.api.schedules` identities remain forbidden. Only the application root and transport peers may import the new routers. The import guard scans this package and rejects owner-private imports or reverse imports from Runtime and execution dependencies. Positive and negative fixtures enforce both directions.

Azure Identity, croniter and Pillow are no longer orphan dependencies: Channel's Teams managed-identity provider, Trigger's cron validation and attachment previews have actual consumers and tests. They leave the deleted-owner dependency set; removed Runtime and integration libraries without current consumers remain forbidden.

Adding another implementation file requires an explicit owner-roster update and its normal review. This test does not grant contract approval or validate approval receipts; the owner-contract manifest checker remains responsible for those facts. Passing the package test establishes source placement, not product behavior or phase completion.

## Verification

The package suite covers the actual checkout and positive and negative fixtures for schema-only approval, G006 services, amended Run approval, approval state and phase mismatches, unknown Channel provider paths, directories masquerading as source files, symlinks, and S3 bypass attempts. The focused package suite passes 38 cases. Import-boundary fixtures cover the permitted transport imports and forbidden legacy, private and reverse imports; dependency fixtures retain both allowed current libraries and forbidden orphan libraries. No manifest or approved specification is changed by these checks.
