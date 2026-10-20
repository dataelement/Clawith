# Local sandbox permissions and failure diagnostics

## Ownership and deployment prerequisites

`SubprocessBackend.execute()` owns local execution and output-publication results.
The production image retains bubblewrap's packaged permissions instead of adding
setuid. The entrypoint still drops privileges to `clawith`. Host and container
policy must allow that user's namespace creation, including any applicable
AppArmor restrictions. A setuid bit does not resolve a namespace-policy denial
and some bubblewrap builds reject it. Administrators own host policy; execution
never changes sysctls, enables privileged containers, or retries a startup denial
without isolation.

## Model-visible diagnostic contract

The persistent execution catch and both publication catches log the exception
with traceback and boundary context via `logger.exception`. Backend logs are the
administrator diagnostic surface and may contain sensitive exception details.

Model-visible errors retain `sandbox_persistent_execution_failed` or
`sandbox_publication_unknown`, followed by the exception type (at most 64
characters) and a safe diagnostic. Details are at most 500 Unicode characters;
the complete error including prefix, type and separators is at most 700
characters. These are character limits, not byte limits. No raw exception text
crosses this trust boundary: recognized bubblewrap setuid and namespace denials
map to fixed descriptions with administrator guidance. Exact known ownership and
missing-publication-callback failures map to fixed causes. All other messages,
including empty, multiline, oversized and Unicode messages, become a fixed
"details withheld" diagnostic directing the administrator to backend logs. This
allowlist avoids relying on incomplete secret/path redaction. Because arbitrary
text is withheld rather than copied, no raw-detail truncation marker is needed.

Publication failure still means publication is unknown, returns exit code 1,
and preserves captured stdout/stderr. This diagnostic policy applies to the
error field; it does not change the existing code-output capture contract.
Execution success, session reuse, timeout reset, cancellation and cleanup retain
their existing ownership and behavior.

## Verification

From `backend/`:

```sh
uv run --extra dev pytest tests/test_sandbox_subprocess_backend.py tests/test_sandbox_execution_policy.py
uv run --extra dev ruff check app/services/sandbox/local/subprocess_backend.py tests/test_sandbox_subprocess_backend.py
uv run --extra dev pyright app
```

The focused tests drive `execute()` with an early-exit persistent process and
with failing publication in persistent and one-shot execution. They check safe
causes, boundary logging, output preservation and diagnostic bounds.

Linux deployment verification additionally requires building the backend image,
checking that `/usr/bin/bwrap` has no setuid bit, and running a minimal bwrap
command explicitly as `clawith`. Record the host namespace/AppArmor policy and
`bwrap --version`. With namespaces allowed, exercise application `execute_code`
with `print("hello")`; with namespaces denied, verify the bounded error and
backend traceback without an unsandboxed success. Mocked tests do not establish
Ubuntu/AppArmor compatibility. The reported deployment cause remains reporter
evidence until those live checks run.

### Local evidence (2026-10-20)

On macOS with Python 3.14.6, the pytest command above passed all 57 tests
(two dependency deprecation warnings). Running the new diagnostic tests against
the original `HEAD` module in memory produced 16 failures and two passing success
cases, confirming regression coverage without altering the checkout.

The Ruff command above exited 1 with 53 findings; comparison against `HEAD`
showed the same 53 findings and no additions. The Pyright command above exited 1
with 675 errors. A full comparison against an archived `HEAD` application, using
the same environment, found 675 errors in each tree and no new diagnostics.
`git diff --check` passed. These baseline failures were left outside this fix.

Linux image and application checks were not run: Docker could not connect to
`~/.colima/default/docker.sock`. No live bubblewrap version or host namespace
policy was established. `docs/testing.md` and
`.agents/skills/clawith-pre-push-checks/SKILL.md` are absent in this clone; neither
was run. No commit, push or PR was created.
