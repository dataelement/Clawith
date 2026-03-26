import * as dns from "node:dns";
import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import type { Logger, RetryOptions } from "./types";

/**
 * Mask sensitive fields in data for safe logging
 * Prevents PII leakage in debug logs
 */
export function maskSensitiveData(data: unknown): any {
  if (data === null || data === undefined) {
    return data;
  }

  if (typeof data !== "object") {
    return data as string | number;
  }

  const masked = JSON.parse(JSON.stringify(data)) as Record<string, unknown>;
  const sensitiveFields = new Set(["token", "accessToken"]);

  function maskObj(obj: Record<string, unknown>): void {
    for (const key in obj) {
      if (sensitiveFields.has(key)) {
        const val = obj[key];
        if (typeof val === "string" && val.length > 6) {
          obj[key] = val.slice(0, 3) + "*".repeat(val.length - 6) + val.slice(-3);
        } else if (typeof val === "string") {
          obj[key] = "*".repeat(val.length);
        }
      } else if (typeof obj[key] === "object" && obj[key] !== null && !Array.isArray(obj[key])) {
        maskObj(obj[key] as Record<string, unknown>);
      }
    }
  }

  maskObj(masked);
  return masked;
}

export function stringifyUnknown(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    typeof value === "bigint"
  ) {
    return String(value);
  }
  try {
    const serialized = JSON.stringify(value);
    return serialized ?? String(value);
  } catch {
    return "[unserializable]";
  }
}

export function getErrorMessage(err: unknown): string {
  if (err instanceof Error && err.message) {
    return err.message;
  }
  if (err && typeof err === "object") {
    const record = err as Record<string, unknown>;
    if (typeof record.message === "string" && record.message.trim()) {
      return record.message;
    }
  }
  return stringifyUnknown(err);
}

export function getErrorResponseData(err: unknown): unknown {
  if (!err || typeof err !== "object") {
    return undefined;
  }
  return (err as { response?: { data?: unknown } }).response?.data;
}

export function formatDingTalkErrorPayload(payload: unknown): string {
  if (payload === null || payload === undefined) {
    return "payload=unknown";
  }

  let code: string | undefined;
  let message: string | undefined;
  if (typeof payload === "object" && !Array.isArray(payload)) {
    const obj = payload as Record<string, unknown>;
    if (typeof obj.code === "string" || typeof obj.code === "number") {
      code = String(obj.code);
    }
    if (typeof obj.message === "string") {
      message = obj.message;
    }
  }

  let serialized: string;
  try {
    serialized = JSON.stringify(maskSensitiveData(payload));
  } catch {
    if (typeof payload === "string") {
      serialized = payload;
    } else if (typeof payload === "number" || typeof payload === "boolean" || typeof payload === "bigint") {
      serialized = `${payload}`;
    } else {
      serialized = "[unserializable-payload]";
    }
  }

  const parts: string[] = [];
  if (code) {
    parts.push(`code=${code}`);
  }
  if (message) {
    parts.push(`message=${message}`);
  }
  parts.push(`payload=${serialized}`);
  return parts.join(" ");
}

export function formatDingTalkErrorPayloadLog(
  scope: string,
  payload: unknown,
  prefix: "[DingTalk]" | "[DingTalk][AICard]" = "[DingTalk]",
): string {
  return `${prefix}[ErrorPayload][${scope}] ${formatDingTalkErrorPayload(payload)}`;
}

export function getProxyBypassOption(config?: { bypassProxyForSend?: boolean }): { proxy: false } | Record<string, never> {
  return config?.bypassProxyForSend ? { proxy: false } : {};
}

type LookupCallback = (
  err: NodeJS.ErrnoException | null,
  address: string | dns.LookupAddress[],
  family?: number,
) => void;

type LookupOptions = dns.LookupOneOptions | dns.LookupAllOptions;

export function createResolve4FallbackLookup(log?: Logger, accountId?: string) {
  return createResolve4FallbackLookupWithDeps(log, accountId, dns, net);
}

export function createResolve4FallbackLookupWithDeps(
  log: Logger | undefined,
  accountId: string | undefined,
  dnsImpl: Pick<typeof dns, "lookup" | "resolve4">,
  netImpl: Pick<typeof net, "isIP">,
) {
  let fallbackLogged = false;

  return (hostname: string, options: LookupOptions, callback: LookupCallback): void => {
    const ipFamily = netImpl.isIP(hostname);
    if (ipFamily !== 0) {
      if (options.all) {
        callback(null, [{ address: hostname, family: ipFamily }], ipFamily);
        return;
      }

      callback(null, hostname, ipFamily);
      return;
    }

    dnsImpl.lookup(hostname, options, (lookupErr, address, family) => {
      if (!lookupErr) {
        callback(null, address, family);
        return;
      }

      if (lookupErr.code !== "ENOTFOUND") {
        callback(lookupErr, address, family);
        return;
      }

      dnsImpl.resolve4(hostname, (resolveErr, addresses) => {
        if (resolveErr || !addresses || addresses.length === 0) {
          callback(lookupErr, address, family);
          return;
        }

        if (!fallbackLogged) {
          fallbackLogged = true;
          log?.warn?.(
            `[${accountId ?? "default"}] System DNS lookup failed for ${hostname} (ENOTFOUND); using resolve4 fallback ${addresses[0]}`,
          );
        }

        if (options.all) {
          callback(
            null,
            addresses.map((item) => ({ address: item, family: 4 })),
            4,
          );
          return;
        }

        callback(null, addresses[0], 4);
      });
    });
  };
}

