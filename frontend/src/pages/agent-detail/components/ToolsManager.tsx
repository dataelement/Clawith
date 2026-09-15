import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import {
  IconBrowser,
  IconChevronDown,
  IconClock,
  IconFileText,
  IconMessageCircle,
  IconSearch,
  IconSettings,
  IconTerminal2,
  IconTools,
} from "@tabler/icons-react";

import { useDialog } from "../../../components/Dialog/DialogContext";
import { useToast } from "../../../components/Toast/ToastContext";
import {
  caughtErrorMessage,
  parseHttpErrorResponse,
} from "../../../services/apiError";
import type { JsonValue } from "../../../services/apiContracts";
import { useAuthStore } from "../../../stores";
import {
  closeMcpAuthorizationWindow,
  getSmitheryAuthorizationButtonLabel,
  getSmitheryAuthorizationTool,
  navigateMcpAuthorizationWindow,
  openMcpAuthorizationWindow,
  requestMcpAuthorizationStatus,
  shouldPreopenMcpAuthorizationWindow,
  type McpAuthorizationState,
} from "../mcpAuthorization";
import {
  parseCompleteList,
  requestAgentToolsWithConfig,
  requestToolsJson,
  requestToolsMutation,
  resetAgentToolConfig,
  updateToolEnabled,
} from "../toolsManagerData";

type ToolConfig = Record<string, JsonValue>;
type ToolStatusFilter = "all" | "enabled" | "disabled" | "configured";
type ConfigLoadStatus = "idle" | "loading" | "ready" | "error";

const TOOL_STATUS_FILTERS: readonly ToolStatusFilter[] = [
  "all",
  "enabled",
  "disabled",
  "configured",
];

interface ConfigOption {
  value: string | number;
  label: string;
  help_text?: string;
  help_url?: string;
}

interface ConfigField {
  key: string;
  label: string;
  type: string;
  placeholder?: string;
  default?: JsonValue;
  options?: ConfigOption[];
  min?: number;
  max?: number;
  advanced?: boolean;
  depends_on?: Record<string, JsonValue[]>;
  read_only_for_roles?: string[];
}

interface ConfigSchema {
  title?: string;
  fields: ConfigField[];
}

interface AgentTool {
  id: string;
  agent_tool_id: string | null;
  name: string;
  display_name: string;
  description: string;
  type: string;
  category: string;
  enabled: boolean;
  mcp_server_name: string | null;
  mcp_authorization_provider: string | null;
  config_schema: ConfigSchema;
  global_config: ToolConfig;
  agent_config: ToolConfig;
  source: string;
}

interface CategoryConfigResponse {
  global_config: ToolConfig;
  agent_config: ToolConfig;
}

interface EmailTestResponse {
  ok: boolean;
  imap?: string;
  smtp?: string;
  error?: string;
}

interface CategoryTestResponse {
  ok: boolean;
  message?: string;
  error?: JsonValue;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isJsonValue = (value: unknown): value is JsonValue => {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return true;
  }
  if (Array.isArray(value)) return value.every(isJsonValue);
  return isRecord(value) && Object.values(value).every(isJsonValue);
};

const parseToolConfig = (value: unknown): ToolConfig => {
  if (!isRecord(value)) return {};
  const config: ToolConfig = {};
  for (const [key, entry] of Object.entries(value)) {
    if (isJsonValue(entry)) config[key] = entry;
  }
  return config;
};

const parseConfigOption = (value: unknown): ConfigOption | null => {
  if (
    !isRecord(value) ||
    (typeof value.value !== "string" && typeof value.value !== "number") ||
    typeof value.label !== "string"
  ) {
    return null;
  }
  return {
    value: value.value,
    label: value.label,
    ...(typeof value.help_text === "string"
      ? { help_text: value.help_text }
      : {}),
    ...(typeof value.help_url === "string" ? { help_url: value.help_url } : {}),
  };
};

const parseConfigField = (value: unknown): ConfigField | null => {
  if (
    !isRecord(value) ||
    typeof value.key !== "string" ||
    typeof value.label !== "string" ||
    typeof value.type !== "string"
  ) {
    return null;
  }
  const dependsOn: Record<string, JsonValue[]> = {};
  if (isRecord(value.depends_on)) {
    for (const [key, entries] of Object.entries(value.depends_on)) {
      if (Array.isArray(entries)) {
        dependsOn[key] = entries.filter(isJsonValue);
      }
    }
  }
  return {
    key: value.key,
    label: value.label,
    type: value.type,
    ...(typeof value.placeholder === "string"
      ? { placeholder: value.placeholder }
      : {}),
    ...(isJsonValue(value.default) ? { default: value.default } : {}),
    ...(Array.isArray(value.options)
      ? {
          options: value.options
            .map(parseConfigOption)
            .filter((option): option is ConfigOption => option !== null),
        }
      : {}),
    ...(typeof value.min === "number" ? { min: value.min } : {}),
    ...(typeof value.max === "number" ? { max: value.max } : {}),
    ...(typeof value.advanced === "boolean"
      ? { advanced: value.advanced }
      : {}),
    ...(Object.keys(dependsOn).length > 0 ? { depends_on: dependsOn } : {}),
    ...(Array.isArray(value.read_only_for_roles)
      ? {
          read_only_for_roles: value.read_only_for_roles.filter(
            (role): role is string => typeof role === "string",
          ),
        }
      : {}),
  };
};

const parseConfigSchema = (value: unknown): ConfigSchema => {
  if (!isRecord(value)) return { fields: [] };
  return {
    ...(typeof value.title === "string" ? { title: value.title } : {}),
    fields: Array.isArray(value.fields)
      ? value.fields
          .map(parseConfigField)
          .filter((field): field is ConfigField => field !== null)
      : [],
  };
};

const parseAgentTool = (value: unknown): AgentTool | null => {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.name !== "string" ||
    typeof value.display_name !== "string" ||
    typeof value.description !== "string" ||
    typeof value.type !== "string" ||
    typeof value.category !== "string" ||
    typeof value.enabled !== "boolean" ||
    typeof value.source !== "string" ||
    (value.agent_tool_id !== null && typeof value.agent_tool_id !== "string") ||
    (value.mcp_server_name !== null &&
      typeof value.mcp_server_name !== "string") ||
    (value.mcp_authorization_provider !== null &&
      typeof value.mcp_authorization_provider !== "string") ||
    !isRecord(value.config_schema) ||
    !isRecord(value.global_config) ||
    !isRecord(value.agent_config)
  ) {
    return null;
  }
  return {
    id: value.id,
    agent_tool_id: value.agent_tool_id,
    name: value.name,
    display_name: value.display_name,
    description: value.description,
    type: value.type,
    category: value.category,
    enabled: value.enabled,
    mcp_server_name: value.mcp_server_name,
    mcp_authorization_provider: value.mcp_authorization_provider,
    config_schema: parseConfigSchema(value.config_schema),
    global_config: parseToolConfig(value.global_config),
    agent_config: parseToolConfig(value.agent_config),
    source: value.source,
  };
};

const parseAgentTools = (value: unknown): AgentTool[] =>
  parseCompleteList({
    payload: value,
    parseItem: parseAgentTool,
    contractName: "agent tools",
  });

const parseCategoryConfig = (value: unknown): CategoryConfigResponse => {
  if (
    !isRecord(value) ||
    !isRecord(value.global_config) ||
    !isRecord(value.agent_config)
  ) {
    throw new Error("Invalid category configuration response");
  }
  return {
    global_config: parseToolConfig(value.global_config),
    agent_config: parseToolConfig(value.agent_config),
  };
};

const parseEmailTestResponse = (value: unknown): EmailTestResponse => {
  if (!isRecord(value)) return { ok: false, error: "Invalid response" };
  return {
    ok: value.ok === true,
    ...(typeof value.imap === "string" ? { imap: value.imap } : {}),
    ...(typeof value.smtp === "string" ? { smtp: value.smtp } : {}),
    ...(typeof value.error === "string" ? { error: value.error } : {}),
  };
};

const parseCategoryTestResponse = (value: unknown): CategoryTestResponse => {
  if (!isRecord(value) || typeof value.ok !== "boolean") {
    throw new Error("Invalid category test response");
  }
  if (value.message !== undefined && typeof value.message !== "string") {
    throw new Error("Invalid category test response");
  }
  if (value.error !== undefined && !isJsonValue(value.error)) {
    throw new Error("Invalid category test response");
  }
  return {
    ok: value.ok,
    ...(typeof value.message === "string" ? { message: value.message } : {}),
    ...(value.error !== undefined ? { error: value.error } : {}),
  };
};

const inputValue = (value: JsonValue | undefined): string | number => {
  if (typeof value === "string" || typeof value === "number") return value;
  if (typeof value === "boolean") return String(value);
  return value === null || value === undefined ? "" : JSON.stringify(value);
};

