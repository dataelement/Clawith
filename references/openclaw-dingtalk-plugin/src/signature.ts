import { createHmac } from "node:crypto";

/**
 * Generate DingTalk custom-bot style signature.
 * Sign payload format: `${timestamp}\n${secret}`
 */
export function generateDingTalkSignature(timestamp: string | number, secret: string): string {
  if (!secret) {
    throw new Error("secret is required for DingTalk signature generation");
  }

  const timestampText = String(timestamp);
  const payload = `${timestampText}\n${secret}`;
  return createHmac("sha256", secret).update(payload).digest("base64");
}
