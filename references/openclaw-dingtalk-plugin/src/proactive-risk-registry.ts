type ProactiveRiskLevel = "low" | "medium" | "high";

export interface ProactiveRiskObservation {
  accountId: string;
  targetId: string;
  level: ProactiveRiskLevel;
  reason: string;
  source: string;
  observedAtMs?: number;
}

export interface ProactiveRiskSnapshot {
  level: ProactiveRiskLevel;
  reason: string;
  source: string;
  observedAtMs: number;
}

const DEFAULT_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const store = new Map<string, ProactiveRiskSnapshot>();

function keyOf(accountId: string, targetId: string): string {
  return `${accountId}:${targetId.trim()}`;
}

export function recordProactiveRiskObservation(observation: ProactiveRiskObservation): void {
  const targetId = observation.targetId?.trim();
  if (!observation.accountId || !targetId) {
    return;
  }

  store.set(keyOf(observation.accountId, targetId), {
    level: observation.level,
    reason: observation.reason,
    source: observation.source,
    observedAtMs: observation.observedAtMs ?? Date.now(),
  });
}

export function getProactiveRiskObservation(
  accountId: string,
  targetId: string,
  nowMs = Date.now(),
): ProactiveRiskSnapshot | null {
  const target = targetId?.trim();
  if (!accountId || !target) {
    return null;
  }

  const key = keyOf(accountId, target);
  const entry = store.get(key);
  if (!entry) {
    return null;
  }
  if (nowMs - entry.observedAtMs > DEFAULT_TTL_MS) {
    store.delete(key);
    return null;
  }
  return entry;
}

export function getProactiveRiskObservationForAny(
  accountId: string,
  targetIds: Array<string | null | undefined>,
  nowMs = Date.now(),
): ProactiveRiskSnapshot | null {
  for (const candidate of targetIds) {
    if (!candidate) {
      continue;
    }
    const observation = getProactiveRiskObservation(accountId, candidate, nowMs);
    if (observation) {
      return observation;
    }
  }
  return null;
}

export function deleteProactiveRiskObservation(accountId: string, targetId: string): void {
  const target = targetId?.trim();
  if (!accountId || !target) {
    return;
  }
  store.delete(keyOf(accountId, target));
}

export function clearProactiveRiskObservationsForTest(): void {
  store.clear();
}
