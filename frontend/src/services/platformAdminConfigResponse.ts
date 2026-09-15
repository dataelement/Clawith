export interface ParsedPlatformSettings {
  allow_self_create_company: boolean;
  invitation_code_enabled: boolean;
  sso_custom_domain_redirect_enabled: boolean;
}

export interface ParsedNotificationBarValue {
  enabled: boolean;
  text: string;
}

export interface ParsedSystemEmailConfig {
  SYSTEM_EMAIL_ENABLED: boolean;
  SYSTEM_EMAIL_FROM_ADDRESS: string;
  SYSTEM_EMAIL_FROM_NAME: string;
  SYSTEM_SMTP_HOST: string;
  SYSTEM_SMTP_PORT: number;
  SYSTEM_SMTP_USERNAME: string;
  SYSTEM_SMTP_PASSWORD: string;
  SYSTEM_SMTP_SSL: boolean;
  SYSTEM_SMTP_TIMEOUT_SECONDS: number;
}

export interface ParsedSystemSetting<T> {
  value?: T;
  updated_at?: string | null;
}

export interface ParsedEmailTemplate {
  subject: string;
  body: string;
}

export interface ParsedEmailTemplates {
  templates?: Record<string, ParsedEmailTemplate>;
  variables?: Record<string, string[]>;
  defaults?: Record<string, ParsedEmailTemplate>;
}

