/**
 * Peer ID Registry
 *
 * Maps lowercased peer/session keys back to their original case-sensitive
 * DingTalk conversationId values. DingTalk conversationIds are base64-encoded
 * and therefore case-sensitive, but the framework may lowercase session keys
 * internally. This registry preserves the original casing so outbound messages
 * can be delivered correctly.
 */

import { readFileSync, readdirSync } from "fs";
import * as os from "os";
import { join } from "path";
import { getLogger } from "./logger-context";

const peerIdMap = new Map<string, string>();
let preloaded = false;

/**
 * Register an original peer ID, keyed by its lowercased form.
 */
export function registerPeerId(originalId: string): void {
  if (!originalId) {
    return;
  }
  peerIdMap.set(originalId.toLowerCase(), originalId);
}

function maybeRegisterDingTalkGroupPeerId(value: unknown): void {
  // DingTalk group openConversationId values are base64-like and consistently start with "cid".
  // User IDs in DM contexts do not follow this pattern and do not require case restoration.
  if (typeof value === "string" && value.startsWith("cid")) {
    registerPeerId(value);
  }
}

/**
 * Resolve a possibly-lowercased peer ID back to its original casing.
 *
 * If registry is empty at first outbound call (for example, cron/delivery queue
 * fires before inbound callbacks), perform a one-time lazy preload from sessions.
 *
 * Returns the original if found, otherwise returns the input as-is.
 */
export function resolveOriginalPeerId(id: string): string {
  if (!id) {
    return id;
  }

  if (!preloaded) {
    preloadPeerIdsFromSessions();
  }

  return peerIdMap.get(id.toLowerCase()) || id;
}

/**
 * Clear the registry (for testing or shutdown).
 */
export function clearPeerIdRegistry(): void {
  peerIdMap.clear();
  preloaded = false;
}

/**
 * Preload known peer IDs from all agents' sessions.json files.
 *
 * Safe to call repeatedly:
 * - without explicit homeDir: runs once, then no-ops;
 * - with explicit homeDir: always runs (useful for tests).
 */
export function preloadPeerIdsFromSessions(homeDir?: string): void {
  if (!homeDir && preloaded) {
    return;
  }

  const home = homeDir || os.homedir();
  const agentsDir = join(home, ".openclaw", "agents");
  const log = getLogger();
  let preloadCompleted = false;

  try {
    const agentDirs = readdirSync(agentsDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);

    for (const agentName of agentDirs) {
      const sessionsPath = join(agentsDir, agentName, "sessions", "sessions.json");

      try {
        const raw = readFileSync(sessionsPath, "utf-8");
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object") {
          continue;
        }

        for (const session of Object.values(parsed as Record<string, unknown>)) {
          if (!session || typeof session !== "object") {
            continue;
          }

          const sessionRecord = session as Record<string, unknown>;
          maybeRegisterDingTalkGroupPeerId(sessionRecord.lastTo);

          const origin = sessionRecord.origin;
          if (!origin || typeof origin !== "object") {
            continue;
          }

          const originRecord = origin as Record<string, unknown>;
          maybeRegisterDingTalkGroupPeerId(originRecord.from);
          maybeRegisterDingTalkGroupPeerId(originRecord.to);
        }
      } catch (err) {
        // sessions.json may be missing or malformed for some agents; ignore per file.
        log?.debug?.(
          `[DingTalk][PeerIdRegistry] Failed to parse preload sessions file ${sessionsPath}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
    }
    preloadCompleted = true;
  } catch (err) {
    const errorCode = (err as NodeJS.ErrnoException | undefined)?.code;
    if (errorCode === "ENOENT") {
      // agents directory may not exist yet; this is a stable no-op state.
      preloadCompleted = true;
    } else {
      log?.debug?.(
        `[DingTalk][PeerIdRegistry] Failed to scan preload directory ${agentsDir}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  }

  if (!homeDir && preloadCompleted) {
    preloaded = true;
  }
}
