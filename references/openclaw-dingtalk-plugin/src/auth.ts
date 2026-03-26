import axios from "axios";
import type { DingTalkConfig, Logger, TokenInfo } from "./types";
import { retryWithBackoff } from "./utils";

interface TokenCache {
  accessToken: string;
  expiry: number;
}

// Access Token cache - keyed by clientId for multi-account support.
const accessTokenCache = new Map<string, TokenCache>();

/**
 * Get DingTalk access token with clientId-scoped cache + retry.
 * Refreshes token one minute before expiry to avoid near-expiry failures.
 */
export async function getAccessToken(config: DingTalkConfig, log?: Logger): Promise<string> {
  const cacheKey = config.clientId;
  const now = Date.now();
  const cached = accessTokenCache.get(cacheKey);

  if (cached && cached.expiry > now + 60000) {
    return cached.accessToken;
  }

  const token = await retryWithBackoff(
    async () => {
      const response = await axios.post<TokenInfo>(
        "https://api.dingtalk.com/v1.0/oauth2/accessToken",
        {
          appKey: config.clientId,
          appSecret: config.clientSecret,
        },
      );

      accessTokenCache.set(cacheKey, {
        accessToken: response.data.accessToken,
        expiry: now + response.data.expireIn * 1000,
      });

      return response.data.accessToken;
    },
    { maxRetries: 3, log },
  );

  return token;
}
