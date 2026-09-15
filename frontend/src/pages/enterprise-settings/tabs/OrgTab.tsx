import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { caughtErrorMessage } from "../../../services/apiError";
import { useDialog } from "../../../components/Dialog/DialogContext";
import { useToast } from "../../../components/Toast/ToastContext";
import LinearCopyButton from "../../../components/LinearCopyButton";
import { IconSettings } from "@tabler/icons-react";
import { fetchJson } from "../utils/fetchJson";
import type { Tenant } from "../../../services/apiContracts";
import {
  parseAuthorizationUrl,
  parseIdentityProvider,
  parseIdentityProviders,
  parseOrgDepartments,
  parseOrgMembers,
  parseOrgSyncResponse,
  type IdentityProvider,
  type IdentityProviderConfig,
  type OrgDepartment,
} from "../utils/responseParsers";

interface IdentityProviderForm {
  provider_type: string;
  name: string;
  config: IdentityProviderConfig;
  app_id: string;
  app_secret: string;
  authorize_url: string;
  token_url: string;
  user_info_url: string;
  scope: string;
}

interface OrgSyncResult {
  providerId: string;
  departments?: number;
  members?: number;
  errors?: string[];
  error?: string;
}

function hasMessageType(value: unknown, type: string): boolean {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    value.type === type
  );
}

const FEISHU_SYNC_PERM_JSON = `{
  "scopes": {
    "tenant": [
      "contact:contact.base:readonly",
      "contact:department.base:readonly",
      "contact:user.base:readonly",
      "contact:user.employee_id:readonly"
    ],
    "user": []
  }
}`;

