import type { TokenResponse, User } from "../types";

export interface OAuthTenantChoice {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  logo_url?: string;
}

export interface OAuthTenantSelectionResponse {
  requires_tenant_selection: true;
  login_identifier: string;
  tenants: OAuthTenantChoice[];
  pending_token: string;
}

export type OAuthCallbackResponse =
  TokenResponse | OAuthTenantSelectionResponse;

const USER_ROLES: User["role"][] = [
  "platform_admin",
  "org_admin",
  "agent_admin",
  "member",
];

function isUserRole(value: string): value is User["role"] {
  return USER_ROLES.some((role) => role === value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value) {
    throw new Error(`Invalid OAuth callback response: ${field}`);
  }
  return value;
}

function stringValue(value: unknown, field: string): string {
  if (typeof value !== "string") {
    throw new Error(`Invalid OAuth callback response: ${field}`);
  }
  return value;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function parseUser(value: unknown): User {
  if (!isRecord(value)) {
    throw new Error("Invalid OAuth callback response: user");
  }
  const role = requiredString(value.role, "user.role");
  if (!isUserRole(role)) {
    throw new Error("Invalid OAuth callback response: user.role");
  }
  if (typeof value.is_active !== "boolean") {
    throw new Error("Invalid OAuth callback response: user.is_active");
  }
  const avatarUrl = optionalString(value.avatar_url);
  const tenantId = optionalString(value.tenant_id);
  const title = optionalString(value.title);
  return {
    id: requiredString(value.id, "user.id"),
    username: optionalString(value.username) ?? "",
    email: optionalString(value.email) ?? "",
    display_name: requiredString(value.display_name, "user.display_name"),
    role,
    is_active: value.is_active,
    created_at: requiredString(value.created_at, "user.created_at"),
    ...(avatarUrl !== undefined ? { avatar_url: avatarUrl } : {}),
    ...(typeof value.is_platform_admin === "boolean"
      ? { is_platform_admin: value.is_platform_admin }
      : {}),
    ...(tenantId !== undefined ? { tenant_id: tenantId } : {}),
    ...(title !== undefined ? { title } : {}),
    ...(typeof value.email_verified === "boolean"
      ? { email_verified: value.email_verified }
      : {}),
  };
}

function parseTenantChoice(value: unknown): OAuthTenantChoice {
  if (!isRecord(value)) {
    throw new Error("Invalid OAuth callback response: tenant");
  }
  const logoUrl = optionalString(value.logo_url);
  return {
    tenant_id: requiredString(value.tenant_id, "tenant.tenant_id"),
    tenant_name: requiredString(value.tenant_name, "tenant.tenant_name"),
    tenant_slug: stringValue(value.tenant_slug, "tenant.tenant_slug"),
    ...(logoUrl !== undefined ? { logo_url: logoUrl } : {}),
  };
}

export function parseOAuthCallbackResponse(
  value: unknown,
): OAuthCallbackResponse {
  if (!isRecord(value)) {
    throw new Error("Invalid OAuth callback response");
  }
  if (value.requires_tenant_selection === true) {
    if (!Array.isArray(value.tenants)) {
      throw new Error("Invalid OAuth callback response: tenants");
    }
    return {
      requires_tenant_selection: true,
      login_identifier: stringValue(value.login_identifier, "login_identifier"),
      tenants: value.tenants
        .filter((tenant) => !isRecord(tenant) || tenant.tenant_id !== null)
        .map(parseTenantChoice),
      pending_token: requiredString(value.pending_token, "pending_token"),
    };
  }
  if (typeof value.needs_company_setup !== "boolean") {
    throw new Error("Invalid OAuth callback response: needs_company_setup");
  }
  return {
    access_token: requiredString(value.access_token, "access_token"),
    token_type: requiredString(value.token_type, "token_type"),
    user: parseUser(value.user),
    needs_company_setup: value.needs_company_setup,
  };
}
