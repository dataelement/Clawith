import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  parseAgentApprovalList,
  requestAgentApprovalResolution,
} from "../src/pages/agent-detail/agentApprovalData.ts";

const approvalsTabSource = readFileSync(
  new URL("../src/pages/agent-detail/tabs/ApprovalsTab.tsx", import.meta.url),
  "utf8",
);

test("agent approval responses are validated before use", () => {
  assert.deepEqual(
    parseAgentApprovalList([
      {
        id: "approval-1",
        status: "pending",
        action_type: "send_message",
        details: { channel: "external" },
        created_at: "2026-08-26T12:00:00Z",
        resolved_at: null,
      },
    ]),
    [
      {
        id: "approval-1",
        status: "pending",
        action_type: "send_message",
        details: { channel: "external" },
        created_at: "2026-08-26T12:00:00Z",
        resolved_at: null,
      },
    ],
  );
  assert.throws(() =>
    parseAgentApprovalList([{ id: "approval-1", status: "unknown" }]),
  );
});

test("401, 403, and 500 approval failures never reach success", async () => {
  let successCount = 0;
  for (const status of [401, 403, 500]) {
    const promise = requestAgentApprovalResolution({
      agentId: "agent-1",
      approvalId: "approval-1",
      action: "approve",
      request: async () => {
        throw Object.assign(new Error(`HTTP ${status}`), { status });
      },
    }).then((result) => {
      successCount += 1;
      return result;
    });
    await assert.rejects(promise, (error) => error.status === status);
  }
  assert.equal(successCount, 0);
});

test("validated approval success reaches success exactly once", async () => {
  const calls = [];
  let successCount = 0;
  const result = await requestAgentApprovalResolution({
    agentId: "agent / 1",
    approvalId: "approval / 1",
    action: "reject",
    request: async (url, options) => {
      calls.push({ url, options });
      return {
        id: "approval-1",
        status: "rejected",
        resolved_at: "2026-08-26T12:30:00Z",
      };
    },
  }).then((value) => {
    successCount += 1;
    return value;
  });

  assert.deepEqual(result, {
    id: "approval-1",
    status: "rejected",
    resolved_at: "2026-08-26T12:30:00Z",
  });
  assert.equal(successCount, 1);
  assert.deepEqual(calls, [
    {
      url: "/agents/agent%20%2F%201/approvals/approval%20%2F%201/resolve",
      options: {
        method: "POST",
        body: JSON.stringify({ action: "reject" }),
      },
    },
  ]);
});

test("ApprovalsTab does not bypass the canonical request boundary", () => {
  assert.doesNotMatch(approvalsTabSource, /\bfetch\s*\(/);
  assert.match(approvalsTabSource, /fetchJson<unknown>/);
  assert.match(approvalsTabSource, /requestAgentApprovalResolution/);
});
