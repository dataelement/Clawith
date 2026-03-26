// src/media-utils.ts

/**
 * Media handling utilities for DingTalk channel plugin.
 * Provides functions for media type detection and file upload to DingTalk media servers.
 */

import * as fs from "node:fs";
import { randomUUID } from "node:crypto";
import * as os from "node:os";
import * as path from "node:path";
import { promises as fsPromises } from "node:fs";
import { lookup as dnsLookup } from "node:dns/promises";
import { BlockList, isIP } from "node:net";
import axios from "axios";
import FormData from "form-data";
import type { DingTalkConfig, Logger } from "./types";
import { formatDingTalkErrorPayloadLog, getProxyBypassOption } from "./utils";

/**
 * Calculate MP3 duration in seconds by parsing MPEG frame headers
 * Supports CBR and VBR MP3 files
 * @param filePath Path to the MP3 file
 * @param log Optional logger
 * @returns Duration in seconds (0 if parsing fails)
 */
export async function getMp3DurationSeconds(filePath: string, log?: Logger): Promise<number> {
  try {
    const buffer = await fsPromises.readFile(filePath);
    let offset = 0;

    // Skip ID3v2 tag if present
    if (buffer.length >= 10 && buffer[0] === 0x49 && buffer[1] === 0x44 && buffer[2] === 0x33) {
      const flags = buffer[5];
      const id3Size =
        ((buffer[6] & 0x7f) << 21) |
        ((buffer[7] & 0x7f) << 14) |
        ((buffer[8] & 0x7f) << 7) |
        (buffer[9] & 0x7f);

      // ID3 size excludes the 10-byte header; footer (if present) adds 10 bytes.
      const footerSize = (flags & 0x10) ? 10 : 0;
      offset = 10 + id3Size + footerSize;
    }

    // Skip ID3v1 tag at the end (last 128 bytes)
    const endOffset =
      buffer.length > 128 &&
      buffer[buffer.length - 128] === 0x54 &&
      buffer[buffer.length - 127] === 0x41 &&
      buffer[buffer.length - 126] === 0x47
        ? buffer.length - 128
        : buffer.length;

    let frameCount = 0;
    let totalSamples = 0;
    let lastSampleRate = 0;

    // Sample rate tables
    const sampleRates: Record<"1" | "2" | "2.5", number[]> = {
      "1": [44100, 48000, 32000, 0],
      "2": [22050, 24000, 16000, 0],
      "2.5": [11025, 12000, 8000, 0],
    };

    // Bitrate tables (kbps) by (version group -> layer)
    // Note: version group here is MPEG1 vs MPEG2/2.5 for bitrate tables.
    const bitratesLayer1: Record<"1" | "2", number[]> = {
      "1": [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0],
      "2": [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0],
    };
    const bitratesLayer2: Record<"1" | "2", number[]> = {
      "1": [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0],
      "2": [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    };
    const bitratesLayer3: Record<"1" | "2", number[]> = {
      "1": [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
      "2": [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    };

    while (offset < endOffset - 4) {
      // Frame sync (11 bits set)
      if (buffer[offset] === 0xff && (buffer[offset + 1] & 0xe0) === 0xe0) {
        const versionBits = (buffer[offset + 1] >> 3) & 0x03; // 00=2.5, 01=reserved, 10=2, 11=1
        const layerBits = (buffer[offset + 1] >> 1) & 0x03;   // 01=III, 10=II, 11=I, 00=reserved
        const bitrateIndex = (buffer[offset + 2] >> 4) & 0x0f; // 0000/1111 invalid
        const sampleRateIndex = (buffer[offset + 2] >> 2) & 0x03; // 11 invalid
        const paddingBit = (buffer[offset + 2] >> 1) & 0x01;

        // quick validity checks to reduce false sync hits
        if (layerBits === 0 || bitrateIndex === 0 || bitrateIndex === 15 || sampleRateIndex === 3) {
          offset++;
          continue;
        }

        // MPEG version
        let mpegVersion: "1" | "2" | "2.5";
        if (versionBits === 0) {
          mpegVersion = "2.5";
        } else if (versionBits === 2) {
          mpegVersion = "2";
        } else if (versionBits === 3) {
          mpegVersion = "1";
        } else {
          offset++;
          continue; // reserved
        }

        // Layer
        let layer: 1 | 2 | 3;
        if (layerBits === 1) {
          layer = 3; // Layer III
        } else if (layerBits === 2) {
          layer = 2; // Layer II
        } else if (layerBits === 3) {
          layer = 1; // Layer I
        } else {
          offset++;
          continue;
        }

        const sampleRate = sampleRates[mpegVersion][sampleRateIndex] || 0;
        if (!sampleRate) {
          offset++;
          continue;
        }

        // bitrate tables use group: MPEG1 vs MPEG2/2.5
        const brGroup: "1" | "2" = mpegVersion === "1" ? "1" : "2";
        let bitrateKbps = 0;
        if (layer === 1) {
          bitrateKbps = bitratesLayer1[brGroup][bitrateIndex] || 0;
        } else if (layer === 2) {
          bitrateKbps = bitratesLayer2[brGroup][bitrateIndex] || 0;
        } else {
          bitrateKbps = bitratesLayer3[brGroup][bitrateIndex] || 0;
        }

        if (!bitrateKbps) {
          offset++;
          continue;
        }

        // samples per frame
        let samplesPerFrame: number;
        if (layer === 1) {
          samplesPerFrame = 384;
        } else if (layer === 2) {
          samplesPerFrame = 1152;
        } else {
          samplesPerFrame = mpegVersion === "1" ? 1152 : 576; // Layer III
        }

        // frame size
        let frameSize = 0;
        if (layer === 1) {
          frameSize = Math.floor(((12 * bitrateKbps * 1000) / sampleRate + paddingBit) * 4);
        } else if (layer === 3 && mpegVersion !== "1") {
          // Layer III + MPEG2/2.5 uses 72, not 144
          frameSize = Math.floor((72 * bitrateKbps * 1000) / sampleRate + paddingBit);
        } else {
          // Layer II OR Layer III MPEG1
          frameSize = Math.floor((144 * bitrateKbps * 1000) / sampleRate + paddingBit);
        }

        if (frameSize > 0 && frameSize < 10000 && offset + frameSize <= endOffset) {
          frameCount++;
          totalSamples += samplesPerFrame;
          lastSampleRate = sampleRate;
          offset += frameSize;
          continue;
        }
      }

      offset++;
    }

    if (frameCount > 0 && lastSampleRate > 0) {
      const duration = totalSamples / lastSampleRate;
      log?.debug?.(`[DingTalk] Parsed ${frameCount} MP3 frames, duration: ${duration.toFixed(3)}s`);
      return Math.floor(duration);
    }

    log?.warn?.(`[DingTalk] Could not parse MP3 duration from ${filePath} (found ${frameCount} frames)`);
    return 0;
  } catch (err: unknown) {
    log?.error?.(`[DingTalk] Failed to get MP3 duration: ${err instanceof Error ? err.message : String(err)}`);
    return 0;
  }
}

const DEFAULT_VOICE_DURATION_MS = 1000;

export async function getVoiceDurationMs(
  filePath: string,
  mediaType: DingTalkMediaType,
  log?: Logger,
): Promise<number> {
  if (mediaType !== "voice") {
    return DEFAULT_VOICE_DURATION_MS;
  }

  const ext = path.extname(filePath).toLowerCase();

  if (ext === ".mp3") {
    const durationSec = await getMp3DurationSeconds(filePath, log);
    if (durationSec > 0) {
      return Math.max(1, Math.round(durationSec * 1000));
    }

    log?.warn?.(
      `[DingTalk] MP3 duration parse returned ${durationSec} for ${filePath}; using fallback ${DEFAULT_VOICE_DURATION_MS}ms`,
    );
    return DEFAULT_VOICE_DURATION_MS;
  }

  return DEFAULT_VOICE_DURATION_MS;
}


export type DingTalkMediaType = "image" | "voice" | "video" | "file";
export type DingTalkOutboundMediaType = DingTalkMediaType;

export interface PreparedMediaInput {
  path: string;
  cleanup?: () => Promise<void>;
}

export const REMOTE_MEDIA_ERROR_CODES = {
  ALLOWLIST_MISS: "ERR_MEDIA_ALLOWLIST_MISS",
  PRIVATE_HOST: "ERR_MEDIA_PRIVATE_HOST",
  DNS_UNRESOLVED: "ERR_MEDIA_DNS_UNRESOLVED",
  DNS_PRIVATE: "ERR_MEDIA_DNS_PRIVATE",
  REDIRECT_HOST: "ERR_MEDIA_REDIRECT_HOST",
} as const;

export class RemoteMediaError extends Error {
  constructor(
    message: string,
    public readonly code: (typeof REMOTE_MEDIA_ERROR_CODES)[keyof typeof REMOTE_MEDIA_ERROR_CODES],
  ) {
    super(message);
    this.name = "RemoteMediaError";
  }
}

const REMOTE_MEDIA_DOWNLOAD_TIMEOUT_MS = 10_000;
const REMOTE_MEDIA_MAX_BYTES = 20 * 1024 * 1024;
const REMOTE_MEDIA_MAX_REDIRECTS = 5;

function normalizeAllowlistEntry(entry: string): string {
  return entry.trim().toLowerCase();
}

function normalizeHostname(hostname: string): string {
  return hostname.replace(/^\[/, "").replace(/\]$/, "").trim().toLowerCase();
}

function isIpInCidr(ip: string, cidr: string): boolean {
  const [network, rawMask] = cidr.split("/");
  const mask = Number.parseInt(rawMask || "", 10);
  const normalizedIp = normalizeHostname(ip);
  const normalizedNetwork = normalizeHostname(network || "");
  if (!normalizedNetwork || Number.isNaN(mask)) {
    return false;
  }

  const ipVersion = isIP(normalizedIp);
  const networkVersion = isIP(normalizedNetwork);
  if (ipVersion === 0 || ipVersion !== networkVersion) {
    return false;
  }

  const blockList = new BlockList();
  if (ipVersion === 4) {
    blockList.addSubnet(normalizedNetwork, mask, "ipv4");
    return blockList.check(normalizedIp, "ipv4");
  }

  blockList.addSubnet(normalizedNetwork, mask, "ipv6");
  return blockList.check(normalizedIp, "ipv6");
}

function matchesAllowlistHost(hostname: string, port: string, entry: string): boolean {
  const normalizedHost = normalizeHostname(hostname);
  const normalizedEntry = normalizeAllowlistEntry(entry);
  if (!normalizedEntry) {
    return false;
  }

  if (normalizedEntry.includes("/")) {
    return isIpInCidr(normalizedHost, normalizedEntry);
  }

  if (normalizedEntry.startsWith("*.")) {
    const suffix = normalizedEntry.slice(1);
    return normalizedHost.endsWith(suffix);
  }

  if (normalizedEntry.includes(":")) {
    return `${normalizedHost}:${port}` === normalizedEntry;
  }

  return normalizedHost === normalizedEntry;
}

function isAllowedByMediaUrlAllowlist(url: URL, mediaUrlAllowlist: string[]): boolean {
  const port = url.port || (url.protocol === "https:" ? "443" : "80");
  return mediaUrlAllowlist.some((entry) => matchesAllowlistHost(url.hostname, port, entry));
}

/**
 * Detect media type from file extension
 * Matches DingTalk's supported media types:
 * - image: jpg, gif, png, bmp (max 20MB)
 * - voice: amr, mp3, wav (max 2MB)
 * - video: mp4 (max 20MB)
 * - file: doc, docx, xls, xlsx, ppt, pptx, zip, pdf, rar (max 20MB)
 *
 * @param filePath Path to the media file
 * @returns Detected media type
 */
export function detectMediaTypeFromExtension(filePath: string): DingTalkMediaType {
  const ext = path.extname(filePath).toLowerCase();

  if ([".jpg", ".jpeg", ".png", ".gif", ".bmp"].includes(ext)) {
    return "image";
  } else if ([".mp3", ".amr", ".wav"].includes(ext)) {
    return "voice";
  } else if ([".mp4", ".avi", ".mov"].includes(ext)) {
    return "video";
  }

  return "file";
}

function normalizeOutboundMediaType(value?: string | null): DingTalkOutboundMediaType | undefined {
  if (!value) {
    return undefined;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized === "image" || normalized === "voice" || normalized === "video" || normalized === "file") {
    return normalized;
  }

  return undefined;
}

export function resolveOutboundMediaType(params: {
  mediaType?: string | null;
  mediaPath: string;
  asVoice: boolean;
}): DingTalkOutboundMediaType {
  const explicitType = normalizeOutboundMediaType(params.mediaType);
  const detectedType = detectMediaTypeFromExtension(params.mediaPath);

  if (params.asVoice) {
    if (explicitType && explicitType !== "voice") {
      throw new Error('asVoice requires mediaType="voice" when mediaType is provided.');
    }

    if (detectedType !== "voice") {
      throw new Error("asVoice requires an audio file (mp3, amr, wav).");
    }

    return "voice";
  }

  if (explicitType) {
    return explicitType;
  }

  return detectedType;
}

function isRemoteMediaUrl(input: string): boolean {
  try {
    const parsed = new URL(input);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function isPrivateOrLocalHost(hostname: string): boolean {
  const normalized = normalizeHostname(hostname);
  if (!normalized) {
    return true;
  }

  if (normalized === "localhost" || normalized.endsWith(".localhost")) {
    return true;
  }

  const ipVersion = isIP(normalized);
  if (ipVersion === 4) {
    const parts = normalized.split(".").map((part) => Number.parseInt(part, 10));
    if (parts.length !== 4 || parts.some((part) => Number.isNaN(part))) {
      return true;
    }

    const [a, b] = parts;
    return (
      a === 0 ||
      a === 10 ||
      a === 127 ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168)
    );
  }

  if (ipVersion === 6) {
    if (normalized === "::1") {
      return true;
    }
    return normalized.startsWith("fc") || normalized.startsWith("fd") || normalized.startsWith("fe80");
  }

  return false;
}

async function resolveHostname(hostname: string): Promise<Array<{ address: string; family: number }>> {
  const records = await dnsLookup(hostname, { all: true, verbatim: true });
  return Array.isArray(records) ? records : [records];
}

function detectExtensionFromContentType(contentType?: string): string {
  const normalized = contentType?.split(";")[0]?.trim().toLowerCase();
  switch (normalized) {
    case "image/jpeg":
      return ".jpg";
    case "image/png":
      return ".png";
    case "image/gif":
      return ".gif";
    case "image/bmp":
      return ".bmp";
    case "audio/amr":
      return ".amr";
    case "audio/mpeg":
      return ".mp3";
    case "audio/wav":
    case "audio/x-wav":
      return ".wav";
    case "video/mp4":
      return ".mp4";
    case "application/pdf":
      return ".pdf";
    default:
      return "";
  }
}

export async function prepareMediaInput(
  input: string,
  log?: Logger,
  mediaUrlAllowlist?: string[],
): Promise<PreparedMediaInput> {
  const trimmed = input.trim();
  if (!isRemoteMediaUrl(trimmed)) {
    return { path: trimmed };
  }

  const allowlist = mediaUrlAllowlist?.filter((entry) => entry.trim().length > 0) || [];
  const allowlistConfigured = allowlist.length > 0;

  const assertRemoteUrlAllowed = async (
    url: URL,
  ): Promise<((hostname: string) => Promise<{ address: string; family: number }>) | undefined> => {
    const inAllowlist = allowlistConfigured ? isAllowedByMediaUrlAllowlist(url, allowlist) : false;

    if (allowlistConfigured && !inAllowlist) {
      throw new RemoteMediaError(
        `remote media URL host is not in mediaUrlAllowlist: ${url.hostname}`,
        REMOTE_MEDIA_ERROR_CODES.ALLOWLIST_MISS,
      );
    }

    if (isPrivateOrLocalHost(url.hostname) && !inAllowlist) {
      throw new RemoteMediaError(
        `remote media URL points to private or local network host: ${url.hostname}`,
        REMOTE_MEDIA_ERROR_CODES.PRIVATE_HOST,
      );
    }

    if (isIP(url.hostname) !== 0) {
      return undefined;
    }

    const resolvedRecords = await resolveHostname(url.hostname);
    if (resolvedRecords.length === 0) {
      throw new RemoteMediaError(
        `remote media URL host cannot be resolved: ${url.hostname}`,
        REMOTE_MEDIA_ERROR_CODES.DNS_UNRESOLVED,
      );
    }

    if (!inAllowlist && resolvedRecords.some((record) => isPrivateOrLocalHost(record.address))) {
      throw new RemoteMediaError(
        `remote media URL host resolves to private or local network address: ${url.hostname}`,
        REMOTE_MEDIA_ERROR_CODES.DNS_PRIVATE,
      );
    }

    const pinnedResolved = resolvedRecords[0];
    return async (hostname: string): Promise<{ address: string; family: number }> => {
      if (hostname === url.hostname) {
        return pinnedResolved;
      }

      throw new RemoteMediaError(
        `remote media URL redirected to unexpected host: ${hostname}`,
        REMOTE_MEDIA_ERROR_CODES.REDIRECT_HOST,
      );
    };
  };

  let currentUrl = new URL(trimmed);
  let response: { data: unknown; headers?: Record<string, unknown>; status: number } | null = null;

  for (let redirectCount = 0; redirectCount <= REMOTE_MEDIA_MAX_REDIRECTS; redirectCount += 1) {
    const lookup = await assertRemoteUrlAllowed(currentUrl);
    const currentResponse = await axios.get(currentUrl.toString(), {
      responseType: "arraybuffer",
      maxBodyLength: REMOTE_MEDIA_MAX_BYTES,
      maxContentLength: REMOTE_MEDIA_MAX_BYTES,
      timeout: REMOTE_MEDIA_DOWNLOAD_TIMEOUT_MS,
      maxRedirects: 0,
      validateStatus: (status) => status >= 200 && status < 400,
      lookup,
    });

    response = currentResponse;
    const statusCode = typeof currentResponse.status === "number" ? currentResponse.status : 200;
    if (statusCode < 300 || statusCode >= 400) {
      break;
    }

    const locationHeader =
      typeof currentResponse.headers?.location === "string"
        ? currentResponse.headers.location
        : Array.isArray(currentResponse.headers?.location)
          ? currentResponse.headers.location[0]
          : undefined;
    if (!locationHeader) {
      throw new Error(`redirect response missing location header: ${statusCode}`);
    }

    if (redirectCount === REMOTE_MEDIA_MAX_REDIRECTS) {
      throw new Error(`too many redirects when downloading remote media: ${currentUrl.toString()}`);
    }

    const redirectUrl = new URL(locationHeader, currentUrl);
    if (redirectUrl.protocol !== "http:" && redirectUrl.protocol !== "https:") {
      throw new RemoteMediaError(
        `remote media URL redirected to unsupported scheme: ${redirectUrl.protocol}`,
        REMOTE_MEDIA_ERROR_CODES.REDIRECT_HOST,
      );
    }
    currentUrl = redirectUrl;
  }

  if (!response) {
    throw new Error("remote media download failed: empty response");
  }

  const contentType =
    typeof response.headers?.["content-type"] === "string"
      ? response.headers["content-type"]
      : undefined;
  const urlPath = currentUrl.pathname;
  const ext = path.extname(urlPath) || detectExtensionFromContentType(contentType) || ".bin";
  const tempPath = path.join(os.tmpdir(), `dingtalk_${randomUUID()}${ext}`);
  const buffer = Buffer.isBuffer(response.data)
    ? response.data
    : Buffer.from(response.data as ArrayBuffer);

  await fsPromises.writeFile(tempPath, buffer);
  log?.debug?.(`[DingTalk] Downloaded remote media to temp file: ${tempPath}`);

  return {
    path: tempPath,
    cleanup: async () => {
      try {
        await fsPromises.unlink(tempPath);
      } catch (err: unknown) {
        const code = typeof err === "object" && err !== null && "code" in err ? err.code : undefined;
        if (code !== "ENOENT") {
          const message = err instanceof Error ? err.message : String(err);
          log?.warn?.(`[DingTalk] Failed to remove temp media ${tempPath}: ${message}`);
        }
      }
    },
  };
}

/**
 * File size limits for DingTalk media types (in bytes)
 */
const FILE_SIZE_LIMITS: Record<DingTalkMediaType, number> = {
  image: 20 * 1024 * 1024, // 20MB
  voice: 2 * 1024 * 1024, // 2MB
  video: 20 * 1024 * 1024, // 20MB
  file: 20 * 1024 * 1024, // 20MB
};

/**
 * Upload media file to DingTalk and get media_id
 * Uses DingTalk's media upload API: https://oapi.dingtalk.com/media/upload
 *
 * Note: Media files are stored temporarily by DingTalk (not in permanent storage).
 * The media_id can be used in subsequent message sends.
 *
 * @param config DingTalk configuration
 * @param mediaPath Local path to the media file
 * @param mediaType Type of media: 'image' | 'voice' | 'video' | 'file'
 * @param getAccessToken Function to get DingTalk access token
 * @param log Optional logger
 * @returns media_id on success, null on failure
 */
export async function uploadMedia(
  config: DingTalkConfig,
  mediaPath: string,
  mediaType: DingTalkMediaType,
  getAccessToken: (config: DingTalkConfig, log?: Logger) => Promise<string>,
  log?: Logger,
): Promise<string | null> {
  let fileStream: fs.ReadStream | null = null;

  try {
    const token = await getAccessToken(config, log);

    // Check file size (stat will throw if file doesn't exist)
    const stats = await fsPromises.stat(mediaPath);
    const sizeLimit = FILE_SIZE_LIMITS[mediaType];
    if (stats.size > sizeLimit) {
      const sizeMB = (stats.size / (1024 * 1024)).toFixed(2);
      const limitMB = (sizeLimit / (1024 * 1024)).toFixed(2);
      log?.error?.(
        `[DingTalk] Media file too large: ${sizeMB}MB exceeds ${limitMB}MB limit for ${mediaType}`,
      );
      return null;
    }

    // Read file as a stream for better memory efficiency
    fileStream = fs.createReadStream(mediaPath);
    const filename = path.basename(mediaPath);

    // Upload to DingTalk's media server using form-data
    const form = new FormData();
    form.append("media", fileStream, { filename });

    const uploadUrl = `https://oapi.dingtalk.com/media/upload?access_token=${token}&type=${mediaType}`;

    log?.debug?.(`[DingTalk] Uploading media: ${filename} (${stats.size} bytes) as ${mediaType}`);

    const response = await axios.post(uploadUrl, form, {
      headers: form.getHeaders(),
      maxBodyLength: Infinity,
      maxContentLength: Infinity,
      ...getProxyBypassOption(config),
    });

    if (response.data?.errcode === 0 && response.data?.media_id) {
      log?.debug?.(
        `[DingTalk] Media uploaded successfully: ${response.data.media_id} (${stats.size} bytes)`,
      );
      return response.data.media_id;
    } else {
      log?.error?.(`[DingTalk] Media upload failed: ${JSON.stringify(response.data)}`);
      return null;
    }
  } catch (err: unknown) {
    // Handle file system errors (e.g., file not found, permission denied)
    const errno = err as NodeJS.ErrnoException;
    if (errno.code === "ENOENT") {
      log?.error?.(`[DingTalk] Media file not found: ${mediaPath}`);
    } else if (errno.code === "EACCES") {
      log?.error?.(`[DingTalk] Permission denied accessing media file: ${mediaPath}`);
    } else {
      log?.error?.(`[DingTalk] Failed to upload media: ${err instanceof Error ? err.message : String(err)}`);
      if (axios.isAxiosError(err) && err.response) {
        const status = err.response.status;
        const statusText = err.response.statusText;
        const statusLabel = status ? ` status=${status}${statusText ? ` ${statusText}` : ""}` : "";
        log?.error?.(`[DingTalk] Upload response${statusLabel}`);
        log?.error?.(formatDingTalkErrorPayloadLog("media.upload", err.response.data));
      }
    }
    return null;
  } finally {
    // Ensure file stream is closed even on error
    if (fileStream) {
      fileStream.destroy();
    }
  }
}
