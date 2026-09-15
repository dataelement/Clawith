import assert from "node:assert/strict";
import test from "node:test";

import { authApi } from "../src/services/api.ts";

globalThis.localStorage = {
  getItem: () => null,
  removeItem: () => {},
  setItem: () => {},
};
globalThis.window = { location: { href: "" } };

const user = {
  id: "user-1",
  username: null,
  email: null,
  display_name: "Verified User",
  role: "member",
  is_platform_admin: false,
  tenant_id: null,
  title: null,
  avatar_url: null,
  is_active: true,
  email_verified: true,
  created_at: "2026-01-01T00:00:00Z",
};

test("verify-email parses the backend TokenResponse shape", async () => {
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        access_token: "token-1",
        token_type: "bearer",
        user,
        identity: {
          id: "identity-1",
          email: null,
          phone: "+10000000000",
          username: null,
          is_active: true,
          is_platform_admin: false,
          email_verified: true,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
        needs_company_setup: true,
      }),
      { status: 200 },
    );

  const response = await authApi.verifyEmail("verification-token");
  assert.equal(response.access_token, "token-1");
  assert.equal(response.user.username, "");
  assert.equal(response.user.email, "");
  assert.equal(response.needs_company_setup, true);
});

test("verify-email rejects malformed successful TokenResponse payloads", async () => {
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        access_token: "token-1",
        token_type: "bearer",
        user: { ...user, is_active: "true" },
        needs_company_setup: true,
      }),
      { status: 200 },
    );

  await assert.rejects(
    () => authApi.verifyEmail("verification-token"),
    (error) => error?.code === "invalid_api_response",
  );
});

test("adjacent auth envelopes match forgot, reset, resend, and SSO register", async () => {
  const responses = [
    { ok: true, message: "sent" },
    { ok: true },
    { ok: true, message: "sent" },
    {
      access_token: "token-2",
      token_type: "bearer",
      user,
      needs_company_setup: false,
    },
  ];
  globalThis.fetch = async () =>
    new Response(JSON.stringify(responses.shift()), { status: 200 });

  assert.equal(
    (await authApi.forgotPassword({ email: "a@example.com" })).ok,
    true,
  );
  assert.equal(
    (await authApi.resetPassword({ token: "reset", new_password: "secret" }))
      .ok,
    true,
  );
  assert.equal((await authApi.resendVerification("a@example.com")).ok, true);
  const registration = await authApi.register({
    email: "a@example.com",
    password: "secret",
    display_name: "User",
    provider: "google",
    provider_code: "code",
  });
  assert.equal(registration.user_id, "user-1");
  assert.equal(registration.email, "");
  assert.equal(registration.user?.is_active, true);
});
