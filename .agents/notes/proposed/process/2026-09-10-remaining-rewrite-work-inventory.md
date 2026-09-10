# Agent Note: Separate remaining product work from implemented foundations

Status: proposed — the remaining-work inventory is available for discussion; module order and individual product contracts are not approved by this inventory.

## Problem

The 17 unreviewed product-contract entries exclude management surfaces for implemented owners and retained capability families such as Sandbox and external Tools. Treating that list as the complete remaining rewrite would omit work; treating every listed capability as approved would restore behavior the clean break removed.

## Proposal

Use the [remaining-work table](../../../../backend/rewrite/remaining-work.md) to navigate all 34 owners, cross-owner capability work and later qualification. Distinguish implemented foundation services from product entry paths, retained mechanics from active capabilities, and candidate functionality from approved contracts. Existing coverage, owner-contract and product-contract ledgers remain the only respective governance authorities.

Discuss and implement bounded work packages after resolving their shared dependencies. Keep the proposed order separate from approval. Do not reactivate Sandbox or expand an existing owner through the G007 label alone. Performance execution is paused at the user's request without changing thresholds or recording qualification as passed.

## Alternatives considered

Using only the 17 product entries omits existing-owner product integration and retained external operations. Treating all 34 owners as unimplemented discards verified foundation work. The inventory separates these surfaces and retains explicit unknowns rather than creating another approval ledger.

## Verification

The inventory is checked against the owner roster, product roster, coverage states, source layout and application entry points at implementation baseline `64f83bb1`. Validation confirms all 34 owners appear exactly once in the module tables, the 17 product entries match their ledger, all 401 coverage records retain their recorded disposition state, and local links resolve. This is planning evidence, not exhaustive historical operation coverage or authorization to start a new product implementation.
