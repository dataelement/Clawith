import { AppError } from "./apiError.ts";
import type { ResponseParser } from "./apiResponseParsers";
import type {
  Group,
  GroupError,
  GroupMember,
  GroupMemberCandidate,
  GroupMention,
  GroupMessage,
  GroupMessageIntake,
  GroupRunState,
  GroupSession,
  GroupSessionSummary,
  GroupTextFile,
  GroupWorkspaceEntry,
} from "../types/group";

function invalid(path: string, expected: string): never {
  throw new AppError({
    message: `Invalid API response at ${path}: expected ${expected}`,
    code: "invalid_api_response",
    source: "http",
    retryable: false,
    details: { path, expected },
  });
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    invalid(path, "object");
  return Object.fromEntries(Object.entries(value));
}

function string(value: unknown, path: string): string {
  if (typeof value !== "string") invalid(path, "string");
  return value;
}

function number(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value))
    invalid(path, "finite number");
  return value;
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") invalid(path, "boolean");
  return value;
}

function nullableString(value: unknown, path: string): string | null {
  return value === null ? null : string(value, path);
}

function array<T>(
  value: unknown,
  path: string,
  parser: (item: unknown, itemPath: string) => T,
): T[] {
  if (!Array.isArray(value)) invalid(path, "array");
  return value.map((item, index) => parser(item, `${path}[${index}]`));
}

function participantType(value: unknown, path: string): "user" | "agent" {
  if (value !== "user" && value !== "agent") invalid(path, "participant type");
  return value;
}

function group(value: unknown, path: string): Group {
  const item = record(value, path);
  return {
    id: string(item.id, `${path}.id`),
    tenant_id: string(item.tenant_id, `${path}.tenant_id`),
    name: string(item.name, `${path}.name`),
    description: nullableString(item.description, `${path}.description`),
    created_by_participant_id: string(
      item.created_by_participant_id,
      `${path}.created_by_participant_id`,
    ),
    created_at: string(item.created_at, `${path}.created_at`),
    updated_at: string(item.updated_at, `${path}.updated_at`),
  };
}

export const parseGroupResponse: ResponseParser<Group> = (value) =>
  group(value, "response");
export const parseGroupsResponse: ResponseParser<Group[]> = (value) =>
  array(value, "response", group);

function member(value: unknown, path: string): GroupMember {
  const item = record(value, path);
  const role = item.role;
  if (role !== "manager" && role !== "member")
    invalid(`${path}.role`, "group role");
  return {
    id: string(item.id, `${path}.id`),
    participant_id: string(item.participant_id, `${path}.participant_id`),
    participant_type: participantType(
      item.participant_type,
      `${path}.participant_type`,
    ),
    participant_ref_id: string(
      item.participant_ref_id,
      `${path}.participant_ref_id`,
    ),
    display_name: string(item.display_name, `${path}.display_name`),
    avatar_url: nullableString(item.avatar_url, `${path}.avatar_url`),
    role,
    role_description: nullableString(
      item.role_description,
      `${path}.role_description`,
    ),
    title: nullableString(item.title, `${path}.title`),
    is_deleted: boolean(item.is_deleted, `${path}.is_deleted`),
    joined_at: string(item.joined_at, `${path}.joined_at`),
  };
}

export const parseGroupMemberResponse: ResponseParser<GroupMember> = (value) =>
  member(value, "response");
export const parseGroupMembersResponse: ResponseParser<GroupMember[]> = (
  value,
) => array(value, "response", member);

function candidate(value: unknown, path: string): GroupMemberCandidate {
  const item = record(value, path);
  return {
    participant_id: string(item.participant_id, `${path}.participant_id`),
    participant_type: participantType(
      item.participant_type,
      `${path}.participant_type`,
    ),
    participant_ref_id: string(
      item.participant_ref_id,
      `${path}.participant_ref_id`,
    ),
    display_name: string(item.display_name, `${path}.display_name`),
    avatar_url: nullableString(item.avatar_url, `${path}.avatar_url`),
    role_description: nullableString(
      item.role_description,
      `${path}.role_description`,
    ),
    title: nullableString(item.title, `${path}.title`),
  };
}

