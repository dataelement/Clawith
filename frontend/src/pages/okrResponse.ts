type JsonRecord = Record<string, unknown>;

export interface OKRSettings {
  enabled: boolean;
  first_enabled_at: string | null;
  daily_report_enabled: boolean;
  daily_report_time: string;
  daily_report_skip_non_workdays: boolean;
  weekly_report_enabled: boolean;
  weekly_report_day: number;
  period_frequency: string;
  period_length_days: number | null;
  period_frequency_locked: boolean;
  okr_agent_id: string | null;
}

export interface KeyResult {
  id: string;
  objective_id: string;
  title: string;
  target_value: number;
  current_value: number;
  unit: string | null;
  focus_ref: string | null;
  status: string;
  last_updated_at: string;
  created_at: string;
}

export interface Objective {
  id: string;
  title: string;
  description: string | null;
  owner_type: string;
  owner_id: string | null;
  owner_name: string | null;
  period_start: string;
  period_end: string;
  status: string;
  created_at: string;
  key_results: KeyResult[];
}

export interface Period {
  start: string;
  end: string;
  label: string;
  is_current: boolean;
}

export interface CompanyReport {
  id: string;
  report_type: "daily" | "weekly" | "monthly";
  period_start: string;
  period_end: string;
  period_label: string;
  content: string;
  submitted_count: number;
  missing_count: number;
  needs_refresh: boolean;
  generated_at: string;
  updated_at: string;
}

export interface MemberDailyReportItem {
  id: string;
  member_type: "user" | "agent";
  member_id: string;
  display_name: string;
  avatar_url: string | null;
  group_label: string;
  report_date: string;
  content: string;
  status: string;
  submitted_at: string | null;
  updated_at: string | null;
}

export interface MemberWithoutOKR {
  id: string;
  type: "user" | "agent";
  display_name: string;
  avatar_url: string;
  channel: string | null;
  channel_user_id: string | null;
  source_label: string | null;
}

export interface ChannelWarning {
  channel_type: string;
  channel_display: string;
  affected_members: string[];
  count: number;
}

export interface MembersWithoutOKRData {
  period_start: string;
  period_end: string;
  company_okr_exists: boolean;
  okr_agent_id: string | null;
  members_without_okr: MemberWithoutOKR[];
  tracked_user_ids: string[];
  tracked_agent_ids: string[];
  total: number;
  last_outreach_error: {
    message: string;
    timestamp: string;
    is_read: boolean;
  } | null;
  channel_warnings: ChannelWarning[];
}

export type OutreachResult =
  | {
      status: "accepted";
      message: string;
      members_count: number;
      okr_agent_id: string;
    }
  | {
      status: "no_action";
      message: string;
      okr_agent_id: string;
    };

