import type { OpenClawConfig, ChannelOnboardingAdapter, WizardPrompter } from "openclaw/plugin-sdk";
import { DEFAULT_ACCOUNT_ID, normalizeAccountId, formatDocsLink } from "openclaw/plugin-sdk";
import type { DingTalkConfig, DingTalkChannelConfig } from "./types.js";
import { listDingTalkAccountIds, resolveDingTalkAccount } from "./types.js";
import { DEFAULT_JOURNAL_TTL_DAYS } from "./quote-journal.js";

const channel = "dingtalk" as const;

function isConfigured(account: DingTalkConfig): boolean {
  return Boolean(account.clientId && account.clientSecret);
}

function parseList(value: string): string[] {
  return value
    .split(/[\n,;]+/g)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function applyAccountNameToChannelSection(params: {
  cfg: OpenClawConfig;
  channelKey: string;
  accountId: string;
  name?: string;
}): OpenClawConfig {
  const { cfg, channelKey, name } = params;
  if (!name) {
    return cfg;
  }
  const base = cfg.channels?.[channelKey] as DingTalkChannelConfig | undefined;
  return {
    ...cfg,
    channels: {
      ...cfg.channels,
      [channelKey]: { ...base, name },
    },
  };
}

async function promptDingTalkAccountId(options: {
  cfg: OpenClawConfig;
  prompter: WizardPrompter;
  label: string;
  currentId: string;
  listAccountIds: (cfg: OpenClawConfig) => string[];
  defaultAccountId: string;
}): Promise<string> {
  const existingIds = options.listAccountIds(options.cfg);
  if (existingIds.length === 0) {
    return options.defaultAccountId;
  }
  const useExisting = await options.prompter.confirm({
    message: `Use existing ${options.label} account?`,
    initialValue: true,
  });
  if (useExisting && existingIds.includes(options.currentId)) {
    return options.currentId;
  }
  const newId = await options.prompter.text({
    message: `New ${options.label} account ID`,
    placeholder: options.defaultAccountId,
    initialValue: options.defaultAccountId,
  });
  return normalizeAccountId(String(newId));
}

async function noteDingTalkHelp(prompter: WizardPrompter): Promise<void> {
  await prompter.note(
    [
      "You need DingTalk application credentials.",
      "1. Visit https://open-dev.dingtalk.com/",
      "2. Create an enterprise internal application",
      "3. Enable 'Robot' capability",
      "4. Configure message receiving mode as 'Stream mode'",
      "5. Copy Client ID (AppKey) and Client Secret (AppSecret)",
      `Docs: ${formatDocsLink("/channels/dingtalk", "channels/dingtalk")}`,
    ].join("\n"),
    "DingTalk setup",
  );
}

function applyAccountConfig(params: {
  cfg: OpenClawConfig;
  accountId: string;
  input: Partial<DingTalkConfig>;
}): OpenClawConfig {
  const { cfg, accountId, input } = params;
  const useDefault = accountId === DEFAULT_ACCOUNT_ID;

  const namedConfig = applyAccountNameToChannelSection({
    cfg,
    channelKey: "dingtalk",
    accountId,
    name: input.name,
  });
  const base = namedConfig.channels?.dingtalk as DingTalkChannelConfig | undefined;

  const payload: Partial<DingTalkConfig> = {
    ...(input.clientId ? { clientId: input.clientId } : {}),
    ...(input.clientSecret ? { clientSecret: input.clientSecret } : {}),
    ...(input.robotCode ? { robotCode: input.robotCode } : {}),
    ...(input.corpId ? { corpId: input.corpId } : {}),
    ...(input.agentId ? { agentId: input.agentId } : {}),
    ...(input.dmPolicy ? { dmPolicy: input.dmPolicy } : {}),
    ...(input.groupPolicy ? { groupPolicy: input.groupPolicy } : {}),
    ...(input.allowFrom && input.allowFrom.length > 0 ? { allowFrom: input.allowFrom } : {}),
    ...(input.messageType ? { messageType: input.messageType } : {}),
    ...(input.cardTemplateId ? { cardTemplateId: input.cardTemplateId } : {}),
    ...(input.cardTemplateKey ? { cardTemplateKey: input.cardTemplateKey } : {}),
    ...(typeof input.maxReconnectCycles === "number"
      ? { maxReconnectCycles: input.maxReconnectCycles }
      : {}),
    ...(typeof input.useConnectionManager === "boolean"
      ? { useConnectionManager: input.useConnectionManager }
      : {}),
    ...(typeof input.mediaMaxMb === "number" ? { mediaMaxMb: input.mediaMaxMb } : {}),
    ...(typeof input.journalTTLDays === "number" ? { journalTTLDays: input.journalTTLDays } : {}),
  };

  if (useDefault) {
    return {
      ...namedConfig,
      channels: {
        ...namedConfig.channels,
        dingtalk: {
          ...base,
          enabled: true,
          ...payload,
        },
      },
    };
  }

  const accounts = (base as { accounts?: Record<string, unknown> }).accounts ?? {};
  const existingAccount =
    (base as { accounts?: Record<string, Record<string, unknown>> }).accounts?.[accountId] ?? {};

  return {
    ...namedConfig,
    channels: {
      ...namedConfig.channels,
      dingtalk: {
        ...base,
        enabled: base?.enabled ?? true,
        accounts: {
          ...accounts,
          [accountId]: {
            ...existingAccount,
            enabled: true,
            ...payload,
          },
        },
      },
    },
  };
}

export const dingtalkOnboardingAdapter: ChannelOnboardingAdapter = {
  channel,
  getStatus: ({ cfg }) => {
    const accountIds = listDingTalkAccountIds(cfg);
    const configured =
      accountIds.length > 0
        ? accountIds.some((accountId) => isConfigured(resolveDingTalkAccount(cfg, accountId)))
        : isConfigured(resolveDingTalkAccount(cfg, DEFAULT_ACCOUNT_ID));

    return Promise.resolve({
      channel,
      configured,
      statusLines: [`DingTalk: ${configured ? "configured" : "needs setup"}`],
      selectionHint: configured ? "configured" : "钉钉企业机器人",
      quickstartScore: configured ? 1 : 4,
    });
  },
  configure: async ({ cfg, prompter, accountOverrides, shouldPromptAccountIds }) => {
    const override = accountOverrides[channel]?.trim();
    let accountId = override ? normalizeAccountId(override) : DEFAULT_ACCOUNT_ID;

    if (shouldPromptAccountIds && !override) {
      accountId = await promptDingTalkAccountId({
        cfg,
        prompter,
        label: "DingTalk",
        currentId: accountId,
        listAccountIds: listDingTalkAccountIds,
        defaultAccountId: DEFAULT_ACCOUNT_ID,
      });
    }

    const resolved = resolveDingTalkAccount(cfg, accountId);
    await noteDingTalkHelp(prompter);

    const clientId = await prompter.text({
      message: "Client ID (AppKey)",
      placeholder: "dingxxxxxxxx",
      initialValue: resolved.clientId ?? undefined,
      validate: (value) => (String(value ?? "").trim() ? undefined : "Required"),
    });

    const clientSecret = await prompter.text({
      message: "Client Secret (AppSecret)",
      placeholder: "xxx-xxx-xxx-xxx",
      initialValue: resolved.clientSecret ?? undefined,
      validate: (value) => (String(value ?? "").trim() ? undefined : "Required"),
    });

    const wantsFullConfig = await prompter.confirm({
      message: "Configure robot code, corp ID, and agent ID? (recommended for full features)",
      initialValue: false,
    });

    let robotCode: string | undefined;
    let corpId: string | undefined;
    let agentId: string | undefined;

    if (wantsFullConfig) {
      robotCode =
        String(
          await prompter.text({
            message: "Robot Code",
            placeholder: "dingxxxxxxxx",
            initialValue: resolved.robotCode ?? undefined,
          }),
        ).trim() || undefined;

      corpId =
        String(
          await prompter.text({
            message: "Corp ID",
            placeholder: "dingxxxxxxxx",
            initialValue: resolved.corpId ?? undefined,
          }),
        ).trim() || undefined;

      agentId =
        String(
          await prompter.text({
            message: "Agent ID",
            placeholder: "123456789",
            initialValue: resolved.agentId ? String(resolved.agentId) : undefined,
          }),
        ).trim() || undefined;
    }

    const wantsCardMode = await prompter.confirm({
      message: "Enable AI interactive card mode? (for streaming AI responses)",
      initialValue: resolved.messageType === "card",
    });

    let cardTemplateId: string | undefined;
    let cardTemplateKey: string | undefined;
    let messageType: "markdown" | "card" = "markdown";

    if (wantsCardMode) {
      await prompter.note(
        [
          "Create an AI card template in DingTalk Developer Console:",
          "https://open-dev.dingtalk.com/fe/card",
          "1. Go to 'My Templates' > 'Create Template'",
          "2. Select 'AI Card' scenario",
          "3. Design your card and publish",
          "4. Copy the Template ID (e.g., xxx.schema)",
        ].join("\n"),
        "Card Template Setup",
      );

      cardTemplateId =
        String(
          await prompter.text({
            message: "Card Template ID",
            placeholder: "xxxxx-xxxxx-xxxxx.schema",
            initialValue: resolved.cardTemplateId ?? undefined,
          }),
        ).trim() || undefined;

      cardTemplateKey =
        String(
          await prompter.text({
            message: "Card Template Key (content field name)",
            placeholder: "content",
            initialValue: resolved.cardTemplateKey ?? "content",
          }),
        ).trim() || "content";

      messageType = "card";
    }

    const dmPolicyValue = await prompter.select({
      message: "Direct message policy",
      options: [
        { label: "Open - anyone can DM", value: "open" },
        { label: "Allowlist - only allowed users", value: "allowlist" },
      ],
      initialValue: resolved.dmPolicy ?? "open",
    });

    let allowFrom: string[] | undefined;
    if (dmPolicyValue === "allowlist") {
      const entry = await prompter.text({
        message: "Allowed user IDs (comma-separated)",
        placeholder: "user1, user2",
      });
      const parsed = parseList(String(entry ?? ""));
      allowFrom = parsed.length > 0 ? parsed : undefined;
    }

    const mediaUrlAllowlistEntry = await prompter.text({
      message: "Media URL allowlist (comma-separated host/IP/CIDR, optional)",
      placeholder: "cdn.example.com, 192.168.1.23, 10.0.0.0/8",
      initialValue: (resolved.mediaUrlAllowlist || []).join(", ") || undefined,
    });
    const mediaUrlAllowlistParsed = parseList(String(mediaUrlAllowlistEntry ?? ""));
    const mediaUrlAllowlist = mediaUrlAllowlistParsed.length > 0 ? mediaUrlAllowlistParsed : undefined;

    const groupPolicyValue = await prompter.select({
      message: "Group message policy",
      options: [
        { label: "Open - any group can use bot", value: "open" },
        { label: "Allowlist - only allowed groups", value: "allowlist" },
      ],
      initialValue: resolved.groupPolicy ?? "open",
    });

    let maxReconnectCycles: number | undefined;
    const wantsReconnectLimits = await prompter.confirm({
      message: "Configure runtime reconnect cycle limit? (recommended)",
      initialValue: typeof resolved.maxReconnectCycles === "number",
    });
    if (wantsReconnectLimits) {
      const parsedCycles = Number(
        String(
          await prompter.text({
            message: "Max runtime reconnect cycles",
            placeholder: "10",
            initialValue: String(resolved.maxReconnectCycles ?? 10),
            validate: (value) => {
              const raw = String(value ?? "").trim();
              const num = Number(raw);
              if (!raw) {
                return "Required";
              }
              if (!Number.isInteger(num) || num < 1) {
                return "Must be an integer >= 1";
              }
              return undefined;
            },
          }),
        ).trim(),
      );
      maxReconnectCycles = Number.isInteger(parsedCycles) && parsedCycles > 0 ? parsedCycles : 10;
    }

    let mediaMaxMb: number | undefined;
    const wantsMediaMax = await prompter.confirm({
      message: "Configure inbound media max size in MB? (optional)",
      initialValue: typeof resolved.mediaMaxMb === "number",
    });
    if (wantsMediaMax) {
      const parsedMediaMax = Number(
        String(
          await prompter.text({
            message: "Max inbound media size (MB)",
            placeholder: "20",
            initialValue:
              typeof resolved.mediaMaxMb === "number" ? String(resolved.mediaMaxMb) : "20",
            validate: (value) => {
              const raw = String(value ?? "").trim();
              const num = Number(raw);
              if (!raw) {
                return "Required";
              }
              if (!Number.isInteger(num) || num < 1) {
                return "Must be an integer >= 1";
              }
              return undefined;
            },
          }),
        ).trim(),
      );
      mediaMaxMb = Number.isInteger(parsedMediaMax) && parsedMediaMax > 0 ? parsedMediaMax : 20;
    }

    let journalTTLDays: number | undefined;
    const wantsJournalTTL = await prompter.confirm({
      message: "Configure quote journal retention in days?",
      initialValue: typeof resolved.journalTTLDays === "number",
    });
    if (wantsJournalTTL) {
      const parsedJournalTTL = Number(
        String(
          await prompter.text({
            message: "Quote journal retention days",
            placeholder: String(DEFAULT_JOURNAL_TTL_DAYS),
            initialValue:
              typeof resolved.journalTTLDays === "number"
                ? String(resolved.journalTTLDays)
                : String(DEFAULT_JOURNAL_TTL_DAYS),
            validate: (value) => {
              const raw = String(value ?? "").trim();
              const num = Number(raw);
              if (!raw) {
                return "Required";
              }
              if (!Number.isInteger(num) || num < 1) {
                return "Must be an integer >= 1";
              }
              return undefined;
            },
          }),
        ).trim(),
      );
      journalTTLDays =
        Number.isInteger(parsedJournalTTL) && parsedJournalTTL > 0
          ? parsedJournalTTL
          : DEFAULT_JOURNAL_TTL_DAYS;
    }

    const next = applyAccountConfig({
      cfg,
      accountId,
      input: {
        clientId: String(clientId).trim(),
        clientSecret: String(clientSecret).trim(),
        robotCode,
        corpId,
        agentId,
        dmPolicy: dmPolicyValue as "open" | "allowlist",
        groupPolicy: groupPolicyValue as "open" | "allowlist",
        allowFrom,
        mediaUrlAllowlist,
        messageType,
        cardTemplateId,
        cardTemplateKey,
        maxReconnectCycles,
        mediaMaxMb,
        journalTTLDays,
      },
    });

    return { cfg: next, accountId };
  },
};
