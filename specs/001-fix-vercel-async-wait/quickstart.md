# Quickstart: Verify the Vercel Async Wait Stopgap

## 1. Run Vercel Adapter tests

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_tools_typed_vercel_deploy.py
```

Verify that BUILDING produces a pending asynchronous operation, the internal poll path issues only an
exact deployment GET, READY succeeds, ERROR/CANCELED fail, and no poll repeats a deployment POST.

## 2. Run Runtime contract tests

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_agent_runtime_async_tool_poll.py \
  tests/test_agent_runtime_tool_step_service.py
```

Verify that the existing Runtime schedules the pending outcome and terminal settlement closes the
original receipt without changes to generic Runtime code.

## 3. Run scoped static checks

```bash
cd backend
.venv/bin/ruff check \
  app/services/agent_tools.py \
  tests/test_agent_tools_typed_vercel_deploy.py \
  tests/test_agent_runtime_async_tool_poll.py \
  tests/test_agent_runtime_tool_step_service.py
```

## 4. Diff boundary

Confirm that production code changes are limited to the Vercel Tool Adapter. Generic Scheduler,
Resume, LangGraph wait, terminal settlement, and other Tool files must remain unchanged.
