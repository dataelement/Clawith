export type NormalizedAllowFrom = {
  entries: string[];
  entriesLower: string[];
  hasWildcard: boolean;
  hasEntries: boolean;
};

/**
 * Normalize allowFrom list:
 * - trim whitespace
 * - support "dingtalk:/dd:/ding:" prefixes
 * - precompute lower-case list for case-insensitive checks
 */
export function normalizeAllowFrom(list?: Array<string>): NormalizedAllowFrom {
  const entries = (list ?? []).map((value) => String(value).trim()).filter(Boolean);
  const hasWildcard = entries.includes("*");
  const normalized = entries
    .filter((value) => value !== "*")
    .map((value) => value.replace(/^(dingtalk|dd|ding):/i, ""));
  const normalizedLower = normalized.map((value) => value.toLowerCase());
  return {
    entries: normalized,
    entriesLower: normalizedLower,
    hasWildcard,
    hasEntries: entries.length > 0,
  };
}

export function isSenderAllowed(params: {
  allow: NormalizedAllowFrom;
  senderId?: string;
}): boolean {
  const { allow, senderId } = params;
  if (!allow.hasEntries) {
    return true;
  }
  if (allow.hasWildcard) {
    return true;
  }
  if (senderId && allow.entriesLower.includes(senderId.toLowerCase())) {
    return true;
  }
  return false;
}

export function isSenderGroupAllowed(params: {
  allow: NormalizedAllowFrom;
  groupId?: string;
}): boolean {
  const { allow, groupId } = params;
  if (groupId && allow.entriesLower.includes(groupId.toLowerCase())) {
    return true;
  }
  return false;
}

export function isSenderOwner(params: {
  allow: NormalizedAllowFrom;
  senderId?: string;
  rawSenderId?: string;
}): boolean {
  const { allow, senderId, rawSenderId } = params;
  if (!allow.hasEntries) {
    return false;
  }
  if (senderId && allow.entriesLower.includes(senderId.toLowerCase())) {
    return true;
  }
  if (rawSenderId && allow.entriesLower.includes(rawSenderId.toLowerCase())) {
    return true;
  }
  return false;
}
