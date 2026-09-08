# Agent Note: Login-Session Authorization

Status: proposed — login-scoped authorization and fixed 24-hour expiry are user-confirmed; product-entry implementation remains for G006.

## Problem

The earlier target design revalidates authorization generations during execution and cancels dependent Runs after permission changes. The first release instead needs authorization resolved before execution, with permissions retained during a login session and refreshed on a subsequent login.

## Proposal

Auth owns the login session and its validity. Permission resolves human caller authorization at login: Account, Membership, Tenant, role and admitted Agent access. Backend entrypoints consume that authenticated scope; an identifier supplied by a caller cannot expand it or cross its Tenant boundary. A login session is distinct from a conversational Session. Login does not load or freeze the complete Tool/MCP catalog, Workspace content or Skill packages; the human access representation and its reads must remain bounded.

Human-initiated Runs use the human authorization captured by the login session. Before each new Run, the owning intake/composition resolves the selected Agent's current Model settings, Tool/MCP bindings, Workspace scope and discovery indexes within that authorization, then fixes the execution configuration in Run Snapshot. A capability installed during an earlier Run can therefore become available in a new Run without another human login. The active Run does not expand its Tool set or discovery indexes; existing Skill packages retain their separately agreed load-time freshness.

Runner and Agent Loop do not authenticate the human again, poll current permission changes, or own permission policy. The first release omits authorization generations, the `run_authorization_dependencies` projection, revocation sweeps, and automatic Run cancellation caused by permission changes. Changes to human authorization are resolved on the next login rather than retroactively changing the current login session. Resolving Agent configuration for a new Run is distinct from refreshing the human's authorization; an existing Run retains its own Snapshot.

Heartbeat, Trigger, and A2A execution without a human login session resolve their own authorized scope before starting. A2A retains independent receiver authorization and only explicitly delegated inputs; Subagent Runs inherit their Parent Main Run scope. These executions likewise do not monitor permission changes while running.

Human login sessions expire 24 hours after issuance, with no sliding extension or automatic renewal. Expired users must log in again. Human input, Waiting replies and cancellation validate the current login and its captured authorization at the product entry. Existing Running Runs continue and Waiting Runs remain waiting; neither expiry, logout nor closing the client cancels them. After a new login, a user who still has access may answer and resume the same Waiting Run without changing its captured execution authorization. Authenticated WebSockets close at expiry and reconnect after login using committed-message cursors. Login expiry is not another Run Status or a whole-Run timeout.

Human login grants access to the platform, not a lifetime for Agent operation. Goal, Trigger and Heartbeat operate under their resolved scopes without requiring an online human or valid ongoing human login. Platform crash recovery is separate from login handling.

A fixed authorization scope does not guarantee resource availability. Deleted files, Tools, Agents, or Credentials and external credential rejection may produce ordinary owned missing-resource, unavailable-resource, or execution errors. The platform does not recreate deleted resources or retain unusable Secret material to simulate continued availability. Secret protection and Tenant isolation remain required.

This decision supersedes live permission revalidation, generation-based invalidation, and revocation-driven Run cancellation in the earlier identity, Permission, Credential, Tool, Workspace, Context, Runner, and target-architecture Notes and their implementation plans. Those superseded mechanisms are not first-release implementation requirements. Explicit Run cancellation and parent-to-child terminal cancellation remain unchanged. Detailed administrator exceptions remain for Permission implementation.

## Alternatives considered

### Revalidate permission generations before every protected operation and Model Step

Rejected for the first release because it requires dependency tracking and cancellation coordination for permission changes during active use. Login-scoped authorization follows the accepted simpler product behavior.

### Leave login sessions without expiry

Rejected because the accepted login boundary includes fixed 24-hour expiry and subsequent authorization refresh.

## Acceptance criteria

- Authorization is resolved before execution; Runner consumes the resolved scope.
- Human authorization is retained within its login session and refreshed on subsequent login.
- Each new Run resolves current Agent-owned execution configuration under that human scope; new installations may be used in a new Run without refreshing the login session. Login does not materialize a complete capability catalog.
- Login sessions expire after 24 hours without sliding or automatic renewal; expiry closes human WebSockets but does not cancel Running or Waiting Runs.
- The first release has no live authorization-generation checks, dependency projection, or revocation cancellation sweep.
- Login validity, resource availability, Secret handling, and explicit Run cancellation retain their respective owners.
- Tests and schema gates must be aligned with this decision before the affected owner is implemented; this Note is not runtime evidence.
