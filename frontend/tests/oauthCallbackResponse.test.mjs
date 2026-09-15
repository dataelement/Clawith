import assert from "node:assert/strict";
import test from "node:test";

import { parseOAuthCallbackResponse } from "../src/services/oauthCallbackResponse.ts";

const user = {
  id: "user-1",
  username: null,
  email: "person@example.com",
  display_name: "Person",
  role: "member",
  is_active: true,
  created_at: "2026-08-26T00:00:00Z",
};

test("OAuth callback parser distinguishes token and tenant-selection results", () => {
  assert.deepEqual(
    parseOAuthCallbackResponse({
      access_token: "token",
      token_type: "bearer",
      user,
      needs_company_setup: false,
    }),
    {
      access_token: "token",
      token_type: "bearer",
      user: { ...user, username: "" },
      needs_company_setup: false,
    },
  );

  assert.deepEqual(
    parseOAuthCallbackResponse({
      requires_tenant_selection: true,
      login_identifier: "",
      pending_token: "pending",
      tenants: [
        {
          tenant_id: "tenant-1",
          tenant_name: "Acme",
          tenant_slug: "",
          logo_url: null,
        },
      ],
    }),
    {
      requires_tenant_selection: true,
      login_identifier: "",
      pending_token: "pending",
      tenants: [
        {
          tenant_id: "tenant-1",
          tenant_name: "Acme",
          tenant_slug: "",
        },
      ],
    },
  );
});

test("OAuth callback parser rejects incomplete and unknown variants", () => {
  assert.throws(
    () =>
      parseOAuthCallbackResponse({
        requires_tenant_selection: true,
        login_identifier: "person@example.com",
        tenants: [],
      }),
    /pending_token/,
  );
  assert.throws(
    () =>
      parseOAuthCallbackResponse({
        access_token: "token",
        token_type: "bearer",
        user: { ...user, role: "unknown" },
        needs_company_setup: false,
      }),
    /user\.role/,
  );
  assert.throws(() => parseOAuthCallbackResponse({}), /needs_company_setup/);
});
