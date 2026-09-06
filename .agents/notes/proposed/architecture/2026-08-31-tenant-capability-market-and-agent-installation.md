# Agent Note: Tenant Capability Market and Agent Installation

Status: proposed — the first-release shared Tool, MCP, and Skill discovery and Agent installation model is agreed but not implemented

## Problem

Tool, MCP, and Skill packages need one searchable product surface without copying one external definition, MCP server, or package for every Agent. An Agent must be able to install a capability in the first release, while another Agent in the same Tenant must not receive it automatically or share the installing Agent's Token.

The current per-Agent MCP import path mixes shared Tool definition, mutable server route, Agent assignment, and Agent Secret. Name-based reuse can overwrite another Agent's route, while assignment backfill creates rows that do not represent an explicit installation. The target needs Tenant-level deduplication, Agent-level activation, Agent-specific MCP authentication, and unified administration.

## Proposal

### One Capability Market

`capability_catalog_items` is the shared Market index for `tool`, `mcp`, and `skill` packages:

```text
Capability Catalog Item
  - id
  - tenant_id, optional
  - origin_platform_item_id, optional
  - kind
  - source
  - source_key
  - name
  - description
  - version
  - manifest_schema_version
  - manifest
  - definition_revision
  - enabled
  - installed_by_membership_id, optional
  - installed_by_agent_id, optional
  - created_at
  - updated_at
```

A null Tenant identifies a platform discovery template. It is searchable but is never referenced directly by Agent connection, Tool Grant, Workspace package, or Run. The first Tenant install atomically materializes one non-null Tenant Catalog Item with `origin_platform_item_id`; every later Agent in that Tenant reuses the materialized item. A Tenant source is installed or created only inside that Tenant, and all executable definitions, connections, grants, and packages reference the Tenant materialization through ordinary same-Tenant composite foreign keys.

PostgreSQL uses two partial unique indexes: `(kind, source, source_key)` where `tenant_id IS NULL`, and `(tenant_id, kind, source, source_key)` where `tenant_id IS NOT NULL`. This prevents nullable uniqueness from admitting duplicate platform templates while keeping Platform and Tenant namespaces independent. Concurrent first installs of a platform template converge on one Tenant materialization through the Tenant unique index. MCP uses a normalized registry identity or URL, Skill uses its stable package identity and version, and Tool uses its stable code or product key. Installer identity is audit only and never changes Tenant ownership.

Market owns search, deduplication, display metadata, source, version, enablement, and installation origin. It does not become the execution or file authority. Tool Definition remains with Tool System, MCP-discovered Tool Definitions remain related to the MCP item, and installed Skill files remain authoritative in Workspace. The typed versioned Market manifest contains only bounded discovery metadata and owner references rather than replacing those records with arbitrary JSON.

### Shared registration and separate Agent installation

Market existence does not grant an Agent access. Agent A's first install creates or reuses the Tenant Catalog Item and owner records, materializing a selected platform template when necessary, then creates only Agent A's installation, connection, or grants. Agent B may find that item in the Tenant Market but receives nothing until it explicitly installs it. If B installs the same item, Tool Management reuses the Tenant Catalog Item and definitions and creates only B's Agent relations.

```text
register and discover once per Tenant
                     |
                     +---- Agent A installation
                     +---- Agent B installation
                     `---- Agent C has no access
```

All installation mutations go through Tool or Capability Management. Agent cannot write Catalog, Definition, Grant, Credential, or connection tables directly. First-release Agent installation is exposed through explicitly granted Builtin capabilities such as `search_capability_market` and `install_capability`; basic allow or deny applies, while Approval remains deferred to Permission architecture.

### MCP and Agent-specific Token

One non-null Tenant MCP Catalog Item owns the Tenant-shared non-Secret server identity, normalized route, discovery revision, and related same-Tenant Tool Definitions. Each Agent uses one separate `agent_mcp_connections` row:

```text
Agent MCP Connection
  - id
  - tenant_id
  - agent_id
  - capability_catalog_item_id
  - credential_id, optional
  - non_secret_config
  - enabled
  - connected_at
  - last_tested_at
  - created_at
  - updated_at
