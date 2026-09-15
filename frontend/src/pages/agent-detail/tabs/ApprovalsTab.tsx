import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { fetchJson } from "../../../services/api";
import { caughtErrorMessage } from "../../../services/apiError";
import {
  parseAgentApprovalList,
  requestAgentApprovalResolution,
} from "../agentApprovalData";

export default function ApprovalsTab({
  agentId,
  canManage,
}: {
  agentId: string;
  canManage: boolean;
}) {
  const { i18n } = useTranslation();
  const queryClient = useQueryClient();
  const isChinese = i18n.language?.startsWith("zh");
  const { data: approvals = [], refetch: refetchApprovals } = useQuery({
    queryKey: ["agent-approvals", agentId],
    queryFn: () =>
      fetchJson<unknown>(`/agents/${agentId}/approvals`).then(
        parseAgentApprovalList,
      ),
    enabled: !!agentId,
    refetchInterval: 15000,
  });

  const resolveMut = useMutation({
    mutationFn: async ({
      approvalId,
      action,
    }: {
      approvalId: string;
      action: "approve" | "reject";
    }) => {
      if (!canManage) throw new Error("Approval management is not allowed");
      return requestAgentApprovalResolution({
        agentId,
        approvalId,
        action,
        request: (url, options) => fetchJson<unknown>(url, options),
      });
    },
    onSuccess: () => {
      refetchApprovals();
      queryClient.invalidateQueries({ queryKey: ["notifications-unread"] });
    },
  });

  const pending = approvals.filter((approval) => approval.status === "pending");
  const resolved = approvals.filter(
    (approval) => approval.status !== "pending",
  );
  const statusStyle = (status: string) => ({
    padding: "2px 8px",
    borderRadius: "4px",
    fontSize: "11px",
    fontWeight: 600,
    background:
      status === "approved"
        ? "rgba(0,180,120,0.12)"
        : status === "rejected"
          ? "rgba(255,80,80,0.12)"
          : "rgba(255,180,0,0.12)",
    color:
      status === "approved"
        ? "var(--success)"
        : status === "rejected"
          ? "var(--error)"
          : "var(--warning)",
  });

  return (
    <div style={{ padding: "20px 24px" }}>
      {resolveMut.error && (
        <div role="alert" style={{ color: "var(--error)", marginBottom: 12 }}>
          {caughtErrorMessage(resolveMut.error) || "Approval update failed"}
        </div>
      )}
      {pending.length > 0 && (
        <>
          <h4
            style={{
              margin: "0 0 12px",
              fontSize: "13px",
              color: "var(--warning)",
            }}
          >
            {isChinese
              ? `${pending.length} 个待审批`
              : `${pending.length} Pending`}
          </h4>
          {pending.map((approval) => (
            <div
              key={approval.id}
              style={{
                padding: "14px 16px",
                marginBottom: "8px",
                borderRadius: "8px",
                background: "var(--bg-secondary)",
                border: "1px solid var(--border-subtle)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  marginBottom: "8px",
                }}
              >
                <span style={statusStyle(approval.status)}>
                  {approval.status}
                </span>
                <span style={{ fontSize: "13px", fontWeight: 500 }}>
                  {approval.action_type}
                </span>
                <span style={{ flex: 1 }} />
                <span
                  style={{ fontSize: "11px", color: "var(--text-tertiary)" }}
                >
                  {approval.created_at
                    ? new Date(approval.created_at).toLocaleString()
                    : ""}
                </span>
              </div>
              {approval.details !== null && approval.details !== undefined && (
                <div
                  style={{
                    fontSize: "12px",
                    color: "var(--text-secondary)",
                    marginBottom: "10px",
                    lineHeight: "1.5",
                    maxHeight: "80px",
                    overflow: "hidden",
                  }}
                >
                  {typeof approval.details === "string"
                    ? approval.details
                    : JSON.stringify(approval.details, null, 2)}
                </div>
              )}
              {canManage && (
                <div
                  style={{
                    display: "flex",
                    gap: "8px",
                    justifyContent: "flex-end",
                  }}
                >
                  <button
                    className="btn btn-primary"
                    style={{ padding: "6px 16px", fontSize: "12px" }}
                    onClick={() => {
                      if (canManage)
                        resolveMut.mutate({
                          approvalId: approval.id,
                          action: "approve",
                        });
                    }}
                    disabled={!canManage || resolveMut.isPending}
                  >
                    {isChinese ? "批准" : "Approve"}
                  </button>
                  <button
                    className="btn btn-danger"
                    style={{ padding: "6px 16px", fontSize: "12px" }}
                    onClick={() => {
                      if (canManage)
                        resolveMut.mutate({
                          approvalId: approval.id,
                          action: "reject",
                        });
                    }}
                    disabled={!canManage || resolveMut.isPending}
                  >
                    {isChinese ? "拒绝" : "Reject"}
                  </button>
                </div>
              )}
            </div>
          ))}
          <div
            style={{
              borderTop: "1px solid var(--border-subtle)",
              margin: "16px 0",
            }}
          />
        </>
      )}

      <h4
        style={{
          margin: "0 0 12px",
          fontSize: "13px",
          color: "var(--text-secondary)",
        }}
      >
        {isChinese ? "审批历史" : "History"}
      </h4>
      {resolved.length === 0 && pending.length === 0 && (
        <div
          style={{
            textAlign: "center",
            padding: "40px",
            color: "var(--text-tertiary)",
            fontSize: "13px",
          }}
        >
          {isChinese ? "暂无审批记录" : "No approval records"}
        </div>
      )}
      {resolved.map((approval) => (
        <div
          key={approval.id}
          style={{
            padding: "12px 16px",
            marginBottom: "6px",
            borderRadius: "8px",
            background: "var(--bg-secondary)",
            border: "1px solid var(--border-subtle)",
            opacity: 0.7,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={statusStyle(approval.status)}>{approval.status}</span>
            <span style={{ fontSize: "12px" }}>{approval.action_type}</span>
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: "10px", color: "var(--text-tertiary)" }}>
              {approval.resolved_at
                ? new Date(approval.resolved_at).toLocaleString()
                : ""}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
