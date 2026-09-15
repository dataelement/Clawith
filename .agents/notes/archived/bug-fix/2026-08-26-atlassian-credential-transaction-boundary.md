# Agent Note: Atlassian Credential and Tool-Sync Boundary

Status: implemented — Atlassian credentials and assigned tools share one fail-closed persistence contract.

Archived: 2026-09-03

## Problem

Atlassian configuration spans the owning `ChannelConfig`, discovered shared `Tool` records, per-Agent assignments, and runtime credential dispatch. Persisting plaintext credentials, accepting undecryptable values as legacy plaintext, or committing those records independently would expose a secret at rest, dispatch ciphertext as a credential, or publish configuration success without matching tool assignments.

## Decision

`app.services.atlassian_tool_service` owns configuration reads, writes, deletion, connection tests, Provider discovery, shared Tool upsert, per-Agent assignment synchronization, transaction settlement, and assignment cleanup. API routes authenticate, normalize transport input, and map service outcomes to HTTP. Runtime imports the service directly and never imports an API module.

`ChannelConfig.app_secret` is the sole persisted Atlassian credential. Atlassian Tool config, AgentTool config, and `ChannelConfig.extra_config` never contain a credential alias; synchronization removes legacy copies while preserving unrelated config. API projections return only non-secret fields. Runtime obtains the decrypted key transiently through the strict service reader. Missing or corrupt ciphertext fails before Provider dispatch with an explicit configuration failure.

One service-owned identity predicate recognizes canonical, legacy, and imported Atlassian Tool records by normalized category, name, server name, or structured canonical URL. URL identity includes default-port, trailing-slash, query, and fragment variants for rejection, redaction, and cleanup. Generic Tool creation, update, deletion, server configuration, and per-Agent credential writes cannot bypass the category configuration owner. Generic Smithery and direct MCP imports reject both requested and existing Atlassian records before mutation. Runtime attaches the authoritative credential only to the canonical HTTPS endpoint without user information, query, or fragment; a matching display name cannot redirect the credential to another host.

Atlassian discovery, Tool upsert, AgentTool assignment, and ChannelConfig mutation reuse the request's `AsyncSession`. The service owns the single commit after synchronization succeeds. Missing credentials, discovery failure, empty discovery results, encryption failure, or persistence failure cannot return configuration success; the service rolls back instead. Both configuration routes await the same command and do not create unowned background tasks.

Deleting either Atlassian configuration surface removes the owning `ChannelConfig` and that Agent's Atlassian `AgentTool` assignments in the same transaction. Shared `Tool` discovery records remain available for other Agents. Cleanup failure rolls back both sides, so configuration deletion cannot leave an enabled orphan assignment.

Deployments that may contain pre-fix secret copies use `scripts/remove_legacy_atlassian_agent_tool_secrets.py`. The out-of-band job defaults to dry-run and processes matching Tool config, AgentTool config, and Atlassian `ChannelConfig.extra_config` in bounded batches. It removes only supported credential aliases, is idempotent, and preserves unrelated config and rows. Applying the cleanup is intentionally irreversible because legacy plaintext and corrupt ciphertext cannot be distinguished or restored safely; the authoritative encrypted `ChannelConfig.app_secret` is retained.

## Alternatives considered

- Preserve background synchronization and report eventual status separately. Rejected because no durable synchronization object or consumer currently owns that lifecycle.
- Keep Tool or AgentTool credential copies as runtime fallbacks. Rejected because either copy duplicates the ChannelConfig authority and expands the secret persistence surface.
- Treat decryption failure as legacy plaintext. Rejected because corrupt ciphertext and plaintext cannot be distinguished safely at the dispatch boundary.

## Consequences

Atlassian configuration may take as long as provider discovery, but success means the encrypted ChannelConfig and non-secret Tool/AgentTool records committed together. Provider unavailability is visible as an HTTP failure and does not publish partial configuration state. Removing configuration also removes only the requesting Agent's assignments; shared Tool records and other Agents' assignments are preserved. Platform startup may use `ATLASSIAN_API_KEY` transiently for discovery but never copies it into Tool config. Other MCP providers retain their existing credential contracts.

## Verification

Regression coverage verifies missing-key rejection before database work; category-case and URL identity variants; canonical route repair; requested and existing generic import rejection; current and proposed mutation rejection; API redaction; absence of Tool, AgentTool, and extra-config secret copies; shared-session synchronization before one service-owned commit; rollback on synchronization or commit failure; corrupt-ciphertext rejection; attacker-URL isolation; atomic deletion through both routes; and dry-run, selector, idempotence, and rollback behavior for legacy cleanup. Backend Pyright and the focused Atlassian, dynamic MCP, and LLM capability tests must remain green.
