type ApprovalStatus = "pending" | "approved" | "rejected";

export interface AgentApproval {
  id: string;
  status: ApprovalStatus;
  action_type: string;
  details: unknown;
  created_at: string | null;
  resolved_at: string | null;
}

export interface AgentApprovalResolution {
  id: string;
  status: "approved" | "rejected";
  resolved_at: string | null;
}

type ApprovalRequest = (url: string, options: RequestInit) => Promise<unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: Record<string, unknown>, key: string): string {
  const field = value[key];
  if (typeof field !== "string") throw new Error(`Invalid approval ${key}`);
  return field;
}

function nullableString(
  value: Record<string, unknown>,
  key: string,
): string | null {
  const field = value[key];
  if (field === null) return null;
  if (typeof field !== "string") throw new Error(`Invalid approval ${key}`);
  return field;
}

function approvalStatus(value: unknown): ApprovalStatus {
  if (value !== "pending" && value !== "approved" && value !== "rejected") {
    throw new Error("Invalid approval status");
  }
  return value;
}

export function parseAgentApprovalList(value: unknown): AgentApproval[] {
  if (!Array.isArray(value)) throw new Error("Invalid approvals response");
  return value.map((approval) => {
    if (!isRecord(approval)) throw new Error("Invalid approval response");
    return {
      id: requiredString(approval, "id"),
      status: approvalStatus(approval.status),
      action_type: requiredString(approval, "action_type"),
      details: approval.details,
      created_at: nullableString(approval, "created_at"),
      resolved_at: nullableString(approval, "resolved_at"),
    };
  });
}

export function parseAgentApprovalResolution(
  value: unknown,
): AgentApprovalResolution {
  if (!isRecord(value)) throw new Error("Invalid approval resolution response");
  const status = approvalStatus(value.status);
  if (status === "pending") {
    throw new Error("Invalid approval resolution status");
  }
  return {
    id: requiredString(value, "id"),
    status,
    resolved_at: nullableString(value, "resolved_at"),
  };
}

export async function requestAgentApprovalResolution({
  agentId,
  approvalId,
  action,
  request,
}: {
  agentId: string;
  approvalId: string;
  action: "approve" | "reject";
  request: ApprovalRequest;
}): Promise<AgentApprovalResolution> {
  const payload = await request(
    `/agents/${encodeURIComponent(agentId)}/approvals/${encodeURIComponent(approvalId)}/resolve`,
    {
      method: "POST",
      body: JSON.stringify({ action }),
    },
  );
  return parseAgentApprovalResolution(payload);
}