export const parseGroupCandidatesResponse: ResponseParser<
  GroupMemberCandidate[]
> = (value) => array(value, "response", candidate);

function session(value: unknown, path: string): GroupSession {
  const item = record(value, path);
  return {
    id: string(item.id, `${path}.id`),
    group_id: string(item.group_id, `${path}.group_id`),
    title: string(item.title, `${path}.title`),
    is_primary: boolean(item.is_primary, `${path}.is_primary`),
    unread_count: number(item.unread_count, `${path}.unread_count`),
    created_by_participant_id: nullableString(
      item.created_by_participant_id,
      `${path}.created_by_participant_id`,
    ),
    created_at: string(item.created_at, `${path}.created_at`),
    updated_at: string(item.updated_at, `${path}.updated_at`),
    last_message_at: nullableString(
      item.last_message_at,
      `${path}.last_message_at`,
    ),
  };
}

export const parseGroupSessionResponse: ResponseParser<GroupSession> = (
  value,
) => session(value, "response");
export const parseGroupSessionsResponse: ResponseParser<GroupSession[]> = (
  value,
) => array(value, "response", session);

function mention(value: unknown, path: string): GroupMention {
  const item = record(value, path);
  return {
    participant_id: string(item.participant_id, `${path}.participant_id`),
    ...(item.participant_type === undefined
      ? {}
      : {
          participant_type: participantType(
            item.participant_type,
            `${path}.participant_type`,
          ),
        }),
    ...(item.display_name === undefined
      ? {}
      : { display_name: string(item.display_name, `${path}.display_name`) }),
  };
}

function message(value: unknown, path: string): GroupMessage {
  const item = record(value, path);
  const role = item.role;
  if (role !== "user" && role !== "assistant" && role !== "system")
    invalid(`${path}.role`, "message role");
  return {
    id: string(item.id, `${path}.id`),
    role,
    content: string(item.content, `${path}.content`),
    participant_id: nullableString(
      item.participant_id,
      `${path}.participant_id`,
    ),
    sender_name: nullableString(item.sender_name, `${path}.sender_name`),
    mentions: array(item.mentions, `${path}.mentions`, mention),
    created_at: string(item.created_at, `${path}.created_at`),
    cursor: string(item.cursor, `${path}.cursor`),
  };
}

export const parseGroupMessagesResponse: ResponseParser<GroupMessage[]> = (
  value,
) => array(value, "response", message);

function groupError(value: unknown, path: string): GroupError {
  const item = record(value, path);
  const stage = item.stage;
  if (
    stage !== null &&
    stage !== "planning" &&
    stage !== "execution" &&
    stage !== "delivery"
  )
    invalid(`${path}.stage`, "group error stage");
  const retryable = item.retryable;
  if (retryable !== null && typeof retryable !== "boolean")
    invalid(`${path}.retryable`, "boolean or null");
  structuredValue(item.details, `${path}.details`);
  return {
    code: string(item.code, `${path}.code`),
    message: string(item.message, `${path}.message`),
    trace_id: string(item.trace_id, `${path}.trace_id`),
    run_id: nullableString(item.run_id, `${path}.run_id`),
    agent_id: nullableString(item.agent_id, `${path}.agent_id`),
    stage,
    details: item.details,
    retryable,
  };
}

export const parseGroupMessageIntakeResponse: ResponseParser<
  GroupMessageIntake
> = (value) => {
  const item = record(value, "response");
  const dispatchKind = item.dispatch_kind;
  if (
    dispatchKind !== "none" &&
    dispatchKind !== "single" &&
    dispatchKind !== "planning"
  )
    invalid("response.dispatch_kind", "dispatch kind");
  return {
    message: message(item.message, "response.message"),
    dispatch_kind: dispatchKind,
    run_ids: array(item.run_ids, "response.run_ids", string),
    created: boolean(item.created, "response.created"),
    error_code: nullableString(item.error_code, "response.error_code"),
    ...(item.error === undefined
      ? {}
      : {
          error:
            item.error === null
              ? null
              : groupError(item.error, "response.error"),
        }),
  };
};

