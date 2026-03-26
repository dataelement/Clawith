import http from "node:http";
import https from "node:https";
import axios from "axios";
import { getAccessToken } from "./auth";
import { getDingTalkRuntime } from "./runtime";
import type { DingTalkConfig, Logger, MediaFile } from "./types";
import { formatDingTalkErrorPayload, formatDingTalkErrorPayloadLog } from "./utils";

function asRecord(value: unknown): Record<string, unknown> | undefined {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
        return undefined;
    }
    return value as Record<string, unknown>;
}

const ipv4OnlyHttpAgent = new http.Agent({ family: 4 });
const ipv4OnlyHttpsAgent = new https.Agent({ family: 4 });

const DINGTALK_API = "https://api.dingtalk.com";
const DINGTALK_OAPI = "https://oapi.dingtalk.com";

const MATCH_WINDOW_MS = 10_000;
const MAX_PAGES = 3;
const PAGE_SIZE = 50;

const UNION_ID_CACHE_MAX = 5000;
const SPACE_ID_CACHE_MAX = 500;

function describeResolveError(err: unknown): string {
    if (axios.isAxiosError(err)) {
        const status = err.response?.status;
        const statusText = err.response?.statusText;
        const statusLabel = status ? `status=${status}${statusText ? ` ${statusText}` : ""}` : "status=unknown";
        const code = typeof err.code === "string" && err.code ? ` code=${err.code}` : "";
        const hasRequest = err.request ? " request=yes" : " request=no";
        const hasResponse = err.response ? " response=yes" : " response=no";
        if (err.response?.data !== undefined) {
            return `${statusLabel}${code}${hasRequest}${hasResponse} ${formatDingTalkErrorPayload(err.response.data)}`;
        }
        return `${statusLabel}${code}${hasRequest}${hasResponse} message=${err.message || "unknown axios error"}`;
    }
    if (err instanceof Error) {
        return `${err.name || "Error"} message=${err.message || "unknown error"}`;
    }
    try {
        return JSON.stringify(err);
    } catch {
        return String(err);
    }
}

// ============ LRU caches ============

const unionIdCache = new Map<string, string>();
const spaceIdCache = new Map<string, string>();

function lruSet<V>(map: Map<string, V>, key: string, value: V, maxSize: number): void {
    if (map.has(key)) {
        map.delete(key);
    }
    map.set(key, value);
    if (map.size > maxSize) {
        const oldest = map.keys().next().value;
        if (oldest !== undefined) {
            map.delete(oldest);
        }
    }
}

function lruGet<V>(map: Map<string, V>, key: string): V | undefined {
    const value = map.get(key);
    if (value === undefined) {
        return undefined;
    }
    map.delete(key);
    map.set(key, value);
    return value;
}

// ============ Time parsing ============

export function parseDingTalkFileTime(timeStr: string): number {
    const normalized = timeStr.replace(/\bCST\b/, "+0800");
    const ms = new Date(normalized).getTime();
    if (Number.isNaN(ms)) {
        throw new Error(`Cannot parse DingTalk file time: ${timeStr}`);
    }
    return ms;
}

// ============ API helpers ============

export async function getUnionIdByStaffId(
    config: DingTalkConfig,
    staffId: string,
    log?: Logger,
): Promise<string> {
    const cacheKey = `${config.clientId}:${staffId}`;
    const cached = lruGet(unionIdCache, cacheKey);
    if (cached) {
        return cached;
    }

    const token = await getAccessToken(config, log);
    const resp = await axios.post(
        `${DINGTALK_OAPI}/topapi/v2/user/get?access_token=${token}`,
        { userid: staffId },
    );

    const payload = asRecord(resp.data) ?? {};
    const errcode = typeof payload.errcode === "number" || typeof payload.errcode === "string"
        ? String(payload.errcode)
        : "unknown";
    const errmsg = typeof payload.errmsg === "string" ? payload.errmsg : "unknown";
    if (payload.errcode !== 0) {
        throw new Error(`topapi/v2/user/get failed: errcode=${errcode} errmsg=${errmsg}`);
    }

    const unionIdValue = asRecord(payload.result)?.unionid;
    const unionId = typeof unionIdValue === "string" ? unionIdValue : undefined;
    if (!unionId) {
        throw new Error(`topapi/v2/user/get returned no unionid for staffId=${staffId}`);
    }

    lruSet(unionIdCache, cacheKey, unionId, UNION_ID_CACHE_MAX);
    return unionId;
}

