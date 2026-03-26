import { readNamespaceJson, writeNamespaceJsonAtomic } from "./persistence-store";

const SESSION_PEER_OVERRIDE_NAMESPACE = "session-peer-overrides";

export type SessionPeerSourceKind = "direct" | "group";

interface SessionPeerOverrideBucket {
  peers: Record<string, string>;
}

function buildSourceKey(sourceKind: SessionPeerSourceKind, sourceId: string): string {
  return `${sourceKind}:${sourceId}`;
}

function readBucket(storePath: string, accountId: string): SessionPeerOverrideBucket {
  return readNamespaceJson<SessionPeerOverrideBucket>(SESSION_PEER_OVERRIDE_NAMESPACE, {
    storePath,
    scope: { accountId },
    fallback: { peers: {} },
  });
}

function writeBucket(storePath: string, accountId: string, bucket: SessionPeerOverrideBucket): void {
  writeNamespaceJsonAtomic(SESSION_PEER_OVERRIDE_NAMESPACE, {
    storePath,
    scope: { accountId },
    data: bucket,
  });
}

export function getSessionPeerOverride(params: {
  storePath: string;
  accountId: string;
  sourceKind: SessionPeerSourceKind;
  sourceId: string;
}): string | undefined {
  const bucket = readBucket(params.storePath, params.accountId);
  const sourceKey = buildSourceKey(params.sourceKind, params.sourceId);
  const legacyKey = params.sourceKind === "group" ? params.sourceId : undefined;
  const value = bucket.peers[sourceKey] ?? (legacyKey ? bucket.peers[legacyKey] : undefined);
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function setSessionPeerOverride(params: {
  storePath: string;
  accountId: string;
  sourceKind: SessionPeerSourceKind;
  sourceId: string;
  peerId: string;
}): void {
  const bucket = readBucket(params.storePath, params.accountId);
  const sourceKey = buildSourceKey(params.sourceKind, params.sourceId);
  bucket.peers[sourceKey] = params.peerId.trim();
  writeBucket(params.storePath, params.accountId, bucket);
}

export function clearSessionPeerOverride(params: {
  storePath: string;
  accountId: string;
  sourceKind: SessionPeerSourceKind;
  sourceId: string;
}): boolean {
  const bucket = readBucket(params.storePath, params.accountId);
  const sourceKey = buildSourceKey(params.sourceKind, params.sourceId);
  const legacyKey = params.sourceKind === "group" ? params.sourceId : undefined;
  const existed = Object.prototype.hasOwnProperty.call(bucket.peers, sourceKey)
    || (legacyKey ? Object.prototype.hasOwnProperty.call(bucket.peers, legacyKey) : false);
  if (!existed) {
    return false;
  }
  delete bucket.peers[sourceKey];
  if (legacyKey) {
    delete bucket.peers[legacyKey];
  }
  writeBucket(params.storePath, params.accountId, bucket);
  return true;
}
