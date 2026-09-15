import assert from "node:assert/strict";
import test from "node:test";

import {
  parseEmailTemplates,
  parseIdentityProviders,
  parseNotificationBarSetting,
  parsePlatformSettings,
  parseSystemEmailSetting,
} from "../src/services/platformAdminConfigResponse.ts";

test("platform admin config parsers reject malformed successful payloads", () => {
  assert.throws(() => parsePlatformSettings({}), /allow_self_create_company/);
  assert.throws(() => parseNotificationBarSetting({}), /value/);
  assert.throws(
    () => parseNotificationBarSetting({ value: { enabled: "yes", text: "x" } }),
    /enabled/,
  );
  assert.throws(
    () =>
      parseSystemEmailSetting({
        value: {
          SYSTEM_EMAIL_ENABLED: true,
          SYSTEM_EMAIL_FROM_ADDRESS: "a@example.test",
          SYSTEM_EMAIL_FROM_NAME: "A",
          SYSTEM_SMTP_HOST: "smtp.example.test",
          SYSTEM_SMTP_PORT: "465",
          SYSTEM_SMTP_USERNAME: "a",
          SYSTEM_SMTP_PASSWORD: "secret",
          SYSTEM_SMTP_SSL: true,
          SYSTEM_SMTP_TIMEOUT_SECONDS: 15,
        },
      }),
    /SYSTEM_SMTP_PORT/,
  );
  assert.throws(
    () =>
      parseEmailTemplates({
        templates: {},
        variables: { welcome: ["ok", 1] },
        defaults: {},
      }),
    /variables/,
  );
  assert.throws(
    () =>
      parseIdentityProviders([
        {
          id: "1",
          provider_type: "google",
          name: "Google",
          is_active: true,
          config: [],
        },
      ]),
    /config/,
  );
});

test("platform admin config parsers accept complete contracts", () => {
  assert.equal(
    parsePlatformSettings({
      allow_self_create_company: true,
      invitation_code_enabled: false,
      sso_custom_domain_redirect_enabled: true,
    }).invitation_code_enabled,
    false,
  );
  assert.deepEqual(
    parseEmailTemplates({
      templates: { welcome: { subject: "Hi", body: "Body" } },
      variables: { welcome: ["name"] },
      defaults: { welcome: { subject: "Hi", body: "Body" } },
    }).variables,
    { welcome: ["name"] },
  );
  assert.deepEqual(parseNotificationBarSetting({ value: {} }).value, {
    enabled: false,
    text: "",
  });
  assert.equal(
    parseSystemEmailSetting({ value: {} }).value.SYSTEM_SMTP_PORT,
    465,
  );
  assert.deepEqual(
    parseIdentityProviders([
      {
        id: "1",
        provider_type: "google",
        name: "Google",
        is_active: false,
        config: null,
      },
    ])[0].config,
    {},
  );
});