export async function getGroupFileSpaceId(
    config: DingTalkConfig,
    openConversationId: string,
    unionId: string,
    log?: Logger,
): Promise<string> {
    const cacheKey = `${config.clientId}:${openConversationId}`;
    const cached = lruGet(spaceIdCache, cacheKey);
    if (cached) {
        return cached;
    }

    const token = await getAccessToken(config, log);
    const resp = await axios.post(
        `${DINGTALK_API}/v1.0/convFile/conversations/spaces/query`,
        { openConversationId, unionId },
        { headers: { "x-acs-dingtalk-access-token": token } },
    );

    const spaceIdValue = asRecord(asRecord(resp.data)?.space)?.spaceId;
    const spaceId = typeof spaceIdValue === "string" ? spaceIdValue : undefined;
    if (!spaceId) {
        throw new Error(`convFile spaces/query returned no spaceId for conversationId=${openConversationId}`);
    }

    lruSet(spaceIdCache, cacheKey, spaceId, SPACE_ID_CACHE_MAX);
    return spaceId;
}

interface DentryMatch {
    dentryId: string;
    name: string;
}

export interface ResolvedQuotedFile {
    media: MediaFile;
    spaceId: string;
    fileId: string;
    name?: string;
}

export async function findFileByTimestamp(
    config: DingTalkConfig,
    spaceId: string,
    unionId: string,
    createdAt: number,
    log?: Logger,
): Promise<DentryMatch | null> {
    const token = await getAccessToken(config, log);

    let bestMatch: DentryMatch | null = null;
    let bestDelta = Infinity;
    let nextToken: string | undefined;

    for (let page = 0; page < MAX_PAGES; page++) {
        const body: Record<string, unknown> = {
            option: { maxResults: PAGE_SIZE },
        };
        if (nextToken) {
            (body.option as { nextToken?: string }).nextToken = nextToken;
        }

        const resp = await axios.post(
            `${DINGTALK_API}/v1.0/storage/spaces/${spaceId}/dentries/listAll?unionId=${unionId}`,
            body,
            { headers: { "x-acs-dingtalk-access-token": token } },
        );

        const data = asRecord(resp.data) ?? {};
        const dentries = Array.isArray(data.dentries) ? data.dentries as Array<Record<string, unknown>> : [];

        for (const entry of dentries) {
            if (entry.type !== "FILE" || !entry.createTime) {
                continue;
            }
            try {
                const fileTime = parseDingTalkFileTime(entry.createTime as string);
                const delta = Math.abs(fileTime - createdAt);
                if (delta <= MATCH_WINDOW_MS && delta < bestDelta) {
                    bestDelta = delta;
                    bestMatch = { dentryId: entry.id as string, name: entry.name as string };
                }
            } catch {
                const createTime = typeof entry.createTime === "string" ? entry.createTime : JSON.stringify(entry.createTime);
                log?.debug?.(`[DingTalk][QuotedFile] Failed to parse createTime: ${createTime}`);
            }
        }

        nextToken = typeof data.nextToken === "string" ? data.nextToken : undefined;
        if (!nextToken) {
            break;
        }
    }

    return bestMatch;
}

