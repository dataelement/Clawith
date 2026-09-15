import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  parseStoredChannelConfig,
  parseAtlassianTestResult,
  parseWechatQr,
  parseWechatQrStatus,
  parseWebhookConfig,
  readOptionalChannelResource,
} from "../src/services/channelConfigResponse.ts";
import { channelApi, fetchVoid } from "../src/services/api.ts";

const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  removeItem: (key) => storage.delete(key),
  setItem: (key, value) => storage.set(key, value),
};
globalThis.window = { location: { href: "" } };

const channelConfigSource = readFileSync(
  new URL("../src/components/ChannelConfig.tsx", import.meta.url),
  "utf8",
);

test("only an explicit 404 is treated as an unconfigured channel", async () => {
  assert.equal(
    await readOptionalChannelResource(
      async () => Promise.reject({ status: 404 }),
      parseStoredChannelConfig,
    ),
    null,
  );
  for (const status of [401, 403, 500]) {
    await assert.rejects(() =>
      readOptionalChannelResource(
        async () => Promise.reject({ status }),
        parseStoredChannelConfig,
      ),
    );
  }
});

test("malformed successful channel responses remain errors", async () => {
  await assert.rejects(
    () =>
      readOptionalChannelResource(
        async () => ({ is_configured: "yes" }),
        parseStoredChannelConfig,
      ),
    /channel configuration/i,
  );
  assert.throws(() => parseWebhookConfig({ webhook_url: 42 }), /webhook/i);
  assert.throws(() => parseAtlassianTestResult({ ok: "yes" }), /Atlassian/i);
  assert.throws(() => parseWechatQr({ qrcode: 1 }));
  assert.throws(() => parseWechatQrStatus({ status: 1 }));
});

test("valid channel and webhook responses are parsed", () => {
  assert.deepEqual(
    parseStoredChannelConfig({
      is_configured: true,
      is_connected: false,
      app_id: "app",
      extra_config: { connection_mode: "websocket" },
    }),
    {
      is_configured: true,
      is_connected: false,
      app_id: "app",
      extra_config: { connection_mode: "websocket" },
    },
  );
  assert.deepEqual(
    parseWebhookConfig({ webhook_url: "https://example.test" }),
    {
      webhook_url: "https://example.test",
    },
  );
});

test("channel service preserves status for the UI 404-only mapping", async () => {
  for (const status of [404, 401, 403, 500]) {
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ detail: `HTTP ${status}` }), { status });
    const load = () => channelApi.get("agent-1");
    if (status === 404) {
      assert.equal(
        await readOptionalChannelResource(load, parseStoredChannelConfig),
        null,
      );
    } else {
      await assert.rejects(
        () => readOptionalChannelResource(load, parseStoredChannelConfig),
        (error) => error?.status === status,
      );
    }
  }
});

test("JSON and void transports share non-auth 401 logout behavior", async () => {
  for (const load of [
    () => channelApi.get("agent-1"),
    () => fetchVoid("/agents/agent-1/channel", { method: "DELETE" }),
  ]) {
    storage.set("token", "expired");
    storage.set("user", "cached");
    globalThis.window.location.href = "";
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ detail: "Expired" }), { status: 401 });

    await assert.rejects(load, (error) => error?.status === 401);
    assert.equal(storage.has("token"), false);
    assert.equal(storage.has("user"), false);
    assert.equal(globalThis.window.location.href, "/login");
  }
});

test("ChannelConfig never types raw JSON before parsing", () => {
  assert.doesNotMatch(channelConfigSource, /fetchAuth</);
  assert.match(channelConfigSource, /parseAtlassianTestResult/);
  assert.match(channelConfigSource, /parseWechatQr/);
  assert.match(channelConfigSource, /parseWechatQrStatus/);
  assert.match(channelConfigSource, /fetchAuthVoid/);
});