function getHeaderCaseInsensitive(headers: unknown, key: string): string | undefined {
  if (!headers || typeof headers !== "object" || Array.isArray(headers)) {
    return undefined;
  }

  const entries = Object.entries(headers as Record<string, unknown>);
  const matched = entries.find(([name]) => name.toLowerCase() === key.toLowerCase());
  if (!matched) {
    return undefined;
  }

  const value = matched[1];
  if (Array.isArray(value)) {
    return value.length > 0 ? String(value[0]) : undefined;
  }
  if (value === null || value === undefined) {
    return undefined;
  }
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean" ||
    typeof value === "bigint"
  ) {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return undefined;
  }
}

export function formatDingTalkConnectionErrorLog(
  scope: string,
  err: unknown,
  baseMessage: string,
): string | null {
  if (!err || typeof err !== "object") {
    return null;
  }

  const errRecord = err as Record<string, unknown>;
  const stage =
    typeof errRecord.dingtalkConnectionStage === "string"
      ? errRecord.dingtalkConnectionStage
      : scope;
  const endpoint =
    typeof errRecord.dingtalkConnectionEndpoint === "string"
      ? errRecord.dingtalkConnectionEndpoint
      : undefined;

  const hasResponse = "response" in errRecord && errRecord.response !== null && errRecord.response !== undefined;
  if (!hasResponse && !endpoint && stage === scope) {
    return null;
  }

  const parts: string[] = [`${baseMessage} [DingTalk][ConnectionError][${stage}]`];
  if (endpoint) {
    parts.push(`endpoint=${endpoint}`);
  }

  const response = (err as { response?: { status?: unknown; data?: unknown; headers?: unknown } }).response;
  if (response) {
    if (response.status !== undefined && response.status !== null) {
      const statusText =
        typeof response.status === "string" ||
        typeof response.status === "number" ||
        typeof response.status === "boolean" ||
        typeof response.status === "bigint"
          ? String(response.status)
          : JSON.stringify(response.status);
      parts.push(`status=${statusText}`);
    }

    let requestId = getHeaderCaseInsensitive(response.headers, "x-acs-dingtalk-request-id");
    if (!requestId && response.data && typeof response.data === "object" && !Array.isArray(response.data)) {
      const data = response.data as Record<string, unknown>;
      if (typeof data.requestId === "string") {
        requestId = data.requestId;
      } else if (typeof data.requestid === "string") {
        requestId = data.requestid;
      }
    }
    if (requestId) {
      parts.push(`requestId=${requestId}`);
    }

    if (response.data !== undefined) {
      parts.push(formatDingTalkErrorPayload(response.data));
    }
  }

  if (stage === "connect.websocket") {
    parts.push("Likely websocket/proxy/WSS issue after connections/open succeeded");
  }

  parts.push("See docs/connection-troubleshooting.md or run scripts/dingtalk-connection-check.*");

  return parts.join(" ");
}

/**
 * Cleanup orphaned temp files from dingtalk media
 * Run at startup to clean up files from crashed processes
 */
export function cleanupOrphanedTempFiles(log?: Logger): number {
  const tempDir = os.tmpdir();
  const dingtalkPattern = /^dingtalk_\d+\..+$/;
  let cleaned = 0;

  try {
    const files = fs.readdirSync(tempDir);
    const now = Date.now();
    const maxAge = 24 * 60 * 60 * 1000;

    for (const file of files) {
      if (!dingtalkPattern.test(file)) {
        continue;
      }

      const filePath = path.join(tempDir, file);
      try {
        const stats = fs.statSync(filePath);
        if (now - stats.mtime.getTime() > maxAge) {
          fs.unlinkSync(filePath);
          cleaned++;
          log?.debug?.(`[DingTalk] Cleaned up orphaned temp file: ${file}`);
        }
      } catch (err: unknown) {
        log?.debug?.(`[DingTalk] Failed to cleanup temp file ${file}: ${getErrorMessage(err)}`);
      }
    }

    if (cleaned > 0) {
      log?.info?.(`[DingTalk] Cleaned up ${cleaned} orphaned temp files`);
    }
  } catch (err: unknown) {
    log?.debug?.(`[DingTalk] Failed to cleanup temp directory: ${getErrorMessage(err)}`);
  }

  return cleaned;
}

/**
 * Retry logic for API calls with exponential backoff
 * Handles transient failures like 401 token expiry
 */
export async function retryWithBackoff<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {},
): Promise<T> {
  const { maxRetries = 3, baseDelayMs = 100, log } = options;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err: unknown) {
      const statusCode = (err as { response?: { status?: number } }).response?.status;
      const isRetryable =
        statusCode === 401 || statusCode === 429 || (statusCode && statusCode >= 500);

      const responseData = getErrorResponseData(err);
      if (responseData !== undefined) {
        log?.debug?.(formatDingTalkErrorPayloadLog("retry.beforeDecision", responseData));
      }

      if (!isRetryable || attempt === maxRetries) {
        throw err;
      }

      const delayMs = baseDelayMs * Math.pow(2, attempt - 1);
      log?.debug?.(`[DingTalk] Retry attempt ${attempt}/${maxRetries} after ${delayMs}ms`);
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }

  throw new Error("Retry exhausted without returning");
}

/**
 * Get current timestamp in ISO-compatible epoch milliseconds for status tracking.
 */
export function getCurrentTimestamp(): number {
  return Date.now();
}