// ─── Department Tree ───────────────────────────────
function DeptTree({
  departments,
  parentId,
  selectedDept,
  onSelect,
  level,
}: {
  departments: OrgDepartment[];
  parentId: string | null;
  selectedDept: string | null;
  onSelect: (id: string | null) => void;
  level: number;
}) {
  const children = departments.filter((d) =>
    parentId === null ? !d.parent_id : d.parent_id === parentId,
  );
  if (children.length === 0) return null;
  return (
    <>
      {children.map((d) => (
        <div key={d.id}>
          <div
            style={{
              padding: "5px 8px",
              paddingLeft: `${8 + level * 16}px`,
              borderRadius: "4px",
              cursor: "pointer",
              fontSize: "13px",
              marginBottom: "1px",
              background:
                selectedDept === d.id
                  ? "rgba(224,238,238,0.12)"
                  : "transparent",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
            onClick={() => onSelect(d.id)}
          >
            <div>
              <span
                style={{
                  color: "var(--text-tertiary)",
                  marginRight: "4px",
                  fontSize: "11px",
                }}
              >
                {departments.some((c) => c.parent_id === d.id) ? "▾" : "·"}
              </span>
              {d.name}
            </div>
            {d.member_count !== undefined && (
              <span style={{ fontSize: "10px", color: "var(--text-tertiary)" }}>
                {d.member_count}
              </span>
            )}
          </div>
          <DeptTree
            departments={departments}
            parentId={d.id}
            selectedDept={selectedDept}
            onSelect={onSelect}
            level={level + 1}
          />
        </div>
      ))}
    </>
  );
}

// ─── SSO Channel Section ────────────────────────────────
function SsoChannelSection({
  idpType,
  existingProvider,
  tenant,
  t,
}: {
  idpType: string;
  existingProvider?: IdentityProvider;
  tenant?: Tenant;
  t: TFunction;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [liveDomainOverride, setLiveDomainOverride] = useState<string | null>(
    null,
  );
  const [ssoError, setSsoError] = useState<string>("");
  const [toggling, setToggling] = useState(false);

  const ssoEnabled = existingProvider
    ? !!existingProvider.sso_login_enabled
    : false;
  const domain =
    liveDomainOverride ??
    existingProvider?.sso_domain ??
    tenant?.sso_domain ??
    "";
  const callbackUrl = domain
    ? domain.startsWith("http")
      ? `${domain}/api/auth/${idpType}/callback`
      : `https://${domain}/api/auth/${idpType}/callback`
    : "";

  const handleSsoToggle = async () => {
    if (!existingProvider) {
      toast.warning(
        t(
          "enterprise.identity.saveFirst",
          "Please save the configuration first to enable SSO.",
        ),
      );
      return;
    }
    const newVal = !ssoEnabled;
    setToggling(true);
    setSsoError("");
    try {
      const result = parseIdentityProvider(
        await fetchJson<unknown>(
          `/enterprise/identity-providers/${existingProvider.id}`,
          {
            method: "PUT",
            body: JSON.stringify({ sso_login_enabled: newVal }),
          },
        ),
      );
      if (result.sso_domain) setLiveDomainOverride(result.sso_domain);
      qc.invalidateQueries({ queryKey: ["identity-providers"] });
      if (tenant?.id) qc.invalidateQueries({ queryKey: ["tenant", tenant.id] });
    } catch (error) {
      const msg = caughtErrorMessage(error) || "";
      if (msg.includes("IP address") || msg.includes("multi-tenant")) {
        setSsoError(
          t(
            "enterprise.identity.ssoIpConflict",
            "IP 模式下只能有一个企业开启 SSO，当前已有其他企业占用。",
          ),
        );
      } else {
        setSsoError(
          msg ||
            t("enterprise.identity.ssoToggleFailed", "Failed to toggle SSO"),
        );
      }
    } finally {
      setToggling(false);
    }
  };

  return (
    <div
      style={{
        marginTop: "20px",
        paddingTop: "20px",
        borderTop: "1px dashed var(--border-subtle)",
      }}
    >
      {/* SSO Toggle */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: ssoError ? "8px" : "16px",
        }}
      >
        <div>
          <div style={{ fontWeight: 500, fontSize: "13px" }}>
            {t("enterprise.identity.ssoLoginToggle", "SSO Login")}
          </div>
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-tertiary)",
              marginTop: "2px",
            }}
          >
            {t(
              "enterprise.identity.ssoLoginToggleHint",
              "Allow users to log in via this identity provider.",
            )}
          </div>
        </div>
        <label
          style={{
            position: "relative",
            display: "inline-block",
            width: "36px",
            height: "20px",
            flexShrink: 0,
            opacity: existingProvider && !toggling ? 1 : 0.5,
          }}
        >
          <input
            type="checkbox"
            checked={ssoEnabled}
            onChange={handleSsoToggle}
            disabled={!existingProvider || toggling}
            style={{ opacity: 0, width: 0, height: 0 }}
          />
          <span
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              borderRadius: "20px",
              cursor: existingProvider && !toggling ? "pointer" : "not-allowed",
              background: ssoEnabled
                ? "var(--accent-primary)"
                : "var(--border-subtle)",
              transition: "0.2s",
            }}
          >
            <span
              style={{
                position: "absolute",
                left: ssoEnabled ? "18px" : "2px",
                top: "2px",
                width: "16px",
                height: "16px",
                borderRadius: "50%",
                background: "#fff",
                transition: "0.2s",
                boxShadow: "0 1px 2px rgba(0,0,0,0.1)",
              }}
            />
          </span>
        </label>
      </div>
      {ssoError && (
        <div
          style={{
            fontSize: "12px",
            color: "var(--error)",
            marginBottom: "12px",
            padding: "6px 10px",
            background: "rgba(var(--error-rgb,220,38,38),0.08)",
            borderRadius: "6px",
          }}
        >
          {ssoError}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        <div>
          <label
            className="form-label"
            style={{
              fontSize: "11px",
              marginBottom: "4px",
              color: "var(--text-secondary)",
            }}
          >
            {t("enterprise.identity.ssoSubdomain", "SSO Login URL")}
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div
              style={{
                flex: 1,
                maxWidth: "400px",
                padding: "8px 12px",
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "6px",
                fontSize: "12px",
                color: domain ? "var(--text-primary)" : "var(--text-tertiary)",
                fontFamily: "monospace",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {domain
                ? domain.startsWith("http")
                  ? domain
                  : `https://${domain}`
                : t(
                    "enterprise.identity.ssoUrlEmpty",
                    "请先开启 SSO 以生成地址",
                  )}
            </div>
            <LinearCopyButton
              className="btn btn-ghost btn-sm"
              style={{
                fontSize: "11px",
                width: "auto",
                minWidth: "70px",
                height: "33px",
              }}
              disabled={!domain}
              textToCopy={
                domain
                  ? domain.startsWith("http")
                    ? domain
                    : `https://${domain}`
                  : ""
              }
              label={t("common.copy", "Copy")}
              copiedLabel="Copied"
            />
          </div>
          <div
            style={{
              fontSize: "10px",
              color: "var(--text-tertiary)",
              marginTop: "4px",
            }}
          >
            {t(
              "enterprise.identity.ssoSubdomainHint",
              "Share this URL with your team. SSO login buttons will appear when they visit this address.",
            )}
          </div>
        </div>
        <div>
          <label
            className="form-label"
            style={{
              fontSize: "11px",
              marginBottom: "4px",
              color: "var(--text-secondary)",
            }}
          >
            {t(
              "enterprise.identity.callbackUrl",
              "Redirect URL (paste this in your app settings)",
            )}
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div
              style={{
                flex: 1,
                maxWidth: "400px",
                padding: "8px 12px",
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "6px",
                fontSize: "12px",
                color: callbackUrl
                  ? "var(--text-primary)"
                  : "var(--text-tertiary)",
                fontFamily: "monospace",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {callbackUrl ||
                t("enterprise.identity.ssoUrlEmpty", "请先开启 SSO 以生成地址")}
            </div>
            <LinearCopyButton
              className="btn btn-ghost btn-sm"
              style={{
                fontSize: "11px",
                width: "auto",
                minWidth: "70px",
                height: "33px",
              }}
              disabled={!callbackUrl}
              textToCopy={callbackUrl}
              label={t("common.copy", "Copy")}
              copiedLabel="Copied"
            />
          </div>
          <div
            style={{
              fontSize: "10px",
              color: "var(--text-tertiary)",
              marginTop: "4px",
            }}
          >
            {t(
              "enterprise.identity.callbackUrlHint",
              "Add this URL as the OAuth redirect URI in your identity provider's app configuration.",
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Org & Identity Tab ─────────────────────────────
export default function OrgTab({ tenant }: { tenant?: Tenant }) {
  const { t } = useTranslation();
  const dialog = useDialog();
  const qc = useQueryClient();

  const [syncing, setSyncing] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<OrgSyncResult | null>(null);
  const [memberSearch, setMemberSearch] = useState("");
  const [selectedDept, setSelectedDept] = useState<string | null>(null);
  const [expandedType, setExpandedType] = useState<string | null>(null);
  const [savingProvider, setSavingProvider] = useState(false);
  const [saveProviderOk, setSaveProviderOk] = useState(false);

  // Identity Providers state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [useOAuth2Form, setUseOAuth2Form] = useState(false);
  const [form, setForm] = useState<IdentityProviderForm>({
    provider_type: "feishu",
    name: "",
    config: {},
    app_id: "",
    app_secret: "",
    authorize_url: "",
    token_url: "",
    user_info_url: "",
    scope: "openid profile email",
  });

  const currentTenantId = localStorage.getItem("current_tenant_id") || "";

  // Queries
  const { data: providers = [] } = useQuery({
    queryKey: ["identity-providers", currentTenantId],
    queryFn: () =>
      fetchJson<unknown>(
        `/enterprise/identity-providers${currentTenantId ? `?tenant_id=${currentTenantId}` : ""}`,
      ).then(parseIdentityProviders),
  });

  const { data: departmentsData = { items: [], total_member: 0 } } = useQuery({
    queryKey: ["org-departments", currentTenantId, editingId],
    queryFn: () => {
      const params = new URLSearchParams();
      if (currentTenantId) params.set("tenant_id", currentTenantId);
      if (editingId) params.set("provider_id", editingId);
      return fetchJson<unknown>(`/enterprise/org/departments?${params}`).then(
        parseOrgDepartments,
      );
    },
    enabled: !!editingId,
  });

  const { data: members = [] } = useQuery({
    queryKey: [
      "org-members",
      selectedDept,
      memberSearch,
      currentTenantId,
      editingId,
    ],
    queryFn: () => {
      const params = new URLSearchParams();
      if (selectedDept) params.set("department_id", selectedDept);
      if (memberSearch) params.set("search", memberSearch);
      if (currentTenantId) params.set("tenant_id", currentTenantId);
      if (editingId) params.set("provider_id", editingId);
      return fetchJson<unknown>(`/enterprise/org/members?${params}`).then(
        parseOrgMembers,
      );
    },
    enabled: !!editingId,
  });

  // Mutations
  const addProvider = useMutation({
    mutationFn: async (data: IdentityProviderForm): Promise<void> => {
      const payload = { ...data, tenant_id: currentTenantId, is_active: true };
      if (data.provider_type === "oauth2" && useOAuth2Form) {
        await fetchJson<unknown>("/enterprise/identity-providers/oauth2", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        return;
      }
      await fetchJson<unknown>("/enterprise/identity-providers", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["identity-providers"] });
      setUseOAuth2Form(false);
      setSavingProvider(false);
      setSaveProviderOk(true);
      setTimeout(() => setSaveProviderOk(false), 2500);
    },
    onError: () => setSavingProvider(false),
  });

  const updateProvider = useMutation({
    mutationFn: async ({
      id,
      data,
    }: {
      id: string;
      data: IdentityProviderForm;
    }): Promise<void> => {
      if (data.provider_type === "oauth2" && useOAuth2Form) {
        await fetchJson<unknown>(
          `/enterprise/identity-providers/${id}/oauth2`,
          {
            method: "PATCH",
            body: JSON.stringify(data),
          },
        );
        return;
      }
      await fetchJson<unknown>(`/enterprise/identity-providers/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["identity-providers"] });
      setUseOAuth2Form(false);
      setSavingProvider(false);
      setSaveProviderOk(true);
      setTimeout(() => setSaveProviderOk(false), 2500);
    },
    onError: () => setSavingProvider(false),
  });

  const deleteProvider = useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await fetchJson<unknown>(`/enterprise/identity-providers/${id}`, {
        method: "DELETE",
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["identity-providers"] }),
  });

  const triggerSync = async (providerId: string) => {
    setSyncing(providerId);
    setSyncResult(null);
    try {
      const result = parseOrgSyncResponse(
        await fetchJson<unknown>(
          `/enterprise/org/sync?provider_id=${providerId}`,
          { method: "POST" },
        ),
      );
      setSyncResult({ ...result, providerId });
      // Force refetch to ensure UI updates after sync
      await qc.invalidateQueries({ queryKey: ["org-departments"] });
      await qc.invalidateQueries({ queryKey: ["org-members"] });
      await qc.invalidateQueries({ queryKey: ["identity-providers"] });
    } catch (error) {
      setSyncResult({ error: caughtErrorMessage(error), providerId });
    }
    setSyncing(null);
  };

  const initOAuth2FromConfig = (config: IdentityProviderConfig) => ({
    app_id: config?.app_id || config?.client_id || "",
    app_secret: config?.app_secret || config?.client_secret || "",
    authorize_url: config?.authorize_url || "",
    token_url: config?.token_url || "",
    user_info_url: config?.user_info_url || "",
    scope: config?.scope || "openid profile email",
  });

  const save = () => {
    setSavingProvider(true);
    setSaveProviderOk(false);
    if (editingId) {
      updateProvider.mutate({ id: editingId, data: form });
    } else {
      addProvider.mutate(form);
    }
  };

  const handleGoogleAdminAuthorize = async (providerId: string) => {
    const authorizationUrl = parseAuthorizationUrl(
      await fetchJson<unknown>(
        `/enterprise/identity-providers/${providerId}/google-workspace-sync/authorize-url`,
      ),
    );
    const popup = window.open(
      authorizationUrl,
      "google-workspace-sync",
      "width=640,height=760",
    );
    if (!popup) {
      window.location.assign(authorizationUrl);
      return;
    }

    const onMessage = (event: MessageEvent<unknown>) => {
      if (hasMessageType(event.data, "google-workspace-sync-authorized")) {
        window.removeEventListener("message", onMessage);
        qc.invalidateQueries({ queryKey: ["identity-providers"] });
      }
    };
    window.addEventListener("message", onMessage);
  };

  const IDP_TYPES = [
    {
      type: "feishu",
      name: "Feishu",
      desc: "Feishu / Lark Integration",
      icon: <img src="/feishu.png" width="20" height="20" alt="Feishu" />,
    },
    {
      type: "wecom",
      name: "WeCom",
      desc: "WeChat Work Integration",
      icon: (
        <img
          src="/wecom.png"
          width="20"
          height="20"
          style={{ borderRadius: "4px" }}
          alt="WeCom"
        />
      ),
    },
    {
      type: "dingtalk",
      name: "DingTalk",
      desc: "DingTalk App Integration",
      icon: (
        <img
          src="/dingtalk.png"
          width="20"
          height="20"
          style={{ borderRadius: "4px" }}
          alt="DingTalk"
        />
      ),
    },
    {
      type: "google_workspace",
      name: "Google",
      desc: "Google Admin Directory Sync",
      icon: <img src="/google.svg" width="20" height="20" alt="Google" />,
    },
    {
      type: "oauth2",
      name: "OAuth2",
      desc: "Generic OIDC Provider",
      icon: (
        <div
          style={{
            width: 20,
            height: 20,
            background: "var(--accent-primary)",
            borderRadius: 4,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#fff",
            fontSize: 10,
            fontWeight: 700,
          }}
        >
          O
        </div>
      ),
    },
  ];

  const handleExpand = (type: string, existingProvider?: IdentityProvider) => {
    if (expandedType === type) {
      setExpandedType(null);
      return;
    }
    setExpandedType(type);
    setEditingId(existingProvider ? existingProvider.id : null);
    setUseOAuth2Form(type === "oauth2");

    if (existingProvider) {
      setForm({
        ...existingProvider,
        ...(type === "oauth2"
          ? initOAuth2FromConfig(existingProvider.config)
          : {}),
      });
    } else {
      const defaults: Record<string, IdentityProviderConfig> = {
        feishu: { app_id: "", app_secret: "", corp_id: "" },
        dingtalk: { app_key: "", app_secret: "", corp_id: "" },
        wecom: {
          corp_id: "",
          secret: "",
          agent_id: "",
          app_secret: "",
          bot_id: "",
          bot_secret: "",
          verify_token: "",
          verify_aes_key: "",
        },
        google_workspace: {
          client_id: "",
          client_secret: "",
        },
      };
      const nameMap: Record<string, string> = {
        feishu: "Feishu",
        wecom: "WeCom",
        dingtalk: "DingTalk",
        google_workspace: "Google",
        oauth2: "OAuth2",
      };
      setForm({
        provider_type: type,
        name: nameMap[type] || type,
        config: defaults[type] || {},
        app_id: "",
        app_secret: "",
        authorize_url: "",
        token_url: "",
        user_info_url: "",
        scope: "openid profile email",
      });
    }
    setSelectedDept(null);
    setMemberSearch("");
  };

  const renderForm = (type: string, existingProvider?: IdentityProvider) => {
    const providerBaseUrl = (() => {
      const rawDomain =
        existingProvider?.sso_domain || tenant?.sso_domain || "";
      if (rawDomain) {
        return rawDomain.startsWith("http")
          ? rawDomain
          : `https://${rawDomain}`;
      }
      return window.location.origin;
    })();
    const providerCallbackUrl = `${providerBaseUrl}/api/auth/${type}/callback`;

    return (
      <div
        style={{
          marginTop: "16px",
          paddingTop: "16px",
          borderTop: "1px solid var(--border-subtle)",
        }}
      >
        {/* Setup Guide moved to the top */}
        {["feishu", "dingtalk", "google_workspace"].includes(type) && (
          <div
            style={{
              background: "var(--bg-primary)",
              padding: "16px",
              borderRadius: "8px",
              border: "1px solid var(--border-subtle)",
              marginBottom: "20px",
              fontSize: "12px",
            }}
          >
            <div
              style={{
                fontWeight: 600,
                fontSize: "13px",
                marginBottom: "8px",
                color: "var(--text-primary)",
                display: "flex",
                alignItems: "center",
                gap: "6px",
              }}
            >
              <IconSettings size={15} stroke={1.8} />{" "}
              {t(
                "enterprise.org.syncSetupGuide",
                "Setup Guide & Required Permissions",
              )}
            </div>
            <div style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
              {type === "feishu" && (
                <>
                  {Array.from({ length: 7 }).map((_, i) => (
                    <div key={i} style={{ marginBottom: "6px" }}>
                      {i + 1}.{" "}
                      {t(`enterprise.org.syncGuide.feishu.step${i + 1}`)}
                    </div>
                  ))}
                  <div style={{ marginTop: "16px", marginBottom: "8px" }}>
                    {t(
                      "enterprise.org.feishuGuideText",
                      "Permission JSON (bulk import)",
                    )}
                  </div>
                  <div
                    style={{
                      position: "relative",
                      background: "#282c34",
                      borderRadius: "6px",
                      padding: "12px",
                      paddingRight: "40px",
                      color: "#abb2bf",
                      fontFamily: "monospace",
                      fontSize: "11px",
                      whiteSpace: "pre-wrap",
                      overflowX: "auto",
                    }}
                  >
                    <LinearCopyButton
                      className="btn btn-ghost"
                      style={{
                        position: "absolute",
                        top: "8px",
                        right: "8px",
                        fontSize: "10px",
                        color: "#abb2bf",
                        padding: "4px 8px",
                        background: "rgba(255,255,255,0.1)",
                        cursor: "pointer",
                        border: "none",
                        borderRadius: "4px",
                        height: "fit-content",
                        minWidth: "60px",
                      }}
                      textToCopy={FEISHU_SYNC_PERM_JSON}
                      label="Copy"
                      copiedLabel="Copied✓"
                    />
                    {FEISHU_SYNC_PERM_JSON}
                  </div>
                  <div
                    style={{ marginTop: "8px", color: "var(--text-secondary)" }}
                  >
                    {t(
                      "enterprise.org.feishuGuideWarning",
                      "Note: You must re-publish the app each time you add new permissions.",
                    )}
                  </div>
                </>
              )}
              {type === "dingtalk" && (
                <>
                  {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} style={{ marginBottom: "6px" }}>
                      {i + 1}.{" "}
                      {t(`enterprise.org.syncGuide.dingtalk.step${i + 1}`)}
                    </div>
                  ))}
                </>
              )}
              {type === "google_workspace" && (
                <>
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} style={{ marginBottom: "6px" }}>
                      {i + 1}.{" "}
                      {t(
                        `enterprise.org.syncGuide.google_workspace.step${i + 1}`,
                      )}
                    </div>
                  ))}
                </>
              )}
              {type === "wecom" && (
                <>
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} style={{ marginBottom: "6px" }}>
                      {i + 1}.{" "}
                      {t(`enterprise.org.syncGuide.wecom.step${i + 1}`)}
                    </div>
                  ))}
                </>
              )}
            </div>
          </div>
        )}

        {/* Name field only for oauth2 */}
        {type === "oauth2" && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "12px",
              marginBottom: "16px",
            }}
          >
            <div className="form-group">
              <label className="form-label">
                {t("enterprise.identity.name")}
              </label>
              <input
                className="form-input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
          </div>
        )}

        {type === "oauth2" ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "12px",
            }}
          >
            <div className="form-group">
              <label className="form-label">Client ID</label>
              <input
                className="form-input"
                value={form.app_id}
                onChange={(e) => setForm({ ...form, app_id: e.target.value })}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Client Secret</label>
              <input
                className="form-input"
                type="password"
                value={form.app_secret}
                onChange={(e) =>
                  setForm({ ...form, app_secret: e.target.value })
                }
              />
            </div>
            <div className="form-group" style={{ gridColumn: "1 / -1" }}>
              <label className="form-label">Authorize URL</label>
              <input
                className="form-input"
                value={form.authorize_url}
                onChange={(e) =>
                  setForm({ ...form, authorize_url: e.target.value })
                }
              />
            </div>
          </div>
        ) : type === "wecom" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "0" }}>
            {/* Prerequisites notice — all strings via i18n */}
            <div
              style={{
                padding: "16px",
                borderRadius: "8px",
                border: "1px solid var(--border-subtle)",
                background: "var(--bg-primary)",
                fontSize: "13px",
                lineHeight: 1.7,
                color: "var(--text-secondary)",
              }}
            >
              <div
                style={{
                  fontWeight: 600,
                  fontSize: "13px",
                  color: "var(--text-primary)",
                  marginBottom: "10px",
                }}
              >
                {t("enterprise.identity.wecomNotice.title")}
              </div>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "10px",
                }}
              >
                <div>
                  <div
                    style={{
                      fontWeight: 500,
                      color: "var(--text-primary)",
                      marginBottom: "3px",
                    }}
                  >
                    {t("enterprise.identity.wecomNotice.syncTitle")}
                  </div>
                  <div
                    style={{ fontSize: "12px", color: "var(--text-tertiary)" }}
                  >
                    {t("enterprise.identity.wecomNotice.syncDesc")}
                  </div>
                </div>
                <div
                  style={{
                    borderTop: "1px solid var(--border-subtle)",
                    paddingTop: "10px",
                  }}
                >
                  <div
                    style={{
                      fontWeight: 500,
                      color: "var(--text-primary)",
                      marginBottom: "3px",
                    }}
                  >
                    {t("enterprise.identity.wecomNotice.ssoTitle")}
                  </div>
                  <div
                    style={{ fontSize: "12px", color: "var(--text-tertiary)" }}
                  >
                    {t("enterprise.identity.wecomNotice.ssoDesc")}
                  </div>
                </div>
                <div
                  style={{
                    borderTop: "1px solid var(--border-subtle)",
                    paddingTop: "10px",
                  }}
                >
                  <div
                    style={{
                      fontWeight: 500,
                      color: "var(--text-primary)",
                      marginBottom: "3px",
                    }}
                  >
                    {t("enterprise.identity.wecomNotice.messagingTitle")}
                  </div>
                  <div
                    style={{ fontSize: "12px", color: "var(--text-tertiary)" }}
                  >
                    {t("enterprise.identity.wecomNotice.messagingDesc")}
                  </div>
                </div>
              </div>
              <div
                style={{
                  marginTop: "14px",
                  paddingTop: "12px",
                  borderTop: "1px solid var(--border-subtle)",
                  fontSize: "12px",
                  color: "var(--text-tertiary)",
                  lineHeight: 1.6,
                }}
              >
                {t("enterprise.identity.wecomNotice.footerText")}
              </div>
            </div>
          </div>
        ) : type === "dingtalk" ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "12px",
            }}
          >
            <div className="form-group" style={{ gridColumn: "1 / -1" }}>
              <div style={{ fontSize: "11px", color: "var(--text-tertiary)" }}>
                {t("enterprise.identity.providerHints.dingtalk")}
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">App Key</label>
              <input
                className="form-input"
                value={form.config.app_key || ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: { ...form.config, app_key: e.target.value },
                  })
                }
                placeholder="dingxxxxxxxxxxxx"
              />
            </div>
            <div className="form-group">
              <label className="form-label">App Secret</label>
              <input
                className="form-input"
                type="password"
                value={form.config.app_secret || ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: { ...form.config, app_secret: e.target.value },
                  })
                }
              />
            </div>
          </div>
        ) : type === "google_workspace" ? (
          <div
            style={{ display: "flex", flexDirection: "column", gap: "16px" }}
          >
            <div
              style={{
                padding: "14px",
                borderRadius: "8px",
                border: "1px solid var(--border-subtle)",
                background: "var(--bg-primary)",
              }}
            >
              <div
                style={{
                  fontWeight: 600,
                  fontSize: "13px",
                  marginBottom: "10px",
                }}
              >
                Google OAuth
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "12px",
                }}
              >
                <div className="form-group" style={{ gridColumn: "1 / -1" }}>
                  <div
                    style={{ fontSize: "11px", color: "var(--text-tertiary)" }}
                  >
                    {t(
                      "enterprise.identity.providerHints.google_workspace",
                      "Google Workspace: use one Client ID and Client Secret for both SSO and admin-authorized directory sync.",
                    )}
                  </div>
                </div>
                <div className="form-group">
                  <label className="form-label">Client ID</label>
                  <input
                    className="form-input"
                    value={form.config.client_id || ""}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        config: { ...form.config, client_id: e.target.value },
                      })
                    }
                    placeholder="xxxxxxxx.apps.googleusercontent.com"
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Client Secret</label>
                  <input
                    className="form-input"
                    type="password"
                    value={form.config.client_secret || ""}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        config: {
                          ...form.config,
                          client_secret: e.target.value,
                        },
                      })
                    }
                  />
                </div>
                <div className="form-group" style={{ gridColumn: "1 / -1" }}>
                  <label className="form-label">
                    {t(
                      "enterprise.identity.callbackUrl",
                      "Redirect URL (paste this in your app settings)",
                    )}
                  </label>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                    }}
                  >
                    <div
                      style={{
                        flex: 1,
                        padding: "8px 12px",
                        background: "var(--bg-elevated)",
                        border: "1px solid var(--border-subtle)",
                        borderRadius: "6px",
                        fontSize: "12px",
                        color: "var(--text-primary)",
                        fontFamily: "monospace",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {providerCallbackUrl}
                    </div>
                    <LinearCopyButton
                      className="btn btn-ghost btn-sm"
                      style={{
                        fontSize: "11px",
                        width: "auto",
                        minWidth: "70px",
                        height: "33px",
                      }}
                      textToCopy={providerCallbackUrl}
                      label={t("common.copy", "Copy")}
                      copiedLabel="Copied"
                    />
                  </div>
                  <div
                    style={{
                      fontSize: "10px",
                      color: "var(--text-tertiary)",
                      marginTop: "4px",
                    }}
                  >
                    {t(
                      "enterprise.identity.callbackUrlHint",
                      "Add this URL as the OAuth redirect URI in your identity provider's app configuration.",
                    )}
                  </div>
                </div>
                <div className="form-group" style={{ gridColumn: "1 / -1" }}>
                  <label className="form-label">
                    Directory Sync Authorization
                  </label>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      flexWrap: "wrap",
                    }}
                  >
                    <button
                      className="btn btn-secondary btn-sm"
                      type="button"
                      onClick={() =>
                        existingProvider &&
                        handleGoogleAdminAuthorize(existingProvider.id)
                      }
                      disabled={!existingProvider}
                    >
                      {existingProvider?.config?.google_admin_authorized_email
                        ? "Re-authorize Admin Sync"
                        : "Authorize Admin Sync"}
                    </button>
                    {!existingProvider && (
                      <span
                        style={{
                          fontSize: "11px",
                          color: "var(--text-tertiary)",
                        }}
                      >
                        Please save the provider first.
                      </span>
                    )}
                    {existingProvider?.config
                      ?.google_admin_authorized_email && (
                      <span
                        style={{
                          fontSize: "11px",
                          color: "var(--text-secondary)",
                        }}
                      >
                        Authorized as{" "}
                        {existingProvider.config.google_admin_authorized_email}
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: "10px",
                      color: "var(--text-tertiary)",
                      marginTop: "4px",
                    }}
                  >
                    Sign in with a Google Workspace admin account to grant
                    directory read access. Clawith will securely store a refresh
                    token and use it for scheduled sync.
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : type === "feishu" ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "12px",
            }}
          >
            <div className="form-group" style={{ gridColumn: "1 / -1" }}>
              <div style={{ fontSize: "11px", color: "var(--text-tertiary)" }}>
                {t("enterprise.identity.providerHints.feishu")}
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">App ID</label>
              <input
                className="form-input"
                value={form.config.app_id || ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: { ...form.config, app_id: e.target.value },
                  })
                }
                placeholder="cli_xxxxxxxxxxxx"
              />
            </div>
            <div className="form-group">
              <label className="form-label">App Secret</label>
              <input
                className="form-input"
                type="password"
                value={form.config.app_secret || ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: { ...form.config, app_secret: e.target.value },
                  })
                }
              />
            </div>
          </div>
        ) : null}

        {/* Hide save/delete for WeCom while config is disabled */}
        {type !== "wecom" && (
          <div
            style={{
              display: "flex",
              gap: "8px",
              alignItems: "center",
              marginTop: "16px",
            }}
          >
            <button
              className="btn btn-primary btn-sm"
              onClick={save}
              disabled={savingProvider}
            >
              {savingProvider ? t("common.loading") : t("common.save", "Save")}
            </button>
            {saveProviderOk && (
              <span style={{ fontSize: "12px", color: "var(--success)" }}>
                Saved
              </span>
            )}
            {existingProvider && (
              <button
                className="btn btn-ghost btn-sm"
                style={{ color: "var(--error)" }}
                onClick={async () => {
                  const ok = await dialog.confirm(
                    t("common.dialog.deleteConfigConfirm"),
                    {
                      title: t("common.dialog.deleteConfig"),
                      danger: true,
                      confirmLabel: t("common.confirmActions.deleteLabel"),
                    },
                  );
                  if (ok) deleteProvider.mutate(existingProvider.id);
                }}
              >
                {t("common.delete", "Delete")}
              </button>
            )}
          </div>
        )}
      </div>
    );
  };

  const renderOrgBrowser = (p: IdentityProvider) => {
    return (
      <div
        style={{
          marginTop: "24px",
          paddingTop: "24px",
          borderTop: "1px dashed var(--border-subtle)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: "16px",
          }}
        >
          <div style={{ fontWeight: 500, fontSize: "14px" }}>
            {t("enterprise.org.orgBrowser", "Organization Browser")}
          </div>

          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-end",
              gap: "8px",
            }}
          >
            {["feishu", "dingtalk", "google_workspace"].includes(
              p.provider_type,
            ) && (
              <button
                className="btn btn-secondary btn-sm"
                style={{ fontSize: "12px" }}
                onClick={() => triggerSync(p.id)}
                disabled={!!syncing}
              >
                {syncing === p.id ? "Syncing..." : "Sync Directory"}
              </button>
            )}
            {syncResult && (
              <div
                style={{
                  padding: "6px 10px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  background:
                    syncResult.error ||
                    (syncResult.errors && syncResult.errors.length > 0)
                      ? "rgba(255,100,0,0.1)"
                      : "rgba(0,200,0,0.1)",
                }}
              >
                {syncResult.error
                  ? `Error: ${syncResult.error}`
                  : `Sync complete: ${syncResult.departments || 0} depts, ${syncResult.members || 0} members synced.`}
                {syncResult.errors && syncResult.errors.length > 0 && (
                  <div
                    style={{
                      marginTop: "4px",
                      color: "var(--color-warning, #f90)",
                    }}
                  >
                    {/* Show first error to help diagnose permission issues */}
                    {`Warning: ${syncResult.errors[0]}`}
                    {syncResult.errors.length > 1 &&
                      ` (+${syncResult.errors.length - 1} more)`}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: "16px" }}>
          <div
            style={{
              width: "260px",
              borderRight: "1px solid var(--border-subtle)",
              paddingRight: "16px",
              maxHeight: "500px",
              overflowY: "auto",
            }}
          >
            <div
              style={{
                padding: "6px 8px",
                borderRadius: "4px",
                cursor: "pointer",
                fontSize: "13px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                background: !selectedDept
                  ? "rgba(224,238,238,0.1)"
                  : "transparent",
              }}
              onClick={() => setSelectedDept(null)}
            >
              {t("common.all")}
              {departmentsData.total_member > 0 && (
                <span
                  style={{ fontSize: "10px", color: "var(--text-tertiary)" }}
                >
                  ({departmentsData.total_member})
                </span>
              )}
            </div>
            <DeptTree
              departments={departmentsData.items}
              parentId={null}
              selectedDept={selectedDept}
              onSelect={setSelectedDept}
              level={0}
            />
          </div>

          <div style={{ flex: 1 }}>
            <input
              className="form-input"
              placeholder={t("enterprise.org.searchMembers")}
              value={memberSearch}
              onChange={(e) => setMemberSearch(e.target.value)}
              style={{ marginBottom: "12px" }}
            />
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "4px",
                maxHeight: "400px",
                overflowY: "auto",
              }}
            >
              {members.map((m) => (
                <div
                  key={m.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                    padding: "8px",
                    borderRadius: "6px",
                    border: "1px solid var(--border-subtle)",
                  }}
                >
                  <div
                    style={{
                      width: "32px",
                      height: "32px",
                      borderRadius: "50%",
                      background: "var(--bg-tertiary)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: "14px",
                      fontWeight: 600,
                    }}
                  >
                    {m.name?.[0]}
                  </div>
                  <div>
                    <div style={{ fontWeight: 500, fontSize: "13px" }}>
                      {m.name}
                    </div>
                    <div
                      style={{
                        fontSize: "11px",
                        color: "var(--text-tertiary)",
                      }}
                    >
                      {m.provider_type && (
                        <span
                          style={{
                            marginRight: "4px",
                            padding: "1px 4px",
                            borderRadius: "3px",
                            background: "var(--bg-secondary)",
                            fontSize: "10px",
                          }}
                        >
                          {m.provider_type}
                        </span>
                      )}
                      {m.title || "-"} ·{" "}
                      {m.department_path || m.department_id || "-"}
                    </div>
                  </div>
                </div>
              ))}
              {members.length === 0 && (
                <div
                  style={{
                    textAlign: "center",
                    padding: "24px",
                    color: "var(--text-tertiary)",
                  }}
                >
                  {t("enterprise.org.noMembers")}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      {/* SSO status is now derived from per-channel toggles — no global switch */}

      {/* 1. Identity Providers Section */}
      <div className="card" style={{ padding: "0", overflow: "hidden" }}>
        <div
          style={{
            padding: "16px 20px",
            borderBottom: "1px solid var(--border-subtle)",
            background: "var(--bg-secondary)",
          }}
        >
          <h3 style={{ margin: 0, fontSize: "15px", fontWeight: 600 }}>
            {t("enterprise.identity.title", "Organization & Directory Sync")}
          </h3>
          <div
            style={{
              fontSize: "12px",
              color: "var(--text-secondary)",
              marginTop: "4px",
            }}
          >
            Configure enterprise directory synchronization and Identity Provider
            settings.
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column" }}>
          {IDP_TYPES.map((idp, index) => {
            const existingProvider = providers.find(
              (p) => p.provider_type === idp.type,
            );
            const isExpanded = expandedType === idp.type;

            return (
              <div
                key={idp.type}
                style={{
                  borderBottom:
                    index < IDP_TYPES.length - 1
                      ? "1px solid var(--border-subtle)"
                      : "none",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "16px 20px",
                    cursor: "pointer",
                    background: isExpanded
                      ? "var(--bg-secondary)"
                      : "transparent",
                    transition: "background 0.2s",
                  }}
                  onClick={() => handleExpand(idp.type, existingProvider)}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "12px",
                    }}
                  >
                    {idp.icon}
                    <div>
                      <div style={{ fontWeight: 500, fontSize: "14px" }}>
                        {idp.name}
                      </div>
                      <div
                        style={{
                          fontSize: "12px",
                          color: "var(--text-tertiary)",
                        }}
                      >
                        {idp.desc}
                      </div>
                    </div>
                  </div>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "16px",
                    }}
                  >
                    {existingProvider ? (
                      <div
                        style={{
                          display: "flex",
                          flexWrap: "wrap",
                          alignItems: "flex-end",
                          gap: "8px",
                        }}
                      >
                        <span
                          className="badge badge-success"
                          style={{ fontSize: "10px" }}
                        >
                          Active
                        </span>
                        {existingProvider.last_synced_at && (
                          <span
                            style={{
                              fontSize: "10px",
                              color: "var(--text-tertiary)",
                            }}
                          >
                            Synced:{" "}
                            {new Date(
                              existingProvider.last_synced_at,
                            ).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span
                        className="badge badge-secondary"
                        style={{ fontSize: "10px" }}
                      >
                        Not configured
                      </span>
                    )}
                    <div
                      style={{
                        color: "var(--text-tertiary)",
                        transform: isExpanded ? "rotate(180deg)" : "none",
                        transition: "transform 0.2s",
                        fontSize: "12px",
                      }}
                    >
                      ▼
                    </div>
                  </div>
                </div>

                {isExpanded && (
                  <div
                    style={{
                      padding: "0 20px 20px",
                      background: "var(--bg-secondary)",
                    }}
                  >
                    {renderForm(idp.type, existingProvider)}

                    {/* Per-channel SSO Login URLs & Toggle */}
                    {[
                      "feishu",
                      "dingtalk",
                      "google_workspace",
                      "oauth2",
                    ].includes(idp.type) && (
                      <SsoChannelSection
                        key={`${existingProvider?.id ?? idp.type}:${existingProvider?.sso_domain ?? tenant?.sso_domain ?? ""}`}
                        idpType={idp.type}
                        existingProvider={existingProvider}
                        tenant={tenant}
                        t={t}
                      />
                    )}
                    {existingProvider &&
                      idp.type !== "wecom" &&
                      renderOrgBrowser(existingProvider)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