export async function downloadGroupFile(
    config: DingTalkConfig,
    spaceId: string,
    dentryId: string,
    unionId: string,
    log?: Logger,
): Promise<MediaFile | null> {
    const rt = getDingTalkRuntime();
    const token = await getAccessToken(config, log);
    let resourceUrl = "";
    let contentType = "application/octet-stream";

    try {
        const infoResp = await axios.post(
            `${DINGTALK_API}/v1.0/storage/spaces/${spaceId}/dentries/${dentryId}/downloadInfos/query?unionId=${unionId}`,
            {},
            { headers: { "x-acs-dingtalk-access-token": token } },
        );

        const info = asRecord(infoResp.data) ?? {};
        const headerSig = asRecord(info.headerSignatureInfo);
        const resourceUrls = headerSig && Array.isArray(headerSig.resourceUrls) ? headerSig.resourceUrls : undefined;
        resourceUrl = typeof resourceUrls?.[0] === "string" ? resourceUrls[0] : "";
        if (!resourceUrl) {
            log?.warn?.("[DingTalk][QuotedFile] downloadInfos/query returned no resourceUrl");
            return null;
        }

        const sigHeaders: Record<string, string> = {};
        const headerMap = asRecord(headerSig?.headers);
        if (headerMap) {
            for (const [k, v] of Object.entries(headerMap)) {
                if (typeof v === "string") {
                    sigHeaders[k] = v;
                }
            }
        }
        let fileResp;
        try {
            fileResp = await axios.get(resourceUrl, {
                headers: sigHeaders,
                responseType: "arraybuffer",
                timeout: 15000,
            });
        } catch (firstErr: unknown) {
            log?.warn?.(
                `[DingTalk][QuotedFile] CDN download failed on default network path, retrying with IPv4-only: ${describeResolveError(firstErr)}`,
            );
            try {
                fileResp = await axios.get(resourceUrl, {
                    headers: sigHeaders,
                    responseType: "arraybuffer",
                    httpAgent: ipv4OnlyHttpAgent,
                    httpsAgent: ipv4OnlyHttpsAgent,
                    timeout: 15000,
                });
            } catch (retryErr: unknown) {
                const host = resourceUrl ? new URL(resourceUrl).host : "(unknown-host)";
                throw new Error(
                    `download-resource failed host=${host} detail=${describeResolveError(retryErr)}`,
                    { cause: retryErr },
                );
            }
        }

        contentType = (fileResp.headers["content-type"] as string) || "application/octet-stream";
        const buffer = Buffer.from(fileResp.data as ArrayBuffer);

        const maxBytes =
            config.mediaMaxMb && config.mediaMaxMb > 0 ? config.mediaMaxMb * 1024 * 1024 : undefined;
        try {
            const saved = maxBytes
                ? await rt.channel.media.saveMediaBuffer(buffer, contentType, "inbound", maxBytes)
                : await rt.channel.media.saveMediaBuffer(buffer, contentType, "inbound");

            return { path: saved.path, mimeType: saved.contentType ?? contentType };
        } catch (err: unknown) {
            throw new Error(
                `save-buffer failed contentType=${contentType} detail=${describeResolveError(err)}`,
                { cause: err },
            );
        }
    } catch (err: unknown) {
        if (axios.isAxiosError(err) && err.response?.data !== undefined) {
            log?.warn?.(formatDingTalkErrorPayloadLog("quotedFile.downloadGroupFile", err.response.data));
        }
        log?.warn?.(
            `[DingTalk][QuotedFile] downloadGroupFile failed: spaceId=${spaceId} dentryId=${dentryId} resourceUrl=${resourceUrl || "(none)"} contentType=${contentType} error=${describeResolveError(err)}`,
        );
        return null;
    }
}

// ============ Composite entry point ============

export interface ResolveQuotedFileParams {
    openConversationId: string;
    senderStaffId?: string;
    fileCreatedAt?: number;
}

export async function resolveQuotedFile(
    config: DingTalkConfig,
    params: ResolveQuotedFileParams,
    log?: Logger,
): Promise<ResolvedQuotedFile | null> {
    const { openConversationId, senderStaffId, fileCreatedAt } = params;
    let stage = "init";

    if (!senderStaffId || !fileCreatedAt) {
        log?.warn?.("[DingTalk][QuotedFile] Missing senderStaffId or fileCreatedAt, skipping");
        return null;
    }

    try {
        stage = "resolve-unionId";
        const unionId = await getUnionIdByStaffId(config, senderStaffId, log);
        stage = "resolve-spaceId";
        const spaceId = await getGroupFileSpaceId(config, openConversationId, unionId, log);
        stage = "list-and-match";
        const match = await findFileByTimestamp(config, spaceId, unionId, fileCreatedAt, log);

        if (!match) {
            log?.warn?.(
                `[DingTalk][QuotedFile] No file matched within ±${MATCH_WINDOW_MS}ms window for createdAt=${fileCreatedAt}`,
            );
            return null;
        }

        stage = "download-file";
        const media = await downloadGroupFile(config, spaceId, match.dentryId, unionId, log);
        if (!media) {
            return null;
        }

        return {
            media,
            spaceId,
            fileId: match.dentryId,
            name: match.name,
        };
    } catch (err: unknown) {
        if (log?.warn) {
            if (axios.isAxiosError(err) && err.response?.data !== undefined) {
                log.warn(formatDingTalkErrorPayloadLog("quotedFile.resolve", err.response.data));
            }
            log.warn(
                `[DingTalk][QuotedFile] Failed to resolve quoted file: stage=${stage} conversationId=${openConversationId} senderStaffId=${senderStaffId} fileCreatedAt=${fileCreatedAt} error=${describeResolveError(err)}`,
            );
        }
        return null;
    }
}

// ============ Test helpers ============

export function clearQuotedFileServiceCachesForTest(): void {
    unionIdCache.clear();
    spaceIdCache.clear();
}
