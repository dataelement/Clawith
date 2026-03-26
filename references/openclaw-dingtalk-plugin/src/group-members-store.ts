import * as fs from "node:fs";
import * as path from "node:path";
import { readNamespaceJson, writeNamespaceJsonAtomic } from "./persistence-store";

const GROUP_MEMBERS_NAMESPACE = "members.group-roster";

function groupMembersFilePath(storePath: string, groupId: string): string {
  const dir = path.join(path.dirname(storePath), "dingtalk-members");
  const safeId = groupId.replace(/\+/g, "-").replace(/\//g, "_");
  return path.join(dir, `${safeId}.json`);
}

function readLegacyRoster(storePath: string, groupId: string): Record<string, string> | null {
  const filePath = groupMembersFilePath(storePath, groupId);
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf-8")) as Record<string, string>;
  } catch {
    return null;
  }
}

function readRoster(storePath: string, groupId: string): Record<string, string> {
  const namespaced = readNamespaceJson<Record<string, string>>(GROUP_MEMBERS_NAMESPACE, {
    storePath,
    scope: { groupId },
    format: "json",
    fallback: {},
  });
  if (Object.keys(namespaced).length > 0) {
    return namespaced;
  }

  const legacy = readLegacyRoster(storePath, groupId);
  if (legacy && Object.keys(legacy).length > 0) {
    writeNamespaceJsonAtomic(GROUP_MEMBERS_NAMESPACE, {
      storePath,
      scope: { groupId },
      format: "json",
      data: legacy,
    });
    return legacy;
  }
  return {};
}

function writeRoster(storePath: string, groupId: string, roster: Record<string, string>): void {
  writeNamespaceJsonAtomic(GROUP_MEMBERS_NAMESPACE, {
    storePath,
    scope: { groupId },
    format: "json",
    data: roster,
  });
}

export function noteGroupMember(
  storePath: string,
  groupId: string,
  userId: string,
  name: string,
): void {
  if (!userId || !name) {
    return;
  }
  const roster = readRoster(storePath, groupId);
  if (roster[userId] === name) {
    return;
  }
  roster[userId] = name;
  writeRoster(storePath, groupId, roster);
}

export function formatGroupMembers(storePath: string, groupId: string): string | undefined {
  const roster = readRoster(storePath, groupId);
  const entries = Object.entries(roster);
  if (entries.length === 0) {
    return undefined;
  }
  return entries.map(([id, name]) => `${name} (${id})`).join(", ");
}
