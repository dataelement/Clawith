# G004 committed architecture acceptance

Verdict: PASS for the committed execution-dependency architecture at `3083dba17c9ce2900a7dac87587d8d3c8d311c15`; ready for G005 Run/Context implementation, not product or deployment acceptance.

## Independent review

The cumulative code/security review identified two blockers. Both have independent code and architecture approval after their regression tests were added:

- S3 cached-client initialization could create two clients and retain only one. Initialization and detachment now share an instance lock. All synchronous SDK operations drain their workers on cancellation; GET body ownership spans read and cleanup. Native SDK tests cover first-read contention, HEAD/list cancellation, initialization failure and body/pool disposal.
- Model configuration probes imposed a 256-token output cap while retaining larger thinking budgets. Probes now preserve the resolved output allowance and reasoning settings. Four-protocol regressions exercise both reasoning configurations and small output limits through actual request encoding and public configuration acceptance.

The architecture review found no remaining committed dependency-chain blocker. Model, Tool, Workspace, Market, Audit and application resource ownership remain distinct. Earlier Notes now distinguish implemented provisioning/resource assembly from pending Run consumers.

## Reproducible evidence

Tests ran against a detached checkout of the committed source, not the original dirty worktree. The application import location was checked before execution. The primary development environment was restored and the temporary checkout removed after verification.

- [G004 owner prerequisites](owner-contract-check.txt): passed with all declared approval receipts.
- [G004 integration](execution-dependency-tests.txt): 205 passed.
- Full committed Backend: 2301 passed, 4 deprecation warnings.
- G002 architecture: 1748 passed; collection: 2301 tests.
- G003 foundation schema/integration: 211 passed; contract prerequisites passed.
- G000/G001 manifest, disposition, roster, governance, load-profile configuration and immutable-reference validations: passed.
- Ruff `app tests` and configured Pyright `app`: passed.

## Exclusions and handoff

GitHub/ClawHub import fixes remain explicitly deferred by the user. Uncommitted `market_tools.py`, `skill_sources.py`, their tests and related Tool/Market installation-port changes were excluded from this committed-source acceptance. They are retained in the primary worktree and are not application startup dependencies. The frontend prototype is unrelated and untouched.

G005 must implement the approved Run/History and Context consumers, Waiting/resume, asynchronous Task acceptance, fair execution scheduling and terminal cleanup. It must drain consumers before application resource disposal. No G005 code or E2E capability is asserted by this G004 record.

Hosted CI, live Provider/S3 compatibility, source-import acceptance, product HTTP/WebSocket E2E, migrations, frontend behavior and 50-Agent load remain unverified here. A valid load-profile configuration is not a successful load test.