const getCategoryLabels = (t: TFunction): Record<string, string> => ({
  file: t("agent.toolCategories.file"),
  task: t("agent.toolCategories.task"),
  communication: t("agent.toolCategories.communication"),
  search: t("agent.toolCategories.search"),
  aware: t("agent.toolCategories.aware", "Aware & Triggers"),
  social: t("agent.toolCategories.social", "Social"),
  code: t("agent.toolCategories.code", "Code & Execution"),
  discovery: t("agent.toolCategories.discovery", "Discovery"),
  email: t("agent.toolCategories.email", "Email"),
  feishu: t("agent.toolCategories.feishu", "Feishu / Lark"),
  custom: t("agent.toolCategories.custom"),
  general: t("agent.toolCategories.general"),
  agentbay: t("agent.toolCategories.agentbay", "AgentBay"),
});

export default function ToolsManager({
  agentId,
  canManage = false,
}: {
  agentId: string;
  canManage?: boolean;
}) {
  const { t } = useTranslation();
  const dialog = useDialog();
  const toast = useToast();
  const [tools, setTools] = useState<AgentTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [configTool, setConfigTool] = useState<AgentTool | null>(null);
  const [configData, setConfigData] = useState<ToolConfig>({});
  const [configJson, setConfigJson] = useState("");
  const [configSaving, setConfigSaving] = useState(false);
  const [configLoadStatus, setConfigLoadStatus] =
    useState<ConfigLoadStatus>("idle");
  const [configLoadError, setConfigLoadError] = useState("");
  const [categoryConfigController, setCategoryConfigController] =
    useState<AbortController | null>(null);
  const [toolTab, setToolTab] = useState<"company" | "installed">("company");
  const [deletingToolId, setDeletingToolId] = useState<string | null>(null);
  const [configCategory, setConfigCategory] = useState<string | null>(null);
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [showAdvancedToolConfig, setShowAdvancedToolConfig] = useState(false);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(
    () => new Set(),
  );
  const [toolSearch, setToolSearch] = useState("");
  const [toolStatusFilter, setToolStatusFilter] =
    useState<ToolStatusFilter>("all");
  const [mcpAuthorizationStates, setMcpAuthorizationStates] = useState<
    Record<string, McpAuthorizationState>
  >({});
  const [checkingMcpAuthorizationGroup, setCheckingMcpAuthorizationGroup] =
    useState<string | null>(null);
  // Global (company-level) config for the currently open modal — used to show
  // lock hints and prevent agent from overriding company-set fields.
  const [configGlobalData, setConfigGlobalData] = useState<ToolConfig>({});

  const CATEGORY_CONFIG_SCHEMAS: Record<string, ConfigSchema> = {
    agentbay: {
      title: "AgentBay Settings",
      fields: [
        {
          key: "api_key",
          label: "API Key (from AgentBay)",
          type: "password",
          placeholder: "Enter your AgentBay API key",
        },
        {
          key: "os_type",
          label: "Cloud Computer OS",
          type: "select",
          default: "windows",
          options: [
            { value: "linux", label: "Linux" },
            { value: "windows", label: "Windows" },
          ],
        },
      ],
    },
    atlassian: {
      title: "Atlassian Connectivity Settings",
      fields: [
        {
          key: "api_key",
          label: "API Key (Atlassian API Token)",
          type: "password",
          placeholder: "Enter your Atlassian API key",
        },
        {
          key: "cloud_id",
          label: "Cloud ID (Optional)",
          type: "text",
          placeholder: "e.g. bcc01-abc-123",
        },
      ],
    },
  };

  const fetchTools = useCallback(async (): Promise<AgentTool[]> => {
    const token = localStorage.getItem("token");
    return requestAgentToolsWithConfig({
      agentId,
      token,
      parsePayload: parseAgentTools,
      parseError: parseHttpErrorResponse,
    });
  }, [agentId]);

  const loadTools = useCallback(async (): Promise<boolean> => {
    setLoading(true);
    try {
      setTools(await fetchTools());
      setLoadError("");
      return true;
    } catch (error) {
      setLoadError(
        caughtErrorMessage(error) ||
          t("agent.tools.loadFailed", "Could not load tools."),
      );
      return false;
    } finally {
      setLoading(false);
    }
  }, [fetchTools, t]);

  useEffect(() => {
    let active = true;
    void fetchTools()
      .then((loadedTools) => {
        if (!active) return;
        setTools(loadedTools);
        setLoadError("");
        setLoading(false);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setLoadError(
          caughtErrorMessage(error) ||
            t("agent.tools.loadFailed", "Could not load tools."),
        );
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [fetchTools, t]);

  const toggleTool = async (toolId: string, enabled: boolean) => {
    const toolIds = new Set([toolId]);
    setTools((previous) => updateToolEnabled(previous, toolIds, enabled));
    try {
      const token = localStorage.getItem("token");
      await requestToolsMutation({
        url: `/api/tools/agents/${agentId}`,
        token,
        method: "PUT",
        body: [{ tool_id: toolId, enabled }],
        parseError: parseHttpErrorResponse,
      });
    } catch (error) {
      setTools((previous) => updateToolEnabled(previous, toolIds, !enabled));
      toast.error(t("common.error.saveFailed", "Save failed"), {
        details: caughtErrorMessage(error),
      });
    }
  };

  // Sensitive field keys that should not be pre-filled from masked global config.
  // Hardcoded fallback set + dynamic extraction from config_schema password-type fields.
  const SENSITIVE_KEYS_BASE = new Set([
    "api_key",
    "private_key",
    "auth_code",
    "password",
    "secret",
  ]);

  const getSensitiveKeys = (schema?: ConfigSchema): Set<string> => {
    const keys = new Set(SENSITIVE_KEYS_BASE);
    if (schema) {
      for (const field of schema.fields) {
        if (field.type === "password") keys.add(field.key);
      }
    }
    return keys;
  };

  const applyConfigDefaults = (
    fields: ConfigField[] = [],
    config: ToolConfig = {},
  ) => {
    const next = { ...config };
    for (const field of fields) {
      if (
        field.default !== undefined &&
        (next[field.key] === undefined ||
          next[field.key] === null ||
          next[field.key] === "")
      ) {
        next[field.key] = field.default;
      }
    }
    return next;
  };

  const openConfig = (tool: AgentTool) => {
    categoryConfigController?.abort();
    setCategoryConfigController(null);
    setConfigTool(tool);
    setConfigLoadStatus("ready");
    setConfigLoadError("");
    setShowAdvancedToolConfig(false);
    // Build merged config: start with global defaults, overlay agent overrides.
    // For sensitive fields, only use agent_config values (global ones are masked
    // like "****xxxx" and should not pre-fill the input).
    const sensitiveKeys = getSensitiveKeys(tool.config_schema);
    const globalCfg = tool.global_config || {};
    const agentCfg = tool.agent_config || {};
    const merged: ToolConfig = {};
    for (const [k, v] of Object.entries(globalCfg)) {
      if (!sensitiveKeys.has(k)) merged[k] = v;
    }
    Object.assign(merged, agentCfg);
    Object.assign(
      merged,
      applyConfigDefaults(tool.config_schema?.fields || [], merged),
    );
    setConfigData(merged);
    setConfigJson(JSON.stringify(agentCfg, null, 2));
    setFocusedField(null);
  };

  const openCategoryConfig = async (category: string) => {
    categoryConfigController?.abort();
    const controller = new AbortController();
    setCategoryConfigController(controller);
    setConfigCategory(category);
    setShowAdvancedToolConfig(false);
    setConfigLoadStatus("loading");
    setConfigLoadError("");
    setFocusedField(null);
    try {
      const token = localStorage.getItem("token");
      const data = await requestToolsJson({
        url: `/api/tools/agents/${encodeURIComponent(agentId)}/category-config/${encodeURIComponent(category)}`,
        token,
        signal: controller.signal,
        parsePayload: parseCategoryConfig,
        parseError: parseHttpErrorResponse,
      });
      // global_config: company-level (masked sensitive fields like ****xxxx)
      // agent_config: agent-level overrides only
      const globalCfg = data.global_config;
      const agentCfg = data.agent_config;
      setConfigGlobalData(globalCfg);
      // Pre-fill only agent-level values; company fields show as hints
      const catSchema = CATEGORY_CONFIG_SCHEMAS[category];
      const sensitiveKeys = getSensitiveKeys(catSchema);
      const merged: ToolConfig = {};
      for (const [k, v] of Object.entries(globalCfg)) {
        // Non-sensitive global fields (e.g. os_type) pre-fill; sensitive ones don't
        if (!sensitiveKeys.has(k)) merged[k] = v;
      }
      Object.assign(merged, agentCfg);
      setConfigData(merged);
      setConfigLoadStatus("ready");
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") return;
      setConfigLoadStatus("error");
      setConfigLoadError(
        caughtErrorMessage(error) || "Failed to load category configuration.",
      );
    } finally {
      setCategoryConfigController((current) =>
        current === controller ? null : current,
      );
    }
  };

  const closeConfigModal = () => {
    categoryConfigController?.abort();
    setCategoryConfigController(null);
    setConfigTool(null);
    setConfigCategory(null);
    setConfigLoadStatus("idle");
    setConfigLoadError("");
  };

  const resetToolConfig = async () => {
    if (!configTool) return;
    setConfigSaving(true);
    try {
      await resetAgentToolConfig({
        agentId,
        toolId: configTool.id,
        token: localStorage.getItem("token"),
        parseError: parseHttpErrorResponse,
      });
      closeConfigModal();
      await loadTools();
    } catch (error) {
      toast.error(t("common.error.saveFailed", "Save failed"), {
        details: caughtErrorMessage(error),
      });
    } finally {
      setConfigSaving(false);
    }
  };

  const saveConfig = async () => {
    if (!configTool && !configCategory) return;
    if (configCategory && configLoadStatus !== "ready") return;
    setConfigSaving(true);
    try {
      const token = localStorage.getItem("token");

      if (configCategory) {
        const raw = configData;
        // Strip empty sensitive fields so untouched password inputs
        // don't send empty values that would clear an inherited company key
        const catSchema = CATEGORY_CONFIG_SCHEMAS[configCategory];
        const sensitiveKeys = getSensitiveKeys(catSchema);
        const payload: ToolConfig = {};
        for (const [k, v] of Object.entries(raw)) {
          if (
            sensitiveKeys.has(k) &&
            (v === "" || v === undefined || v === null)
          )
            continue;
          payload[k] = v;
        }
        await requestToolsMutation({
          url: `/api/tools/agents/${encodeURIComponent(agentId)}/category-config/${encodeURIComponent(configCategory)}`,
          token,
          method: "POST",
          body: { config: payload },
          parseError: parseHttpErrorResponse,
        });
        closeConfigModal();
      } else if (configTool) {
        const currentConfigTool = configTool;
        const hasSchema = currentConfigTool.config_schema.fields.length > 0;
        const raw = hasSchema
          ? configData
          : parseToolConfig(JSON.parse(configJson || "{}"));
        // Strip empty sensitive fields only — agent CAN override company values
        const sensitiveKeys = getSensitiveKeys(currentConfigTool.config_schema);
        const payload: ToolConfig = {};
        for (const [k, v] of Object.entries(raw)) {
          if (
            sensitiveKeys.has(k) &&
            (v === "" || v === undefined || v === null)
          )
            continue;
          payload[k] = v;
        }
        await requestToolsMutation({
          url: `/api/tools/agents/${encodeURIComponent(agentId)}/tool-config/${encodeURIComponent(currentConfigTool.id)}`,
          token,
          method: "PUT",
          body: { config: payload },
          parseError: parseHttpErrorResponse,
        });
        closeConfigModal();
      }
      await loadTools();
    } catch (error) {
      toast.error(t("common.error.saveFailed"), {
        details: String(caughtErrorMessage(error) || error),
      });
    }
    setConfigSaving(false);
  };

  if (loading)
    return (
      <div style={{ color: "var(--text-tertiary)", padding: "20px" }}>
        {t("common.loading")}
      </div>
    );

  const loadErrorNotice = loadError ? (
    <div
      role="alert"
      className="card"
      style={{
        padding: "16px",
        color: "var(--error)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "12px",
      }}
    >
      <span>{loadError}</span>
      <button
        type="button"
        className="btn btn-secondary"
        onClick={() => void loadTools()}
      >
        {t("common.retry", "Retry")}
      </button>
    </div>
  ) : null;

  if (loadError && tools.length === 0) return loadErrorNotice;

  // Company tools = platform presets (builtin) + company admin-added tools (admin)
  // Hide system-internal tools (e.g. finish) — they are protocol-level and not user-facing.
  const companyTools = tools.filter(
    (t) =>
      (t.source === "builtin" || t.source === "admin") &&
      t.category !== "system",
  );
  const agentInstalledTools = tools.filter(
    (t) => t.source === "agent" && t.category !== "system",
  );

  const mcpGroupKey = (tool: AgentTool) => {
    const serverName = String(tool.mcp_server_name || "").trim();
    return tool.type === "mcp" && serverName
      ? `mcp:${serverName.toLowerCase()}`
      : tool.category || "general";
  };

  const getToolGroupMeta = (groupKey: string, toolsInGroup: AgentTool[]) => {
    const first =
      toolsInGroup.find(
        (tool) => tool.type === "mcp" && tool.mcp_server_name,
      ) || toolsInGroup[0];
    if (groupKey.startsWith("mcp:") && first?.mcp_server_name) {
      return {
        label: first.mcp_server_name,
        description: t(
          "agent.tools.mcpGroupDescription",
          "Tools from {{name}}",
          { name: first.mcp_server_name },
        ),
        iconCategory: "custom",
        configCategory: first.category || "custom",
      };
    }
    return {
      label: categoryLabels[groupKey] || groupKey,
      description: categoryDescriptions[groupKey] || "Tools in this category",
      iconCategory: groupKey,
      configCategory: groupKey,
    };
  };

  const groupByCategory = (toolList: AgentTool[]) =>
    toolList.reduce<Record<string, AgentTool[]>>((acc, tool) => {
      const cat = mcpGroupKey(tool);
      (acc[cat] = acc[cat] || []).push(tool);
      return acc;
    }, {});

  const categoryLabels = getCategoryLabels(t);
  const categoryDescriptions: Record<string, string> = {
    agentbay: "Browser and cloud computer automation",
    file: "Read, write, convert, and manage workspace files",
    communication: "Messages and cross-channel collaboration",
    search: "Web and knowledge search tools",
    code: "Code execution and development utilities",
    aware: "Triggers, reminders, and awareness workflows",
    email: "Email reading and sending tools",
    feishu: "Feishu / Lark messaging and collaboration",
    okr: "Objectives, key results, and progress reporting",
    social: "Social publishing and community workflows",
    discovery: "Tool and capability discovery",
    custom: "Company-added or MCP tools",
    general: "General purpose tools",
  };
  const renderCategoryIcon = (category: string, size = 15) => {
    const style = { color: "var(--text-tertiary)" };
    switch (category) {
      case "agentbay":
        return <IconBrowser size={size} stroke={1.8} style={style} />;
      case "file":
        return <IconFileText size={size} stroke={1.8} style={style} />;
      case "communication":
      case "feishu":
      case "email":
      case "social":
        return <IconMessageCircle size={size} stroke={1.8} style={style} />;
      case "search":
      case "discovery":
        return <IconSearch size={size} stroke={1.8} style={style} />;
      case "code":
        return <IconTerminal2 size={size} stroke={1.8} style={style} />;
      case "aware":
        return <IconClock size={size} stroke={1.8} style={style} />;
      case "custom":
        return <IconSettings size={size} stroke={1.8} style={style} />;
      default:
        return <IconTools size={size} stroke={1.8} style={style} />;
    }
  };

  const switchTrack = (enabled: boolean, mixed = false): CSSProperties => ({
    position: "absolute",
    inset: 0,
    background: enabled
      ? "var(--accent-primary)"
      : mixed
        ? "var(--border-default)"
        : "var(--bg-tertiary)",
    borderRadius: "11px",
    transition: "background 0.2s",
  });

  const switchKnob = (enabled: boolean): CSSProperties => ({
    position: "absolute",
    left: enabled ? "20px" : "2px",
    top: "2px",
    width: "18px",
    height: "18px",
    background: "#fff",
    borderRadius: "50%",
    transition: "left 0.2s",
    boxShadow: "0 1px 3px rgba(0,0,0,0.12)",
  });

  const toggleCategoryExpanded = (category: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  };

  const bulkToggleCategory = async (
    catTools: AgentTool[],
    enabled: boolean,
  ) => {
    const catToolIds = new Set(catTools.map((t) => t.id));
    setTools((previous) => updateToolEnabled(previous, catToolIds, enabled));
    try {
      const token = localStorage.getItem("token");
      const payload = Array.from(catToolIds).map((id) => ({
        tool_id: id,
        enabled,
      }));
      await requestToolsMutation({
        url: `/api/tools/agents/${agentId}`,
        token,
        method: "PUT",
        body: payload,
        parseError: parseHttpErrorResponse,
      });
    } catch (error) {
      setTools((previous) => updateToolEnabled(previous, catToolIds, !enabled));
      toast.error(t("common.error.saveFailed", "Save failed"), {
        details: caughtErrorMessage(error),
      });
    }
  };

  const checkMcpAuthorization = async (
    groupKey: string,
    groupTools: AgentTool[],
  ) => {
    const authorizationTool = getSmitheryAuthorizationTool(groupTools);
    if (!authorizationTool?.id || checkingMcpAuthorizationGroup) return;

    const currentAuthorizationState =
      mcpAuthorizationStates[groupKey] || "unknown";
    // Authorize/Re-authorize opens while the click still carries a browser
    // gesture. A pure Check never flashes an empty window; if it discovers
    // auth is required, the reliable same-page fallback handles it.
    const authorizationWindow = shouldPreopenMcpAuthorizationWindow(
      currentAuthorizationState,
    )
      ? openMcpAuthorizationWindow()
      : null;
    setCheckingMcpAuthorizationGroup(groupKey);
    try {
      const token = localStorage.getItem("token") || "";
      const status = await requestMcpAuthorizationStatus(
        agentId,
        authorizationTool.id,
        token,
      );
      setMcpAuthorizationStates((prev) => ({
        ...prev,
        [groupKey]: status.state,
      }));
      if (status.state === "auth_required" && status.authorizationUrl) {
        navigateMcpAuthorizationWindow(
          authorizationWindow,
          status.authorizationUrl,
        );
      } else if (status.state === "connected") {
        closeMcpAuthorizationWindow(authorizationWindow);
        toast.success(
          t(
            "agent.tools.authorizationConnected",
            "This MCP connection is authorized.",
          ),
        );
      } else {
        closeMcpAuthorizationWindow(authorizationWindow);
        toast.warning(
          t(
            "agent.tools.authorizationUnavailable",
            "Authorization status is temporarily unavailable.",
          ),
        );
      }
    } catch (error) {
      closeMcpAuthorizationWindow(authorizationWindow);
      setMcpAuthorizationStates((prev) => ({
        ...prev,
        [groupKey]: "unavailable",
      }));
      toast.error(
        t(
          "agent.tools.authorizationCheckFailed",
          "Could not check MCP authorization.",
        ),
        { details: String(caughtErrorMessage(error) || error) },
      );
    } finally {
      setCheckingMcpAuthorizationGroup(null);
    }
  };

  const renderToolRow = (tool: AgentTool, category: string) => {
    const hasConfig =
      tool.config_schema?.fields?.length > 0 || tool.type === "mcp";
    const hasAgentOverride =
      tool.agent_config && Object.keys(tool.agent_config).length > 0;
    const isGlobalCategoryConfig =
      category === "agentbay" && tool.name === "agentbay_browser_navigate";
    return (
      <div
        key={tool.id}
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) auto",
          alignItems: "center",
          gap: "12px",
          padding: "10px 14px",
          borderTop: "1px solid var(--border-subtle)",
          background: "var(--bg-primary)",
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              minWidth: 0,
            }}
          >
            <span
              style={{
                fontWeight: 500,
                fontSize: "13px",
                color: "var(--text-primary)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {tool.display_name}
            </span>
            {tool.type === "mcp" && (
              <span
                style={{
                  fontSize: "10px",
                  background: "var(--primary)",
                  color: "#fff",
                  borderRadius: "4px",
                  padding: "1px 5px",
                  flexShrink: 0,
                }}
              >
                MCP
              </span>
            )}
            {tool.type === "builtin" && (
              <span
                style={{
                  fontSize: "10px",
                  background: "var(--bg-tertiary)",
                  color: "var(--text-secondary)",
                  borderRadius: "4px",
                  padding: "1px 5px",
                  flexShrink: 0,
                }}
              >
                Built-in
              </span>
            )}
            {hasAgentOverride && (
              <span
                style={{
                  fontSize: "10px",
                  background: "rgba(99,102,241,0.15)",
                  color: "var(--accent-color)",
                  borderRadius: "4px",
                  padding: "1px 5px",
                  flexShrink: 0,
                }}
              >
                {t("enterprise.tools.configured", "Configured")}
              </span>
            )}
          </div>
          <div
            style={{
              fontSize: "11px",
              color: "var(--text-tertiary)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {tool.description}
            {tool.mcp_server_name && <span> · {tool.mcp_server_name}</span>}
          </div>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            flexShrink: 0,
          }}
        >
          {canManage && hasConfig && !isGlobalCategoryConfig && (
            <button
              onClick={() => openConfig(tool)}
              style={{
                background: "none",
                border: "1px solid var(--border-subtle)",
                borderRadius: "6px",
                padding: "3px 8px",
                fontSize: "11px",
                cursor: "pointer",
                color: "var(--text-secondary)",
                display: "inline-flex",
                alignItems: "center",
                gap: "4px",
              }}
              title={t(
                "agent.tools.configurePerAgent",
                "Configure per-agent settings",
              )}
            >
              <IconSettings size={12} stroke={1.8} />{" "}
              {t("agent.tools.config", "Config")}
            </button>
          )}
          {canManage && tool.source === "agent" && tool.agent_tool_id && (
            <button
              onClick={async () => {
                const agentToolId = tool.agent_tool_id;
                if (!agentToolId) return;
                const ok = await dialog.confirm(
                  t(
                    "agent.tools.confirmDelete",
                    `Remove "${tool.display_name}" from this agent?`,
                  ),
                  {
                    danger: true,
                    confirmLabel: t("common.confirmActions.removeLabel"),
                  },
                );
                if (!ok) return;
                setDeletingToolId(tool.id);
                try {
                  const token = localStorage.getItem("token");
                  await requestToolsMutation({
                    url: `/api/tools/agent-tool/${encodeURIComponent(agentToolId)}`,
                    token,
                    method: "DELETE",
                    parseError: parseHttpErrorResponse,
                  });
                  await loadTools();
                } catch (error) {
                  toast.error(t("agent.tools.deleteFailed", "Delete failed"), {
                    details: String(caughtErrorMessage(error) || error),
                  });
                }
                setDeletingToolId(null);
              }}
              disabled={deletingToolId === tool.id}
              style={{
                background: "none",
                border: "1px solid var(--border-subtle)",
                borderRadius: "6px",
                padding: "3px 8px",
                fontSize: "11px",
                cursor: "pointer",
                color: "var(--text-tertiary)",
                opacity: deletingToolId === tool.id ? 0.5 : 1,
              }}
              title={t("agent.tools.removeTool", "Remove from agent")}
            >
              {deletingToolId === tool.id ? "..." : "✕"}
            </button>
          )}
          {canManage ? (
            <label
              style={{
                position: "relative",
                display: "inline-block",
                width: "40px",
                height: "22px",
                cursor: "pointer",
                flexShrink: 0,
              }}
            >
              <input
                type="checkbox"
                checked={tool.enabled}
                onChange={(e) => toggleTool(tool.id, e.target.checked)}
                style={{ opacity: 0, width: 0, height: 0 }}
              />
              <span style={switchTrack(tool.enabled)}>
                <span style={switchKnob(tool.enabled)} />
              </span>
            </label>
          ) : (
            <span
              style={{
                fontSize: "11px",
                color: tool.enabled
                  ? "var(--accent-primary)"
                  : "var(--text-tertiary)",
                fontWeight: 500,
              }}
            >
              {tool.enabled
                ? t("common.enabled", "On")
                : t("common.disabled", "Off")}
            </span>
          )}
        </div>
      </div>
    );
  };

  const renderToolGroup = (
    groupedTools: Record<string, AgentTool[]>,
    allGroupedTools: Record<string, AgentTool[]>,
  ) =>
    Object.entries(groupedTools)
      .sort(([a, aTools], [b, bTools]) => {
        const aMeta = getToolGroupMeta(a, allGroupedTools[a] || aTools);
        const bMeta = getToolGroupMeta(b, allGroupedTools[b] || bTools);
        return aMeta.label.localeCompare(bMeta.label);
      })
      .map(([category, catTools]) => {
        const allCatTools = allGroupedTools[category] || catTools;
        const meta = getToolGroupMeta(category, allCatTools);
        const label = meta.label;
        const enabledCount = allCatTools.filter((tool) => tool.enabled).length;
        const configuredCount = allCatTools.filter(
          (tool) =>
            tool.agent_config && Object.keys(tool.agent_config).length > 0,
        ).length;
        const allEnabled =
          allCatTools.length > 0 && enabledCount === allCatTools.length;
        const mixed = enabledCount > 0 && enabledCount < allCatTools.length;
        const expanded =
          expandedCategories.has(category) || !!toolSearch.trim();
        const visibleCount = catTools.length;
        const smitheryAuthorizationTool =
          getSmitheryAuthorizationTool(allCatTools);
        const authorizationState =
          mcpAuthorizationStates[category] || "unknown";
        const checkingAuthorization =
          checkingMcpAuthorizationGroup === category;
        return (
          <div
            key={category}
            style={{
              border: "1px solid var(--border-subtle)",
              borderRadius: "8px",
              overflow: "hidden",
              background: "var(--bg-primary)",
            }}
          >
            <div
              role="button"
              tabIndex={0}
              onClick={() => toggleCategoryExpanded(category)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggleCategoryExpanded(category);
                }
              }}
              style={{
                width: "100%",
                background: "var(--bg-secondary)",
                padding: "13px 16px",
                display: "grid",
                gridTemplateColumns: "1fr auto",
                gap: "14px",
                alignItems: "center",
                cursor: "pointer",
                textAlign: "left",
                boxSizing: "border-box",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "12px",
                  minWidth: 0,
                }}
              >
                <IconChevronDown
                  size={16}
                  style={{
                    transform: expanded ? "rotate(0deg)" : "rotate(-90deg)",
                    transition: "transform 120ms ease",
                    color: "var(--text-tertiary)",
                    flexShrink: 0,
                  }}
                />
                <span
                  style={{
                    width: "28px",
                    height: "28px",
                    borderRadius: "7px",
                    border: "1px solid var(--border-subtle)",
                    background: "var(--bg-primary)",
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  {renderCategoryIcon(meta.iconCategory, 16)}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      flexWrap: "wrap",
                    }}
                  >
                    <span
                      style={{
                        fontSize: "13px",
                        fontWeight: 650,
                        color: "var(--text-primary)",
                      }}
                    >
                      {label}
                    </span>
                    <span
                      style={{
                        fontSize: "11px",
                        color: "var(--text-tertiary)",
                      }}
                    >
                      {allCatTools.length} tools · {enabledCount} enabled
                      {visibleCount !== allCatTools.length
                        ? ` · ${visibleCount} shown`
                        : ""}
                    </span>
                    {configuredCount > 0 && (
                      <span
                        style={{
                          fontSize: "10px",
                          background: "rgba(99,102,241,0.15)",
                          color: "var(--accent-color)",
                          borderRadius: "4px",
                          padding: "1px 5px",
                        }}
                      >
                        {configuredCount} configured
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: "11px",
                      color: "var(--text-tertiary)",
                      marginTop: "2px",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {meta.description}
                  </div>
                </div>
              </div>
              <div
                style={{ display: "flex", alignItems: "center", gap: "8px" }}
                onClick={(e) => e.stopPropagation()}
              >
                {canManage && smitheryAuthorizationTool && (
                  <>
                    {authorizationState === "connected" && (
                      <span
                        style={{
                          fontSize: "10px",
                          background: "rgba(34,197,94,0.14)",
                          color: "#16a34a",
                          borderRadius: "4px",
                          padding: "2px 6px",
                          fontWeight: 600,
                        }}
                      >
                        {t("agent.tools.authorized", "Authorized")}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() =>
                        void checkMcpAuthorization(category, allCatTools)
                      }
                      disabled={checkingAuthorization}
                      style={{
                        background: "var(--bg-primary)",
                        border: "1px solid var(--border-subtle)",
                        borderRadius: "6px",
                        padding: "4px 8px",
                        fontSize: "11px",
                        cursor: checkingAuthorization ? "wait" : "pointer",
                        color: "var(--text-secondary)",
                        opacity: checkingAuthorization ? 0.65 : 1,
                      }}
                      title={t(
                        "agent.tools.checkAuthorization",
                        "Check MCP authorization",
                      )}
                    >
                      {checkingAuthorization
                        ? t("common.loading", "Loading...")
                        : t(
                            `agent.tools.${getSmitheryAuthorizationButtonLabel(authorizationState).replace("-", "")}`,
                            getSmitheryAuthorizationButtonLabel(
                              authorizationState,
                            ),
                          )}
                    </button>
                  </>
                )}
                {CATEGORY_CONFIG_SCHEMAS[meta.configCategory] && canManage && (
                  <button
                    type="button"
                    onClick={() => openCategoryConfig(meta.configCategory)}
                    style={{
                      background: "var(--bg-primary)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "6px",
                      padding: "4px 8px",
                      fontSize: "11px",
                      cursor: "pointer",
                      color: "var(--text-secondary)",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px",
                    }}
                    title={t(
                      "agent.tools.configureCategory",
                      "Configure {{category}}",
                      { category: label },
                    )}
                  >
                    <IconSettings size={12} stroke={1.8} />{" "}
                    {t("agent.tools.config", "Config")}
                  </button>
                )}
                {canManage && (
                  <label
                    style={{
                      position: "relative",
                      display: "inline-block",
                      width: "40px",
                      height: "22px",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                    title={t(
                      "agent.tools.enableDisableAll",
                      "Enable/Disable all {{category}} tools",
                      { category: label },
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={allEnabled}
                      onChange={(e) =>
                        void bulkToggleCategory(allCatTools, e.target.checked)
                      }
                      style={{ opacity: 0, width: 0, height: 0 }}
                    />
                    <span style={switchTrack(allEnabled, mixed)}>
                      <span style={switchKnob(allEnabled)} />
                    </span>
                  </label>
                )}
              </div>
            </div>
            {expanded && (
              <div>{catTools.map((tool) => renderToolRow(tool, category))}</div>
            )}
          </div>
        );
      });

  const activeTools =
    toolTab === "company" ? companyTools : agentInstalledTools;
  const normalizedToolSearch = toolSearch.trim().toLowerCase();
  const matchesToolSearch = (tool: AgentTool) => {
    if (!normalizedToolSearch) return true;
    const category = tool.category || "general";
    const haystack = [
      tool.name,
      tool.display_name,
      tool.description,
      tool.mcp_server_name,
      category,
      categoryLabels[category],
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(normalizedToolSearch);
  };
  const matchesStatusFilter = (tool: AgentTool) => {
    if (toolStatusFilter === "enabled") return !!tool.enabled;
    if (toolStatusFilter === "disabled") return !tool.enabled;
    if (toolStatusFilter === "configured")
      return !!(tool.agent_config && Object.keys(tool.agent_config).length > 0);
    return true;
  };
  const filteredTools = activeTools.filter(
    (tool) => matchesToolSearch(tool) && matchesStatusFilter(tool),
  );
  const groupedActiveTools = groupByCategory(activeTools);
  const groupedFilteredTools = groupByCategory(filteredTools);
  const hasFilters = !!normalizedToolSearch || toolStatusFilter !== "all";

  return (
    <>
      {loadErrorNotice}
      <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        <div
          className="tool-source-tabs"
          role="tablist"
          aria-label={t("agent.tools.sourceTabs", "Tool sources")}
        >
          <button
            type="button"
            role="tab"
            aria-selected={toolTab === "company"}
            className={toolTab === "company" ? "active" : ""}
            onClick={() => setToolTab("company")}
          >
            <span>{t("agent.tools.companyTools", "Company Tools")}</span>
            <span className="tool-source-tab-count">{companyTools.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={toolTab === "installed"}
            className={toolTab === "installed" ? "active" : ""}
            onClick={() => setToolTab("installed")}
          >
            <span>
              {t("agent.tools.agentInstalled", "Agent Self-Installed Tools")}
            </span>
            <span className="tool-source-tab-count">
              {agentInstalledTools.length}
            </span>
          </button>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            flexWrap: "wrap",
          }}
        >
          <div
            style={{
              position: "relative",
              flex: "1 1 260px",
              minWidth: "220px",
            }}
          >
            <IconSearch
              size={15}
              style={{
                position: "absolute",
                left: "10px",
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--text-tertiary)",
              }}
            />
            <input
              value={toolSearch}
              onChange={(e) => setToolSearch(e.target.value)}
              placeholder={t("agent.tools.searchTools", "Search tools...")}
              style={{
                width: "100%",
                boxSizing: "border-box",
                border: "1px solid var(--border-subtle)",
                borderRadius: "8px",
                background: "var(--bg-primary)",
                color: "var(--text-primary)",
                padding: "8px 10px 8px 32px",
                fontSize: "13px",
                outline: "none",
              }}
            />
          </div>
          {TOOL_STATUS_FILTERS.map((filter) => (
            <button
              key={filter}
              type="button"
              onClick={() => setToolStatusFilter(filter)}
              style={{
                border: "1px solid var(--border-subtle)",
                borderRadius: "999px",
                background:
                  toolStatusFilter === filter
                    ? "var(--text-primary)"
                    : "var(--bg-primary)",
                color:
                  toolStatusFilter === filter
                    ? "var(--bg-primary)"
                    : "var(--text-secondary)",
                padding: "6px 10px",
                fontSize: "11px",
                cursor: "pointer",
              }}
            >
              {filter === "all"
                ? t("common.all", "All")
                : filter === "enabled"
                  ? t("common.enabled", "Enabled")
                  : filter === "disabled"
                    ? t("common.disabled", "Disabled")
                    : t("agent.tools.configured", "Configured")}
            </button>
          ))}
          <button
            type="button"
            onClick={() => {
              const categories = Object.keys(groupedActiveTools);
              setExpandedCategories((prev) =>
                prev.size >= categories.length
                  ? new Set()
                  : new Set(categories),
              );
            }}
            style={{
              border: "1px solid var(--border-subtle)",
              borderRadius: "8px",
              background: "var(--bg-primary)",
              color: "var(--text-secondary)",
              padding: "6px 10px",
              fontSize: "11px",
              cursor: "pointer",
            }}
          >
            {expandedCategories.size >= Object.keys(groupedActiveTools).length
              ? t("agent.tools.collapseAll", "Collapse all")
              : t("agent.tools.expandAll", "Expand all")}
          </button>
        </div>

        {/* Tool List */}
        {filteredTools.length > 0 ? (
          renderToolGroup(groupedFilteredTools, groupedActiveTools)
        ) : (
          <div
            className="card"
            style={{
              textAlign: "center",
              padding: "30px",
              color: "var(--text-tertiary)",
            }}
          >
            {hasFilters
              ? t("agent.tools.noMatchingTools", "No matching tools")
              : toolTab === "installed"
                ? t("agent.tools.noInstalled", "No agent-installed tools yet")
                : t("agent.tools.noCompany", "No company-configured tools")}
          </div>
        )}
      </div>
      {tools.length === 0 && (
        <div
          className="card"
          style={{
            textAlign: "center",
            padding: "30px",
            color: "var(--text-tertiary)",
          }}
        >
          {t("common.noData")}
        </div>
      )}

      {/* Tool Config Modal */}
      {(configTool || configCategory) &&
        (() => {
          const schema = configTool
            ? configTool.config_schema
            : configCategory
              ? CATEGORY_CONFIG_SCHEMAS[configCategory]
              : undefined;
          if (!schema) return null;
          const fields = schema.fields;
          const title = configTool?.display_name ?? schema.title ?? "Config";
          const isCat = !!configCategory;
          const visibleFields = fields.filter((field) => {
            if (!field.depends_on) return true;
            return Object.entries(field.depends_on).every(
              ([dependencyKey, dependencyValues]) =>
                dependencyValues.includes(configData[dependencyKey] ?? ""),
            );
          });
          const primaryFields = visibleFields.filter(
            (field) => !field.advanced,
          );
          const advancedFields = visibleFields.filter(
            (field) => field.advanced,
          );
          return (
            <div
              style={{
                position: "fixed",
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                background: "rgba(0,0,0,0.55)",
                zIndex: 2000,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
              onClick={closeConfigModal}
            >
              <div
                onClick={(e) => e.stopPropagation()}
                style={{
                  background: "var(--bg-primary)",
                  borderRadius: "12px",
                  padding: "24px",
                  width: "480px",
                  maxWidth: "95vw",
                  maxHeight: "80vh",
                  overflow: "auto",
                  boxShadow: "0 20px 60px rgba(0,0,0,0.4)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "16px",
                  }}
                >
                  <div>
                    <h3
                      style={{
                        margin: 0,
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                      }}
                    >
                      <IconSettings size={20} stroke={1.8} /> {title}
                    </h3>
                    <div
                      style={{
                        fontSize: "11px",
                        color: "var(--text-tertiary)",
                        marginTop: "2px",
                      }}
                    >
                      {isCat
                        ? "Shared category configuration (affects all tools in this category)"
                        : "Per-agent configuration (overrides global defaults)"}
                    </div>
                  </div>
                  <button
                    onClick={closeConfigModal}
                    style={{
                      background: "none",
                      border: "none",
                      fontSize: "18px",
                      cursor: "pointer",
                      color: "var(--text-secondary)",
                    }}
                  >
                    ✕
                  </button>
                </div>

                {isCat && configLoadStatus !== "ready" && (
                  <div
                    role={configLoadStatus === "error" ? "alert" : "status"}
                    style={{
                      color:
                        configLoadStatus === "error"
                          ? "var(--error)"
                          : "var(--text-secondary)",
                      background: "var(--bg-secondary)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      marginBottom: "14px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: "10px",
                    }}
                  >
                    <span>
                      {configLoadStatus === "loading"
                        ? t("common.loading", "Loading...")
                        : configLoadError}
                    </span>
                    {configLoadStatus === "error" && configCategory && (
                      <button
                        type="button"
                        className="btn btn-secondary"
                        onClick={() => void openCategoryConfig(configCategory)}
                      >
                        {t("common.retry", "Retry")}
                      </button>
                    )}
                  </div>
                )}

                {fields.length > 0 ? (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "12px",
                    }}
                  >
                    {primaryFields.map((field) => {
                      // Get user role from store directly in the map function
                      const userFromStore = useAuthStore.getState().user;
                      const currentUserRole = userFromStore?.role;
                      const isReadOnly =
                        currentUserRole !== undefined &&
                        field.read_only_for_roles?.includes(currentUserRole);
                      return (
                        <div key={field.key}>
                          <label
                            style={{
                              display: "block",
                              fontSize: "12px",
                              fontWeight: 500,
                              marginBottom: "4px",
                            }}
                          >
                            {field.label}
                            {isReadOnly && (
                              <span
                                style={{
                                  fontWeight: 400,
                                  color: "var(--text-tertiary)",
                                  marginLeft: "4px",
                                }}
                              >
                                (Admin only)
                              </span>
                            )}
                            {/* Show company-configured value as a hint in the label */}
                            {(() => {
                              const globalVal =
                                configTool?.global_config?.[field.key] ??
                                configGlobalData?.[field.key];
                              if (!globalVal) return null;
                              return (
                                <span
                                  style={{
                                    fontWeight: 400,
                                    color: "var(--accent-primary)",
                                    marginLeft: "4px",
                                    fontSize: "11px",
                                  }}
                                >
                                  (company: {String(globalVal).slice(0, 20)}
                                  {String(globalVal).length > 20
                                    ? "\u2026"
                                    : ""}
                                  )
                                </span>
                              );
                            })()}
                          </label>
                          {field.type === "checkbox" ? (
                            <label
                              style={{
                                position: "relative",
                                display: "inline-block",
                                width: "40px",
                                height: "22px",
                                cursor: isReadOnly ? "not-allowed" : "pointer",
                              }}
                            >
                              <input
                                type="checkbox"
                                checked={Boolean(
                                  configData[field.key] ??
                                  field.default ??
                                  false,
                                )}
                                disabled={isReadOnly}
                                onChange={(e) =>
                                  setConfigData((p) => ({
                                    ...p,
                                    [field.key]: e.target.checked,
                                  }))
                                }
                                style={{ opacity: 0, width: 0, height: 0 }}
                              />
                              <span
                                style={{
                                  position: "absolute",
                                  inset: 0,
                                  background:
                                    (configData[field.key] ?? field.default)
                                      ? "var(--accent-primary)"
                                      : "var(--bg-tertiary)",
                                  borderRadius: "11px",
                                  transition: "background 0.2s",
                                  opacity: isReadOnly ? 0.6 : 1,
                                }}
                              >
                                <span
                                  style={{
                                    position: "absolute",
                                    left:
                                      (configData[field.key] ?? field.default)
                                        ? "20px"
                                        : "2px",
                                    top: "2px",
                                    width: "18px",
                                    height: "18px",
                                    background: "#fff",
                                    borderRadius: "50%",
                                    transition: "left 0.2s",
                                  }}
                                />
                              </span>
                            </label>
                          ) : field.type === "password" ? (
                            <>
                              {(() => {
                                const globalVal =
                                  configTool?.global_config?.[field.key] ??
                                  configGlobalData?.[field.key];
                                const isUsingGlobal =
                                  globalVal && !configData[field.key];

                                if (
                                  isUsingGlobal &&
                                  focusedField !== field.key
                                ) {
                                  return (
                                    <div
                                      className="form-input"
                                      style={{
                                        display: "flex",
                                        alignItems: "center",
                                        gap: "8px",
                                        cursor: "text",
                                        background: "var(--bg-tertiary)",
                                        borderColor: "var(--border)",
                                        overflow: "hidden",
                                      }}
                                      onClick={() => setFocusedField(field.key)}
                                    >
                                      <span
                                        style={{
                                          flex: 1,
                                          color: "var(--text-tertiary)",
                                          fontSize: "13px",
                                          overflow: "hidden",
                                          textOverflow: "ellipsis",
                                          whiteSpace: "nowrap",
                                        }}
                                      >
                                        {t(
                                          "agent.tools.usingCompanyKey",
                                          "Using company key ({{val}})",
                                          { val: globalVal },
                                        )}
                                      </span>
                                      <span
                                        style={{
                                          fontSize: "12px",
                                          color: "var(--accent-primary)",
                                          flexShrink: 0,
                                          cursor: "pointer",
                                        }}
                                      >
                                        {t("common.edit", "Edit")}
                                      </span>
                                    </div>
                                  );
                                }

                                return (
                                  <input
                                    type="password"
                                    autoComplete="new-password"
                                    className="form-input"
                                    autoFocus={focusedField === field.key}
                                    value={inputValue(configData[field.key])}
                                    placeholder={
                                      globalVal
                                        ? t(
                                            "agent.tools.usingCompanyKey",
                                            "Using company key ({{val}})",
                                            { val: globalVal },
                                          )
                                        : field.placeholder ||
                                          t(
                                            "admin.leaveBlankDefault",
                                            "Leave blank to use global default",
                                          )
                                    }
                                    onBlur={(e) => {
                                      if (!e.target.value)
                                        setFocusedField(null);
                                    }}
                                    onChange={(e) =>
                                      setConfigData((p) => ({
                                        ...p,
                                        [field.key]: e.target.value,
                                      }))
                                    }
                                  />
                                );
                              })()}
                              {/* Per-provider help text for auth_code */}
                              {field.key === "auth_code" &&
                                (() => {
                                  const providerField =
                                    configTool?.config_schema.fields.find(
                                      (candidate) =>
                                        candidate.key === "email_provider",
                                    );
                                  const selectedProvider =
                                    configData["email_provider"] ||
                                    providerField?.default ||
                                    "";
                                  const providerOption =
                                    providerField?.options?.find(
                                      (option) =>
                                        option.value === selectedProvider,
                                    );
                                  if (!providerOption?.help_text) return null;
                                  return (
                                    <div
                                      style={{
                                        fontSize: "11px",
                                        color: "var(--text-tertiary)",
                                        marginTop: "4px",
                                        lineHeight: "1.5",
                                      }}
                                    >
                                      {providerOption.help_text}
                                      {providerOption.help_url && (
                                        <>
                                          {" "}
                                          &middot;{" "}
                                          <a
                                            href={providerOption.help_url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            style={{
                                              color: "var(--accent-primary)",
                                              textDecoration: "none",
                                            }}
                                          >
                                            Setup guide
                                          </a>
                                        </>
                                      )}
                                    </div>
                                  );
                                })()}
                            </>
                          ) : field.type === "select" ? (
                            <select
                              className="form-input"
                              value={inputValue(
                                configData[field.key] ?? field.default,
                              )}
                              onChange={(e) =>
                                setConfigData((p) => ({
                                  ...p,
                                  [field.key]: e.target.value,
                                }))
                              }
                            >
                              {(field.options || []).map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          ) : field.type === "number" ? (
                            <input
                              type="number"
                              className="form-input"
                              value={inputValue(
                                configData[field.key] ?? field.default,
                              )}
                              placeholder={field.placeholder || ""}
                              min={field.min}
                              max={field.max}
                              onChange={(e) =>
                                setConfigData((p) => ({
                                  ...p,
                                  [field.key]: e.target.value
                                    ? Number(e.target.value)
                                    : "",
                                }))
                              }
                            />
                          ) : field.type === "textarea" ? (
                            <textarea
                              className="form-input"
                              value={inputValue(configData[field.key])}
                              placeholder={
                                field.placeholder ||
                                t(
                                  "admin.leaveBlankDefault",
                                  "Leave blank to use global default",
                                )
                              }
                              rows={Math.max(
                                3,
                                Math.min(
                                  10,
                                  String(
                                    configData[field.key] ??
                                      field.default ??
                                      field.placeholder ??
                                      "",
                                  ).split("\n").length,
                                ),
                              )}
                              style={{
                                minHeight: "88px",
                                fontFamily:
                                  "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)",
                                resize: "vertical",
                              }}
                              onChange={(e) =>
                                setConfigData((p) => ({
                                  ...p,
                                  [field.key]: e.target.value,
                                }))
                              }
                            />
                          ) : (
                            <>
                              {(() => {
                                const globalVal =
                                  configTool?.global_config?.[field.key] ??
                                  configGlobalData?.[field.key];
                                const isUsingGlobal =
                                  globalVal && !configData[field.key];

                                if (
                                  isUsingGlobal &&
                                  focusedField !== field.key
                                ) {
                                  return (
                                    <div
                                      className="form-input"
                                      style={{
                                        display: "flex",
                                        alignItems: "center",
                                        gap: "8px",
                                        cursor: "text",
                                        background: "var(--bg-tertiary)",
                                        borderColor: "var(--border)",
                                        overflow: "hidden",
                                      }}
                                      onClick={() => setFocusedField(field.key)}
                                    >
                                      <span
                                        style={{
                                          flex: 1,
                                          color: "var(--text-tertiary)",
                                          fontSize: "13px",
                                          overflow: "hidden",
                                          textOverflow: "ellipsis",
                                          whiteSpace: "nowrap",
                                        }}
                                      >
                                        {t(
                                          "agent.tools.usingCompanyConfig",
                                          "Using company config ({{val}})",
                                          { val: globalVal },
                                        )}
                                      </span>
                                      <span
                                        style={{
                                          fontSize: "12px",
                                          color: "var(--accent-primary)",
                                          flexShrink: 0,
                                          cursor: "pointer",
                                        }}
                                      >
                                        {t("common.edit", "Edit")}
                                      </span>
                                    </div>
                                  );
                                }

                                return (
                                  <input
                                    type="text"
                                    className="form-input"
                                    autoFocus={focusedField === field.key}
                                    value={inputValue(configData[field.key])}
                                    placeholder={
                                      globalVal
                                        ? t(
                                            "agent.tools.usingCompanyConfig",
                                            "Using company config ({{val}})",
                                            { val: globalVal },
                                          )
                                        : field.placeholder ||
                                          t(
                                            "admin.leaveBlankDefault",
                                            "Leave blank to use global default",
                                          )
                                    }
                                    onBlur={(e) => {
                                      if (!e.target.value)
                                        setFocusedField(null);
                                    }}
                                    onChange={(e) =>
                                      setConfigData((p) => ({
                                        ...p,
                                        [field.key]: e.target.value,
                                      }))
                                    }
                                  />
                                );
                              })()}
                            </>
                          )}
                        </div>
                      );
                    })}
                    {advancedFields.length > 0 && (
                      <div
                        style={{
                          borderTop: "1px solid var(--border-subtle)",
                          paddingTop: "10px",
                          marginTop: "2px",
                        }}
                      >
                        <button
                          type="button"
                          className="btn btn-ghost"
                          onClick={() => setShowAdvancedToolConfig((v) => !v)}
                          style={{
                            padding: 0,
                            minWidth: "auto",
                            fontSize: "12px",
                            color: "var(--text-secondary)",
                          }}
                        >
                          {showAdvancedToolConfig
                            ? "Hide advanced settings"
                            : "Advanced settings"}
                        </button>
                        {showAdvancedToolConfig && (
                          <div
                            style={{
                              display: "flex",
                              flexDirection: "column",
                              gap: "12px",
                              marginTop: "12px",
                            }}
                          >
                            {advancedFields.map((field) => {
                              const userFromStore =
                                useAuthStore.getState().user;
                              const currentUserRole = userFromStore?.role;
                              const isReadOnly =
                                currentUserRole !== undefined &&
                                field.read_only_for_roles?.includes(
                                  currentUserRole,
                                );
                              return (
                                <div key={field.key}>
                                  <label
                                    style={{
                                      display: "block",
                                      fontSize: "12px",
                                      fontWeight: 500,
                                      marginBottom: "4px",
                                    }}
                                  >
                                    {field.label}
                                    {isReadOnly && (
                                      <span
                                        style={{
                                          fontWeight: 400,
                                          color: "var(--text-tertiary)",
                                          marginLeft: "4px",
                                        }}
                                      >
                                        (Admin only)
                                      </span>
                                    )}
                                  </label>
                                  {field.type === "checkbox" ? (
                                    <label
                                      style={{
                                        position: "relative",
                                        display: "inline-block",
                                        width: "40px",
                                        height: "22px",
                                        cursor: isReadOnly
                                          ? "not-allowed"
                                          : "pointer",
                                      }}
                                    >
                                      <input
                                        type="checkbox"
                                        checked={Boolean(
                                          configData[field.key] ??
                                          field.default ??
                                          false,
                                        )}
                                        disabled={isReadOnly}
                                        onChange={(e) =>
                                          setConfigData((p) => ({
                                            ...p,
                                            [field.key]: e.target.checked,
                                          }))
                                        }
                                        style={{
                                          opacity: 0,
                                          width: 0,
                                          height: 0,
                                        }}
                                      />
                                      <span
                                        style={{
                                          position: "absolute",
                                          inset: 0,
                                          background:
                                            (configData[field.key] ??
                                            field.default)
                                              ? "var(--accent-primary)"
                                              : "var(--bg-tertiary)",
                                          borderRadius: "11px",
                                          transition: "background 0.2s",
                                          opacity: isReadOnly ? 0.6 : 1,
                                        }}
                                      >
                                        <span
                                          style={{
                                            position: "absolute",
                                            left:
                                              (configData[field.key] ??
                                              field.default)
                                                ? "20px"
                                                : "2px",
                                            top: "2px",
                                            width: "18px",
                                            height: "18px",
                                            background: "#fff",
                                            borderRadius: "50%",
                                            transition: "left 0.2s",
                                          }}
                                        />
                                      </span>
                                    </label>
                                  ) : field.type === "select" ? (
                                    <select
                                      className="form-input"
                                      value={inputValue(
                                        configData[field.key] ?? field.default,
                                      )}
                                      disabled={isReadOnly}
                                      onChange={(e) =>
                                        setConfigData((p) => ({
                                          ...p,
                                          [field.key]: e.target.value,
                                        }))
                                      }
                                    >
                                      {(field.options || []).map((option) => (
                                        <option
                                          key={option.value}
                                          value={option.value}
                                        >
                                          {option.label}
                                        </option>
                                      ))}
                                    </select>
                                  ) : field.type === "number" ? (
                                    <input
                                      type="number"
                                      className="form-input"
                                      value={inputValue(
                                        configData[field.key] ?? field.default,
                                      )}
                                      disabled={isReadOnly}
                                      placeholder={field.placeholder || ""}
                                      min={field.min}
                                      max={field.max}
                                      onChange={(e) =>
                                        setConfigData((p) => ({
                                          ...p,
                                          [field.key]: e.target.value
                                            ? Number(e.target.value)
                                            : "",
                                        }))
                                      }
                                    />
                                  ) : field.type === "textarea" ? (
                                    <textarea
                                      className="form-input"
                                      value={inputValue(
                                        configData[field.key] ?? field.default,
                                      )}
                                      disabled={isReadOnly}
                                      placeholder={
                                        field.placeholder ||
                                        t(
                                          "admin.leaveBlankDefault",
                                          "Leave blank to use global default",
                                        )
                                      }
                                      rows={Math.max(
                                        3,
                                        Math.min(
                                          10,
                                          String(
                                            configData[field.key] ??
                                              field.default ??
                                              field.placeholder ??
                                              "",
                                          ).split("\n").length,
                                        ),
                                      )}
                                      style={{
                                        minHeight: "88px",
                                        fontFamily:
                                          "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)",
                                        resize: "vertical",
                                      }}
                                      onChange={(e) =>
                                        setConfigData((p) => ({
                                          ...p,
                                          [field.key]: e.target.value,
                                        }))
                                      }
                                    />
                                  ) : (
                                    <input
                                      type={
                                        field.type === "password"
                                          ? "password"
                                          : "text"
                                      }
                                      autoComplete={
                                        field.type === "password"
                                          ? "new-password"
                                          : undefined
                                      }
                                      className="form-input"
                                      value={inputValue(
                                        configData[field.key] ?? field.default,
                                      )}
                                      disabled={isReadOnly}
                                      placeholder={
                                        field.placeholder ||
                                        t(
                                          "admin.leaveBlankDefault",
                                          "Leave blank to use global default",
                                        )
                                      }
                                      onChange={(e) =>
                                        setConfigData((p) => ({
                                          ...p,
                                          [field.key]: e.target.value,
                                        }))
                                      }
                                    />
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    )}
                    {/* Email tool: test connection button + help text */}
                    {configTool?.category === "email" && (
                      <div
                        style={{
                          borderTop: "1px solid var(--border-subtle)",
                          paddingTop: "12px",
                          display: "flex",
                          flexDirection: "column",
                          gap: "8px",
                        }}
                      >
                        <button
                          className="btn btn-secondary"
                          style={{ alignSelf: "flex-start" }}
                          onClick={async () => {
                            const btn =
                              document.getElementById("email-test-btn");
                            const status =
                              document.getElementById("email-test-status");
                            if (btn) btn.textContent = "Testing...";
                            if (btn instanceof HTMLButtonElement) {
                              btn.disabled = true;
                            }
                            try {
                              const token = localStorage.getItem("token");
                              const data = await requestToolsJson({
                                url: "/api/tools/test-email",
                                token,
                                method: "POST",
                                body: { config: configData },
                                parsePayload: parseEmailTestResponse,
                                parseError: parseHttpErrorResponse,
                              });
                              if (status) {
                                status.textContent = data.ok
                                  ? `${data.imap}\n${data.smtp}`
                                  : `${data.imap || ""}\n${data.smtp || ""}\n${data.error || ""}`;
                                status.style.color = data.ok
                                  ? "var(--success)"
                                  : "var(--error)";
                              }
                            } catch (error) {
                              if (status) {
                                status.textContent = `Error: ${caughtErrorMessage(error)}`;
                                status.style.color = "var(--error)";
                              }
                            } finally {
                              if (btn) {
                                btn.textContent = "Test Connection";
                                if (btn instanceof HTMLButtonElement) {
                                  btn.disabled = false;
                                }
                              }
                            }
                          }}
                          id="email-test-btn"
                        >
                          Test Connection
                        </button>
                        <div
                          id="email-test-status"
                          style={{
                            fontSize: "11px",
                            whiteSpace: "pre-line",
                            minHeight: "16px",
                          }}
                        ></div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div>
                    <label
                      style={{
                        display: "block",
                        fontSize: "12px",
                        fontWeight: 500,
                        marginBottom: "4px",
                      }}
                    >
                      Config JSON (Agent Override)
                    </label>
                    <textarea
                      className="form-input"
                      value={configJson}
                      onChange={(e) => setConfigJson(e.target.value)}
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "12px",
                        minHeight: "120px",
                        resize: "vertical",
                      }}
                      placeholder="{}"
                    />
                    <div
                      style={{
                        fontSize: "11px",
                        color: "var(--text-tertiary)",
                        marginTop: "4px",
                      }}
                    >
                      Global default:{" "}
                      <code style={{ fontSize: "10px" }}>
                        {JSON.stringify(configTool?.global_config || {}).slice(
                          0,
                          80,
                        )}
                      </code>
                    </div>
                  </div>
                )}

                <div
                  style={{
                    display: "flex",
                    gap: "8px",
                    marginTop: "16px",
                    justifyContent: "flex-end",
                  }}
                >
                  {configTool &&
                    configTool.agent_config &&
                    Object.keys(configTool.agent_config || {}).length > 0 && (
                      <button
                        className="btn btn-ghost"
                        style={{ color: "var(--error)", marginRight: "auto" }}
                        onClick={() => void resetToolConfig()}
                        disabled={configSaving}
                      >
                        Reset to Global
                      </button>
                    )}
                  {isCat && (
                    <button
                      className="btn btn-secondary"
                      style={{ marginRight: "auto" }}
                      onClick={async () => {
                        const category = configCategory;
                        if (!category) return;
                        const btn = document.getElementById("cat-test-btn");
                        if (btn) btn.textContent = "Testing...";
                        try {
                          const token = localStorage.getItem("token");
                          const data = await requestToolsJson({
                            url: `/api/tools/agents/${encodeURIComponent(agentId)}/category-config/${encodeURIComponent(category)}/test`,
                            token,
                            method: "POST",
                            parsePayload: parseCategoryTestResponse,
                            parseError: parseHttpErrorResponse,
                          });
                          if (data.ok) {
                            await dialog.alert(
                              data.message || t("common.error.testSuccess"),
                              {
                                type: "success",
                                title: t("common.model.connectivityTest"),
                              },
                            );
                          } else {
                            await dialog.alert(t("common.error.testFailed"), {
                              type: "error",
                              title: t("common.model.connectivityTest"),
                              details:
                                typeof data.error === "string"
                                  ? data.error
                                  : JSON.stringify(data, null, 2),
                            });
                          }
                        } catch (error) {
                          await dialog.alert(t("common.error.testFailed"), {
                            type: "error",
                            title: t("common.model.connectivityTest"),
                            details: String(caughtErrorMessage(error) || error),
                          });
                        } finally {
                          if (btn) btn.textContent = "Test Connection";
                        }
                      }}
                      id="cat-test-btn"
                      disabled={configLoadStatus !== "ready"}
                    >
                      Test Connection
                    </button>
                  )}
                  <button
                    className="btn btn-secondary"
                    onClick={closeConfigModal}
                  >
                    Cancel
                  </button>
                  <button
                    className="btn btn-primary"
                    onClick={saveConfig}
                    disabled={
                      configSaving || (isCat && configLoadStatus !== "ready")
                    }
                  >
                    {configSaving
                      ? t("common.saving", "Saving…")
                      : t("common.save", "Save")}
                  </button>
                </div>
              </div>
            </div>
          );
        })()}
    </>
  );
}