function invalid(path: string, expected: string): never {
  throw new Error(`Invalid OKR response at ${path}: expected ${expected}`);
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function record(value: unknown, path: string): JsonRecord {
  if (!isRecord(value)) return invalid(path, "object");
  return value;
}

function string(value: unknown, path: string): string {
  return typeof value === "string" ? value : invalid(path, "string");
}

function boolean(value: unknown, path: string): boolean {
  return typeof value === "boolean" ? value : invalid(path, "boolean");
}

function number(value: unknown, path: string): number {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : invalid(path, "finite number");
}

function integer(value: unknown, path: string): number {
  const parsed = number(value, path);
  return Number.isInteger(parsed) ? parsed : invalid(path, "integer");
}

function nullableString(value: unknown, path: string): string | null {
  return value === null ? null : string(value, path);
}

function optionalNullableString(value: unknown, path: string): string | null {
  return value === undefined || value === null ? null : string(value, path);
}

function array(value: unknown, path: string): unknown[] {
  return Array.isArray(value) ? value : invalid(path, "array");
}

function stringArray(value: unknown, path: string): string[] {
  return array(value, path).map((item, index) =>
    string(item, `${path}[${index}]`),
  );
}

function memberType(value: unknown, path: string): "user" | "agent" {
  if (value === "user" || value === "agent") return value;
  return invalid(path, '"user" or "agent"');
}

function reportType(
  value: unknown,
  path: string,
): "daily" | "weekly" | "monthly" {
  if (value === "daily" || value === "weekly" || value === "monthly") {
    return value;
  }
  return invalid(path, '"daily", "weekly", or "monthly"');
}

function parseKeyResult(value: unknown, path: string): KeyResult {
  const item = record(value, path);
  return {
    id: string(item.id, `${path}.id`),
    objective_id: string(item.objective_id, `${path}.objective_id`),
    title: string(item.title, `${path}.title`),
    target_value: number(item.target_value, `${path}.target_value`),
    current_value: number(item.current_value, `${path}.current_value`),
    unit: nullableString(item.unit, `${path}.unit`),
    focus_ref: nullableString(item.focus_ref, `${path}.focus_ref`),
    status: string(item.status, `${path}.status`),
    last_updated_at: string(item.last_updated_at, `${path}.last_updated_at`),
    created_at: string(item.created_at, `${path}.created_at`),
  };
}

function parseObjective(value: unknown, path: string): Objective {
  const item = record(value, path);
  return {
    id: string(item.id, `${path}.id`),
    title: string(item.title, `${path}.title`),
    description: nullableString(item.description, `${path}.description`),
    owner_type: string(item.owner_type, `${path}.owner_type`),
    owner_id: nullableString(item.owner_id, `${path}.owner_id`),
    owner_name: nullableString(item.owner_name, `${path}.owner_name`),
    period_start: string(item.period_start, `${path}.period_start`),
    period_end: string(item.period_end, `${path}.period_end`),
    status: string(item.status, `${path}.status`),
    created_at: string(item.created_at, `${path}.created_at`),
    key_results: array(item.key_results, `${path}.key_results`).map(
      (keyResult, index) =>
        parseKeyResult(keyResult, `${path}.key_results[${index}]`),
    ),
  };
}

function parsePeriod(value: unknown, path: string): Period {
  const item = record(value, path);
  return {
    start: string(item.start, `${path}.start`),
    end: string(item.end, `${path}.end`),
    label: string(item.label, `${path}.label`),
    is_current: boolean(item.is_current, `${path}.is_current`),
  };
}

function parseCompanyReport(value: unknown, path: string): CompanyReport {
  const item = record(value, path);
  return {
    id: string(item.id, `${path}.id`),
    report_type: reportType(item.report_type, `${path}.report_type`),
    period_start: string(item.period_start, `${path}.period_start`),
    period_end: string(item.period_end, `${path}.period_end`),
    period_label: string(item.period_label, `${path}.period_label`),
    content: string(item.content, `${path}.content`),
    submitted_count: integer(item.submitted_count, `${path}.submitted_count`),
    missing_count: integer(item.missing_count, `${path}.missing_count`),
    needs_refresh: boolean(item.needs_refresh, `${path}.needs_refresh`),
    generated_at: string(item.generated_at, `${path}.generated_at`),
    updated_at: string(item.updated_at, `${path}.updated_at`),
  };
}

function parseMemberDailyReport(
  value: unknown,
  path: string,
): MemberDailyReportItem {
  const item = record(value, path);
  return {
    id: string(item.id, `${path}.id`),
    member_type: memberType(item.member_type, `${path}.member_type`),
    member_id: string(item.member_id, `${path}.member_id`),
    display_name: string(item.display_name, `${path}.display_name`),
    avatar_url: nullableString(item.avatar_url, `${path}.avatar_url`),
    group_label: string(item.group_label, `${path}.group_label`),
    report_date: string(item.report_date, `${path}.report_date`),
    content: string(item.content, `${path}.content`),
    status: string(item.status, `${path}.status`),
    submitted_at: nullableString(item.submitted_at, `${path}.submitted_at`),
    updated_at: nullableString(item.updated_at, `${path}.updated_at`),
  };
}

function parseMemberWithoutOKR(value: unknown, path: string): MemberWithoutOKR {
  const item = record(value, path);
  return {
    id: string(item.id, `${path}.id`),
    type: memberType(item.type, `${path}.type`),
    display_name: string(item.display_name, `${path}.display_name`),
    avatar_url: string(item.avatar_url, `${path}.avatar_url`),
    channel: nullableString(item.channel, `${path}.channel`),
    channel_user_id: nullableString(
      item.channel_user_id,
      `${path}.channel_user_id`,
    ),
    source_label: optionalNullableString(
      item.source_label,
      `${path}.source_label`,
    ),
  };
}

function parseChannelWarning(value: unknown, path: string): ChannelWarning {
  const item = record(value, path);
  return {
    channel_type: string(item.channel_type, `${path}.channel_type`),
    channel_display: string(item.channel_display, `${path}.channel_display`),
    affected_members: stringArray(
      item.affected_members,
      `${path}.affected_members`,
    ),
    count: integer(item.count, `${path}.count`),
  };
}

export function parseOKRSettings(value: unknown): OKRSettings {
  const item = record(value, "response");
  return {
    enabled: boolean(item.enabled, "response.enabled"),
    first_enabled_at: nullableString(
      item.first_enabled_at,
      "response.first_enabled_at",
    ),
    daily_report_enabled: boolean(
      item.daily_report_enabled,
      "response.daily_report_enabled",
    ),
    daily_report_time: string(
      item.daily_report_time,
      "response.daily_report_time",
    ),
    daily_report_skip_non_workdays: boolean(
      item.daily_report_skip_non_workdays,
      "response.daily_report_skip_non_workdays",
    ),
    weekly_report_enabled: boolean(
      item.weekly_report_enabled,
      "response.weekly_report_enabled",
    ),
    weekly_report_day: integer(
      item.weekly_report_day,
      "response.weekly_report_day",
    ),
    period_frequency: string(
      item.period_frequency,
      "response.period_frequency",
    ),
    period_length_days:
      item.period_length_days === null
        ? null
        : integer(item.period_length_days, "response.period_length_days"),
    period_frequency_locked: boolean(
      item.period_frequency_locked,
      "response.period_frequency_locked",
    ),
    okr_agent_id: nullableString(item.okr_agent_id, "response.okr_agent_id"),
  };
}

export function parsePeriods(value: unknown): Period[] {
  return array(value, "response").map((period, index) =>
    parsePeriod(period, `response[${index}]`),
  );
}

export function parseObjectives(value: unknown): Objective[] {
  return array(value, "response").map((objective, index) =>
    parseObjective(objective, `response[${index}]`),
  );
}

export function parseMembersWithoutOKR(value: unknown): MembersWithoutOKRData {
  const item = record(value, "response");
  const outreachError = item.last_outreach_error;
  let lastOutreachError: MembersWithoutOKRData["last_outreach_error"] = null;
  if (outreachError !== null) {
    const error = record(outreachError, "response.last_outreach_error");
    lastOutreachError = {
      message: string(error.message, "response.last_outreach_error.message"),
      timestamp: string(
        error.timestamp,
        "response.last_outreach_error.timestamp",
      ),
      is_read: boolean(error.is_read, "response.last_outreach_error.is_read"),
    };
  }
  return {
    period_start: string(item.period_start, "response.period_start"),
    period_end: string(item.period_end, "response.period_end"),
    company_okr_exists: boolean(
      item.company_okr_exists,
      "response.company_okr_exists",
    ),
    okr_agent_id: nullableString(item.okr_agent_id, "response.okr_agent_id"),
    members_without_okr: array(
      item.members_without_okr,
      "response.members_without_okr",
    ).map((member, index) =>
      parseMemberWithoutOKR(member, `response.members_without_okr[${index}]`),
    ),
    tracked_user_ids: stringArray(
      item.tracked_user_ids,
      "response.tracked_user_ids",
    ),
    tracked_agent_ids: stringArray(
      item.tracked_agent_ids,
      "response.tracked_agent_ids",
    ),
    total: integer(item.total, "response.total"),
    last_outreach_error: lastOutreachError,
    channel_warnings: array(
      item.channel_warnings,
      "response.channel_warnings",
    ).map((warning, index) =>
      parseChannelWarning(warning, `response.channel_warnings[${index}]`),
    ),
  };
}

export function parseOutreachResult(value: unknown): OutreachResult {
  const item = record(value, "response");
  const message = string(item.message, "response.message");
  const okrAgentId = string(item.okr_agent_id, "response.okr_agent_id");
  if (item.status === "accepted") {
    return {
      status: item.status,
      message,
      members_count: integer(item.members_count, "response.members_count"),
      okr_agent_id: okrAgentId,
    };
  }
  if (item.status === "no_action") {
    return { status: item.status, message, okr_agent_id: okrAgentId };
  }
  return invalid("response.status", '"accepted" or "no_action"');
}

export function parseCompanyReports(value: unknown): CompanyReport[] {
  return array(value, "response").map((report, index) =>
    parseCompanyReport(report, `response[${index}]`),
  );
}

export function parseMemberDailyReports(
  value: unknown,
): MemberDailyReportItem[] {
  return array(value, "response").map((report, index) =>
    parseMemberDailyReport(report, `response[${index}]`),
  );
}

export function parseCompanyReportResponse(value: unknown): CompanyReport {
  return parseCompanyReport(value, "response");
}
