# Agent Note: Application-owned execution resources

Status: implemented — the single application lifespan composes execution services with tested initialization and cleanup boundaries.

## Problem

Separately tested execution services need a real application owner for their clients, configuration and cleanup. Sharing a business pool with nested S3 advisory locks can prevent the transaction that publishes a package from obtaining a connection.

## Decision

The application constructs native Runtime after the execution services, completes its startup interruption sweep before publication, and closes it before HTTP, storage, Audit and database disposal. A typed optional outcome consumer supplies product-owned transactional settlement; the core E2E uses a fixture consumer without implementing G006 product APIs. Controlled snapshot capture accepts preauthorized owner views and resolves fixed Tool/Skill/Memory inputs through their public owners.

The existing FastAPI factory remains the only entry. Its lifespan requires explicit `EXECUTION` configuration, creates the existing database resources and Audit consumer, then enters `open_execution_resources`. Workspace, Market and Model receive the same execution session factory and application-owned stateless HTTP client where needed. Tool and Credential services are constructed for a supplied transaction; the Credential resolver closes its own transaction before returning a Secret to external execution.

Workspace source availability forwards to the single Market instance through a typed closure and the original transaction. This construction order introduces no reverse owner dependency, private-property mutation, duplicate Market or service locator. Model-visible Tool bindings still require a real Run scope and are assembled by their consumer, not invented during application startup.

Local and S3 storage remain infrastructure adapters. S3 locks use a separate bounded engine with an explicitly configured target-database DSN and session-pinned connections. The resource constructor alone may import concrete storage; individual Tool adapters cannot bypass Workspace.

An AsyncExitStack owns each successfully constructed resource immediately. Partial initialization and shutdown failures still close subsequent resources. Execution HTTP/storage and lock resources close before Audit drains and business databases dispose. App-state references are removed on exit. Consumers must be drained before resource closure; this phase adds no background Runner or second execution lifecycle.

## Configuration

`EXECUTION` is a JSON environment setting parsed by the target Settings owner. `credential_keys` and `continuation_keys` each require `active_version` and a mapping of base64-encoded 32-byte keys. They have no generated defaults or implicit derivation; older versions must remain configured while stored ciphertext references them. `storage` selects an explicit absolute Local root or an S3 bucket/prefix, region, static or ambient authentication, and dedicated lock-pool configuration. HTTP connection-count bounds belong to application configuration. Model/MCP request deadlines remain with their existing owners; application configuration exposes no timeout fields that their explicit requests would override.

The ASGI module can be imported without secrets, but starting its lifespan without execution configuration fails before database creation. This is not a feature switch or fallback operating mode. Startup does not create schema, provision grants, migrate data or repair old configuration.

## Alternatives considered

A second application factory would divide lifecycle ownership. Reusing the business pool for advisory locks can exhaust the connections needed to publish protected facts. A second Market instance or private-field reassignment is unnecessary when a typed forwarding closure preserves one owner. Generated encryption keys could make existing encrypted state unreadable after restart.

## Consequences

Starting the backend requires deployment keys and storage configuration even while HTTP routing remains health-only. Application configuration does not expose ineffective global timeout overrides; existing capability owners retain request deadlines. G005 must register consumer draining before these execution resources close.

## Verification and deferred work

Application integration tests obtain real services through app state and exercise Credential encryption/resolution, controlled Model validation, explicit Builtin grants, Workspace writes and Market-backed Skill discovery. Failure tests cover each construction stage and storage-close errors; S3 lock tests observe independent checked-out connections and pool disposal. No external Provider or S3 request is required for these tests.

Resource/configuration tests passed 45 cases; together with application and import-boundary tests the focused suite passed 177 cases. Ruff and configured Pyright passed. Independent code and architecture reviews found no remaining blocker for this resource-assembly slice.

Runtime E2E, summary integration, application-lifecycle and resource tests passed 55 checks. The E2E verifies the actual written Workspace file and the fixture owner's committed result, not only the model's final text. Lifecycle-only tests substitute a controlled Runtime worker to isolate cleanup order; actual Run behavior is exercised separately with PostgreSQL and controlled HTTP.

GitHub/ClawHub importing remains explicitly deferred by the user. Its uncommitted source drafts are not mounted by this resource assembly or counted as accepted. G005 provides real Run/Loop consumers; product APIs, deployment migrations and 50-Agent performance acceptance remain separate work.
