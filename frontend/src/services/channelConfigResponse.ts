export interface StoredChannelConfig {
  is_configured: boolean;
  is_connected: boolean;
  app_id?: string;
  cloud_id?: string;
  extra_config?: {
    connection_mode?: string;
    session_expired?: boolean;
    ilink_user_id?: string;
    bot_id?: string;
    bot_secret?: string;
    wecom_agent_id?: string;
    tenant_id?: string;
    agent_id?: string;
  } | null;
}

export interface WebhookConfig {
  webhook_url: string;
}

export interface AtlassianTestResultResponse {
  ok: boolean;
  message?: string;
  tool_count?: number;
  error?: string;
}

export function parseAtlassianTestResult(
  value: unknown,
): AtlassianTestResultResponse {
  if (!isRecord(value) || typeof value.ok !== "boolean")
    throw new Error("Invalid Atlassian test response");
  const message = optionalString(value, "message");
  const error = optionalString(value, "error");
  const toolCount = value.tool_count;
  if (
    toolCount !== undefined &&
    (typeof toolCount !== "number" || !Number.isFinite(toolCount))
  )
    throw new Error("Invalid Atlassian tool count");
  return {
    ok: value.ok,
    ...(message !== undefined ? { message } : {}),
    ...(toolCount !== undefined ? { tool_count: toolCount } : {}),
    ...(error !== undefined ? { error } : {}),
  };
}

export function parseWechatQr(value: unknown): {
  qrcode: string;
  qrcode_img_content: string;
} {
  if (!isRecord(value)) throw new Error("Invalid WeChat QR response");
  const qrcode = optionalString(value, "qrcode");
  const imageContent = optionalString(value, "qrcode_img_content");
  if (!qrcode || !imageContent) throw new Error("Invalid WeChat QR response");
  return {
    qrcode,
    qrcode_img_content: imageContent,
  };
}

export function parseWechatQrStatus(value: unknown): { status: string } {
  if (!isRecord(value)) throw new Error("Invalid WeChat QR status response");
  const status = optionalString(value, "status");
  if (!status) throw new Error("Invalid WeChat QR status response");
  return { status };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function optionalString(
  record: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = record[key];
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string") {
    throw new Error(`Invalid channel configuration field: ${key}`);
  }
  return value;
}

function optionalBoolean(
  record: Record<string, unknown>,
  key: string,
): boolean | undefined {
  const value = record[key];
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "boolean") {
    throw new Error(`Invalid channel configuration field: ${key}`);
  }
  return value;
}

export function parseStoredChannelConfig(value: unknown): StoredChannelConfig {
  if (!isRecord(value))
    throw new Error("Invalid channel configuration response");
  if (
    typeof value.is_configured !== "boolean" ||
    typeof value.is_connected !== "boolean"
  ) {
    throw new Error("Invalid channel configuration response");
  }

  let extraConfig: StoredChannelConfig["extra_config"];
  if (value.extra_config === null || value.extra_config === undefined) {
    extraConfig = value.extra_config;
  } else if (isRecord(value.extra_config)) {
    const connectionMode = optionalString(
      value.extra_config,
      "connection_mode",
    );
    const sessionExpired = optionalBoolean(
      value.extra_config,
      "session_expired",
    );
    const ilinkUserId = optionalString(value.extra_config, "ilink_user_id");
    const botId = optionalString(value.extra_config, "bot_id");
    const botSecret = optionalString(value.extra_config, "bot_secret");
    const wecomAgentId = optionalString(value.extra_config, "wecom_agent_id");
    const tenantId = optionalString(value.extra_config, "tenant_id");
    const agentId = optionalString(value.extra_config, "agent_id");
    extraConfig = {
      ...(connectionMode !== undefined
        ? { connection_mode: connectionMode }
        : {}),
      ...(sessionExpired !== undefined
        ? { session_expired: sessionExpired }
        : {}),
      ...(ilinkUserId !== undefined ? { ilink_user_id: ilinkUserId } : {}),
      ...(botId !== undefined ? { bot_id: botId } : {}),
      ...(botSecret !== undefined ? { bot_secret: botSecret } : {}),
      ...(wecomAgentId !== undefined ? { wecom_agent_id: wecomAgentId } : {}),
      ...(tenantId !== undefined ? { tenant_id: tenantId } : {}),
      ...(agentId !== undefined ? { agent_id: agentId } : {}),
    };
  } else {
    throw new Error("Invalid channel configuration response");
  }

  const appId = optionalString(value, "app_id");
  const cloudId = optionalString(value, "cloud_id");
  return {
    is_configured: value.is_configured,
    is_connected: value.is_connected,
    ...(appId !== undefined ? { app_id: appId } : {}),
    ...(cloudId !== undefined ? { cloud_id: cloudId } : {}),
    ...(extraConfig !== undefined ? { extra_config: extraConfig } : {}),
  };
}

export function parseWebhookConfig(value: unknown): WebhookConfig {
  if (
    !isRecord(value) ||
    typeof value.webhook_url !== "string" ||
    !value.webhook_url
  ) {
    throw new Error("Invalid channel webhook response");
  }
  return { webhook_url: value.webhook_url };
}

function errorStatus(error: unknown): number | undefined {
  return isRecord(error) && typeof error.status === "number"
    ? error.status
    : undefined;
}

export async function readOptionalChannelResource<T>(
  load: () => Promise<unknown>,
  parse: (value: unknown) => T,
): Promise<T | null> {
  try {
    return parse(await load());
  } catch (error) {
    if (errorStatus(error) === 404) return null;
    throw error;
  }
}