function runState(value: unknown, path: string): GroupRunState {
  const item = record(value, path);
  return {
    run_id: string(item.run_id, `${path}.run_id`),
    status: string(item.status, `${path}.status`),
    can_cancel: boolean(item.can_cancel, `${path}.can_cancel`),
    agent_id: nullableString(item.agent_id, `${path}.agent_id`),
    system_role: nullableString(item.system_role, `${path}.system_role`),
  };
}

export const parseGroupRunStateResponse: ResponseParser<GroupRunState> = (
  value,
) => runState(value, "response");
export const parseGroupRunStatesResponse: ResponseParser<GroupRunState[]> = (
  value,
) => array(value, "response", runState);

function textFile(value: unknown, path: string): GroupTextFile {
  const item = record(value, path);
  return {
    path: string(item.path, `${path}.path`),
    content: string(item.content, `${path}.content`),
    exists: boolean(item.exists, `${path}.exists`),
    version_token: nullableString(item.version_token, `${path}.version_token`),
    modified_at: nullableString(item.modified_at, `${path}.modified_at`),
    revision_id: nullableString(item.revision_id, `${path}.revision_id`),
  };
}

export const parseGroupTextFileResponse: ResponseParser<GroupTextFile> = (
  value,
) => textFile(value, "response");

function workspaceEntry(value: unknown, path: string): GroupWorkspaceEntry {
  const item = record(value, path);
  return {
    path: string(item.path, `${path}.path`),
    name: string(item.name, `${path}.name`),
    is_dir: boolean(item.is_dir, `${path}.is_dir`),
    size: number(item.size, `${path}.size`),
    modified_at: string(item.modified_at, `${path}.modified_at`),
    version_token: nullableString(item.version_token, `${path}.version_token`),
  };
}

export const parseGroupWorkspaceResponse: ResponseParser<
  GroupWorkspaceEntry[]
> = (value) => array(value, "response", workspaceEntry);

export const parseGroupSessionSummaryResponse: ResponseParser<
  GroupSessionSummary
> = (value) => {
  const item = record(value, "response");
  return {
    version: number(item.version, "response.version"),
    summary: string(item.summary, "response.summary"),
    requirements: array(item.requirements, "response.requirements", (entry) =>
      structuredValue(entry, "response.requirements"),
    ),
    decisions: array(item.decisions, "response.decisions", (entry) =>
      structuredValue(entry, "response.decisions"),
    ),
    open_items: array(item.open_items, "response.open_items", (entry) =>
      structuredValue(entry, "response.open_items"),
    ),
    evidence_refs: array(
      item.evidence_refs,
      "response.evidence_refs",
      (entry) => structuredValue(entry, "response.evidence_refs"),
    ),
    workspace_refs: array(
      item.workspace_refs,
      "response.workspace_refs",
      (entry) => structuredValue(entry, "response.workspace_refs"),
    ),
    covered_through_message_id: nullableString(
      item.covered_through_message_id,
      "response.covered_through_message_id",
    ),
  };
};

function structuredValue(value: unknown, path: string): unknown {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  )
    return value;
  if (Array.isArray(value)) {
    value.forEach((item, index) => structuredValue(item, `${path}[${index}]`));
    return value;
  }
  const item = record(value, path);
  Object.entries(item).forEach(([key, nested]) =>
    structuredValue(nested, `${path}.${key}`),
  );
  return value;
}

export const parseGroupReadReceiptResponse: ResponseParser<{
  session_id: string;
  last_read_message_id: string;
  advanced: boolean;
}> = (value) => {
  const item = record(value, "response");
  return {
    session_id: string(item.session_id, "response.session_id"),
    last_read_message_id: string(
      item.last_read_message_id,
      "response.last_read_message_id",
    ),
    advanced: boolean(item.advanced, "response.advanced"),
  };
};