```

`(agent_id, capability_catalog_item_id)` is unique. Credential is Agent-owned in the same Tenant and is stored once on the connection rather than repeated for every MCP Tool Grant. A connection without valid Credential remains visible as requiring authentication, but its MCP Tools do not enter the Agent's Available Tool Set. Human API-key entry or OAuth completes Credential binding without exposing Token to Agent Context.

`agent_tool_grants` relates an Agent to an actual Tool Definition. A Builtin or ordinary Tool Grant has no MCP connection. An MCP Tool Grant references the same Agent's MCP connection, and the connection's Catalog Item must own that Tool Definition. Only explicit Grants exist; listing the Market or viewing a Tool does not backfill disabled assignments.

### First and later installation

`install_capability` first searches the current-Tenant Catalog and then platform templates by stable source identity. A Tenant match creates only the current Agent relations. A platform-template match materializes or reuses one Tenant item from the validated template without external rediscovery, then creates Agent relations against that Tenant item. If neither exists, Tool Management validates the source, registers one Tenant Catalog Item, discovers and validates its definitions or package, and then creates the Agent relations.

Direct MCP sources in the first functional release require a valid HTTPS URL plus bounded connection, response size, Tool count, and schema size. Registry-backed sources retain their package identity. Failure before shared registration commits creates no partial item; failure after item registration but before Agent binding leaves a valid shared item and reports that the Agent installation did not complete.

Complete MCP egress hardening is explicitly deferred to the next security release. The first release does not guarantee DNS rebinding defense, resolved-IP private or metadata-network denial, per-redirect revalidation, or SSE-provided endpoint revalidation on every discovery, refresh, test, and Tool request. This is an accepted deployment and security risk and not evidence that arbitrary MCP endpoints are safe for untrusted production use.

### Run visibility and updates

Installation never expands a current Run. Available Tools and Workspace Skill Indexes remain fixed in Run Snapshot. A newly installed Tool, MCP connection, or Skill becomes discoverable only in a new Run.

Refreshing an MCP definition or Skill package updates the shared item once and advances its definition revision or version. Existing Runs retain their complete Tool Definitions, versioned executor bindings, resolved non-Secret route and per-Agent configuration, and authorized connection descriptors in Run Snapshot; deployment retains referenced local executor keys and decoders for non-terminal Runs. Current Catalog, Grant or Connection updates affect configuration resolution for new Runs but cannot redirect an existing Run. Human admission still uses its fixed login scope; login does not freeze the Agent's capability catalog. Skill Index discovery remains fixed for a Run, while the next explicit Skill load after controlled cache invalidation reads the current installed package; already loaded content in Model requests and History never changes retroactively. New Runs use the current Tool and Skill catalog. Agent uninstall revokes only that Agent's Grants, connection, Credential, or Workspace package as appropriate. Tenant disablement of a Catalog Item affects capability resolution for new Runs and preserves historical references. It does not trigger cancellation of existing Runs; removed resources or external failures retain their ordinary execution outcomes.

### Unified administration

Tenant administrators see one Market and installation view containing shared package identity, source, version, definitions or package metadata, installed Agents, each Agent's connection state, and Tenant enablement. Agent-specific Tokens remain isolated and masked; unified administration does not reveal one Agent's Secret to another Agent or to model-visible state.

## Alternatives considered

### Copy an MCP server and every Tool Definition per Agent

This repeats routes and schemas, makes updates inconsistent, and lets one logical package drift across Agents. Tenant registration is shared and Agent activation remains separate.

### Automatically grant every Tenant Agent an installed item

Market availability is discovery, not permission. Automatic grants would expand unrelated Agent contexts and external side-effect capability without an explicit Agent installation.

### Share one Tenant MCP Token

Agents may represent different external accounts and authorization scopes. Each Agent owns its MCP connection and Credential even when the server and definitions are shared.

### Put Tool execution, MCP route, and Skill files in the Market manifest

These facts have different owners and consumers. Market indexes them but does not replace Tool Registry, MCP execution routing, Credential, or Workspace package authority.

## Acceptance criteria

- Platform and Tenant Tool, MCP, and Skill packages are searchable through one Capability Market.
- Platform rows are discovery templates; first use materializes one Tenant item, and every Agent connection, Tool Grant, Workspace package, and Run references only same-Tenant materializations.
- One source identity creates at most one Catalog Item per Tenant scope and kind; installer identity is audit only.
- Market existence never grants an Agent access, and Agent A installation does not load the item for Agent B.
- Later Agent installation reuses existing Catalog and definitions and creates only that Agent's installation, connection, grants, and Credential relation.
- Each Agent has at most one connection to one MCP item and uses its own same-Tenant Agent-owned Credential stored once on that connection.
- Agent Tool Grants reference explicit Tool Definitions and, for MCP, the same Agent's matching MCP connection; no listing-time assignment backfill exists.
- Agent installation is available in the first release only through explicitly granted Capability Management Tools and never through direct table mutation.
- Unknown MCP sources pass the first-release HTTPS, connection, response, Tool-count, and schema-size checks before registration.
- Current Runs never discover newly installed capabilities; Tool Definition snapshots remain fixed, while controlled Skill updates use load-time freshness without immutable Skill revisions.
- Tool Definition, MCP connection and route, Credential, and Workspace Skill files retain their existing owners; Market is the discovery and installation catalog.
- Tenant administrators manage shared items and Agent installations without exposing Agent-specific Tokens.

## Risks and open questions

Agent installation from an uncurated external Skill source is an accepted first-release supply-chain risk. Package signing, publisher trust, provenance verification, malicious-instruction review, and script sandbox policy are not security guarantees of this functional release. Installation still records normalized source identity and content hash and publishes one validated package atomically, but those facts do not make an untrusted package safe. Full Skill trust policy is deferred to the next security version.

Exact Market query and ranking, external registry adapters, package signing, normalized source identity, Skill package storage, manifest schema, OAuth callback flow, MCP refresh policy, garbage collection of unused Tenant items, per-item version selection, frontend Market presentation, and next-release end-to-end MCP egress policy remain implementation decisions under the fixed sharing, isolation, and installation boundary.
