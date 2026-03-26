export interface CardCallbackAnalysis {
  summary: string;
  actionId?: string;
  feedbackTarget?: string;
  feedbackAckText?: string;
  userId?: string;
  processQueryKey?: string;
}

function stringifyCandidate(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "[unserializable]";
  }
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function parseEmbeddedJson(value: unknown): unknown {
  if (typeof value !== "string") {
    return value;
  }
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

export function extractCardActionSummary(data: unknown): string {
  const record = asRecord(data);
  const candidates = [
    record?.action,
    record?.actionType,
    record?.actionValue,
    record?.value,
    record?.eventType,
    record?.operate,
    record?.callbackType,
    record?.cardPrivateData,
    record?.privateData,
  ].filter((value) => value !== undefined && value !== null);

  if (candidates.length === 0) {
    return "(no action field found)";
  }

  return candidates.map(stringifyCandidate).join(" | ");
}

export function extractCardActionId(data: unknown): string | undefined {
  const record = asRecord(data);
  const embeddedValue = parseEmbeddedJson(record?.value);
  const embeddedContent = parseEmbeddedJson(record?.content);

  for (const source of [embeddedValue, embeddedContent, record].filter(Boolean)) {
    const sourceRecord = asRecord(source);
    const cardPrivateData = asRecord(sourceRecord?.cardPrivateData);
    const actionIds = cardPrivateData?.actionIds;
    if (Array.isArray(actionIds) && actionIds.length > 0 && typeof actionIds[0] === "string") {
      return actionIds[0];
    }
    if (typeof sourceRecord?.actionValue === "string" && sourceRecord.actionValue.trim()) {
      return sourceRecord.actionValue.trim();
    }
    if (typeof sourceRecord?.eventKey === "string" && sourceRecord.eventKey.trim()) {
      return sourceRecord.eventKey.trim();
    }
    if (typeof sourceRecord?.value === "string" && sourceRecord.value.trim()) {
      return sourceRecord.value.trim();
    }
  }

  return undefined;
}

export function analyzeCardCallback(data: unknown): CardCallbackAnalysis {
  const record = asRecord(data);
  const summary = extractCardActionSummary(data);
  const actionId = extractCardActionId(data);
  const embeddedValue = asRecord(parseEmbeddedJson(record?.value));
  const embeddedContent = asRecord(parseEmbeddedJson(record?.content));
  const processQueryKey =
    (typeof record?.processQueryKey === "string" && record.processQueryKey.trim()) ||
    (typeof embeddedValue?.processQueryKey === "string" && embeddedValue.processQueryKey.trim()) ||
    (typeof embeddedContent?.processQueryKey === "string" && embeddedContent.processQueryKey.trim()) ||
    undefined;

  if (actionId !== "feedback_up" && actionId !== "feedback_down") {
    return { summary, actionId, processQueryKey };
  }

  const spaceType = typeof record?.spaceType === "string" ? record.spaceType.trim().toLowerCase() : "";
  const spaceId = typeof record?.spaceId === "string" ? record.spaceId.trim() : "";
  const userId = typeof record?.userId === "string" ? record.userId.trim() : "";
  const feedbackTarget = spaceType === "im" ? userId : spaceId;
  const feedbackAckText =
    actionId === "feedback_up"
      ? "✅ 已收到你的点赞（反馈已记录）"
      : "⚠️ 已收到你的点踩（反馈已记录，我会改进）";

  return {
    summary,
    actionId,
    feedbackTarget: feedbackTarget || undefined,
    feedbackAckText,
    userId: userId || undefined,
    processQueryKey,
  };
}