export interface ParsedIdentityProvider {
  id: string;
  provider_type: string;
  name: string;
  is_active: boolean;
  config: {
    client_id?: string;
    app_id?: string;
    client_secret?: string;
    app_secret?: string;
    scope?: string;
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireString(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  if (typeof value !== "string") throw new Error(`Invalid ${key}`);
  return value;
}

function requireBoolean(record: Record<string, unknown>, key: string): boolean {
  const value = record[key];
  if (typeof value !== "boolean") throw new Error(`Invalid ${key}`);
  return value;
}

function requireNumber(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isFinite(value))
    throw new Error(`Invalid ${key}`);
  return value;
}

function optionalString(
  record: Record<string, unknown>,
  key: string,
): string | undefined {
  if (!(key in record) || record[key] === undefined) return undefined;
  return requireString(record, key);
}

function parseSystemSetting<T>(
  value: unknown,
  parseValue: (value: unknown) => T,
): ParsedSystemSetting<T> {
  if (!isRecord(value)) throw new Error("Invalid system setting response");
  if (!("value" in value)) throw new Error("Invalid system setting value");
  const updatedAt = value.updated_at;
  if (
    updatedAt !== undefined &&
    updatedAt !== null &&
    typeof updatedAt !== "string"
  ) {
    throw new Error("Invalid system setting updated_at");
  }
  return {
    value: parseValue(value.value),
    ...(updatedAt !== undefined ? { updated_at: updatedAt } : {}),
  };
}

export function parsePlatformSettings(value: unknown): ParsedPlatformSettings {
  if (!isRecord(value)) throw new Error("Invalid platform settings response");
  return {
    allow_self_create_company: requireBoolean(
      value,
      "allow_self_create_company",
    ),
    invitation_code_enabled: requireBoolean(value, "invitation_code_enabled"),
    sso_custom_domain_redirect_enabled: requireBoolean(
      value,
      "sso_custom_domain_redirect_enabled",
    ),
  };
}

export function parseNotificationBarSetting(
  value: unknown,
): ParsedSystemSetting<ParsedNotificationBarValue> {
  return parseSystemSetting(value, (setting) => {
    if (!isRecord(setting)) throw new Error("Invalid notification bar setting");
    const enabled = setting.enabled;
    const text = setting.text;
    if (enabled !== undefined && typeof enabled !== "boolean")
      throw new Error("Invalid enabled");
    if (text !== undefined && typeof text !== "string")
      throw new Error("Invalid text");
    return {
      enabled: enabled ?? false,
      text: text ?? "",
    };
  });
}

export function parseSystemEmailSetting(
  value: unknown,
): ParsedSystemSetting<ParsedSystemEmailConfig> {
  return parseSystemSetting(value, (setting) => {
    if (!isRecord(setting)) throw new Error("Invalid system email setting");
    const stringValue = (key: string, fallback = "") => {
      if (setting[key] === undefined) return fallback;
      return requireString(setting, key);
    };
    const booleanValue = (key: string, fallback: boolean) => {
      if (setting[key] === undefined) return fallback;
      return requireBoolean(setting, key);
    };
    const numberValue = (key: string, fallback: number) => {
      if (setting[key] === undefined) return fallback;
      return requireNumber(setting, key);
    };
    const fromAddress = stringValue("SYSTEM_EMAIL_FROM_ADDRESS");
    const smtpHost = stringValue("SYSTEM_SMTP_HOST");
    return {
      SYSTEM_EMAIL_ENABLED: booleanValue(
        "SYSTEM_EMAIL_ENABLED",
        Boolean(fromAddress && smtpHost),
      ),
      SYSTEM_EMAIL_FROM_ADDRESS: fromAddress,
      SYSTEM_EMAIL_FROM_NAME: stringValue("SYSTEM_EMAIL_FROM_NAME", "Clawith"),
      SYSTEM_SMTP_HOST: smtpHost,
      SYSTEM_SMTP_PORT: numberValue("SYSTEM_SMTP_PORT", 465),
      SYSTEM_SMTP_USERNAME: stringValue("SYSTEM_SMTP_USERNAME"),
      SYSTEM_SMTP_PASSWORD: stringValue("SYSTEM_SMTP_PASSWORD"),
      SYSTEM_SMTP_SSL: booleanValue("SYSTEM_SMTP_SSL", true),
      SYSTEM_SMTP_TIMEOUT_SECONDS: numberValue(
        "SYSTEM_SMTP_TIMEOUT_SECONDS",
        15,
      ),
    };
  });
}

function parseEmailTemplate(value: unknown): ParsedEmailTemplate {
  if (!isRecord(value)) throw new Error("Invalid email template");
  return {
    subject: requireString(value, "subject"),
    body: requireString(value, "body"),
  };
}

function parseTemplateRecord(
  value: unknown,
): Record<string, ParsedEmailTemplate> {
  if (!isRecord(value)) throw new Error("Invalid email templates response");
  return Object.fromEntries(
    Object.entries(value).map(([key, template]) => [
      key,
      parseEmailTemplate(template),
    ]),
  );
}

export function parseEmailTemplates(value: unknown): ParsedEmailTemplates {
  if (!isRecord(value)) throw new Error("Invalid email templates response");
  if (
    !("templates" in value) ||
    !("variables" in value) ||
    !("defaults" in value)
  ) {
    throw new Error("Invalid email templates response");
  }
  if (!isRecord(value.variables))
    throw new Error("Invalid email template variables");
  const variables = Object.fromEntries(
    Object.entries(value.variables).map(([key, items]) => {
      if (
        !Array.isArray(items) ||
        !items.every((item) => typeof item === "string")
      )
        throw new Error("Invalid email template variables");
      return [key, items];
    }),
  );
  return {
    templates: parseTemplateRecord(value.templates),
    variables,
    defaults: parseTemplateRecord(value.defaults),
  };
}

export function parseIdentityProviders(
  value: unknown,
): ParsedIdentityProvider[] {
  if (!Array.isArray(value))
    throw new Error("Invalid identity providers response");
  return value.map((provider) => {
    if (!isRecord(provider)) throw new Error("Invalid identity provider");
    let config: ParsedIdentityProvider["config"] = {};
    if (provider.config !== undefined && provider.config !== null) {
      if (!isRecord(provider.config))
        throw new Error("Invalid identity provider config");
      const parsedConfig = {
        client_id: optionalString(provider.config, "client_id"),
        app_id: optionalString(provider.config, "app_id"),
        client_secret: optionalString(provider.config, "client_secret"),
        app_secret: optionalString(provider.config, "app_secret"),
        scope: optionalString(provider.config, "scope"),
      };
      config = Object.fromEntries(
        Object.entries(parsedConfig).filter(([, item]) => item !== undefined),
      );
    }
    return {
      id: requireString(provider, "id"),
      provider_type: requireString(provider, "provider_type"),
      name: requireString(provider, "name"),
      is_active: requireBoolean(provider, "is_active"),
      config,
    };
  });
}
