/**
 * Tests for the per-mode field-resolution helpers. The Channel-create
 * form derives its visible inputs from these picks, so a regression
 * here turns into "the form suddenly asks operators for fields they
 * don't need" or, worse, "the form silently swallows a required field
 * for one mode".
 */
import { describe, expect, it } from "vitest";

import type { ChannelKindMeta } from "@/hooks/use-channels";
import {
  defaultMode,
  isDualMode,
  pickHiddenFields,
  pickOptionalFields,
  pickRequiredFields,
  pickWebhookOnlyFields,
  pickWebhookRequiredFields,
} from "@/lib/channel-mode-fields";

const wechatMeta: ChannelKindMeta = {
  kind: "wechat",
  display_name: "WeChat",
  description: "",
  docs_url: "",
  required_config_fields: [],
  optional_config_fields: ["bot_token", "bot_uin"],
  supports_outbound: true,
  supported_modes: ["webhook", "stream"],
  default_mode: "stream",
  mode_required_fields: { stream: [], webhook: ["bot_token"] },
  mode_optional_fields: {
    stream: ["bot_uin"],
    webhook: ["relay_token", "bot_uin"],
  },
};

const dingtalkMeta: ChannelKindMeta = {
  kind: "dingtalk",
  display_name: "DingTalk",
  description: "",
  docs_url: "",
  required_config_fields: ["webhook_url", "sign_secret"],
  optional_config_fields: ["client_id", "client_secret", "verify_signatures"],
  supports_outbound: true,
  supported_modes: ["webhook", "stream"],
  default_mode: "stream",
  mode_required_fields: {
    stream: ["client_id", "client_secret"],
    webhook: ["webhook_url", "sign_secret"],
  },
};

const slackMeta: ChannelKindMeta = {
  kind: "slack",
  display_name: "Slack",
  description: "",
  docs_url: "",
  required_config_fields: ["bot_token", "signing_secret"],
  optional_config_fields: [],
  supports_outbound: true,
  // Slack ships only one transport — single-mode providers must skip the
  // mode picker and the per-mode fanout entirely.
  supported_modes: ["webhook"],
  default_mode: "webhook",
  mode_required_fields: null,
};

describe("pickRequiredFields", () => {
  it("returns the per-mode override when present", () => {
    expect(pickRequiredFields(dingtalkMeta, "stream")).toEqual([
      "client_id",
      "client_secret",
    ]);
    expect(pickRequiredFields(dingtalkMeta, "webhook")).toEqual([
      "webhook_url",
      "sign_secret",
    ]);
  });

  it("falls back to the global list when the provider doesn't split", () => {
    expect(pickRequiredFields(slackMeta, "webhook")).toEqual([
      "bot_token",
      "signing_secret",
    ]);
  });

  it("returns an empty list for wechat stream (the QR-bind path)", () => {
    expect(pickRequiredFields(wechatMeta, "stream")).toEqual([]);
  });
});

describe("pickOptionalFields", () => {
  it("uses the per-mode override when present", () => {
    expect(pickOptionalFields(wechatMeta, "stream")).toEqual(["bot_uin"]);
    expect(pickOptionalFields(wechatMeta, "webhook")).toEqual([
      "relay_token",
      "bot_uin",
    ]);
  });

  it("falls back to the global optional list", () => {
    expect(pickOptionalFields(slackMeta, "webhook")).toEqual([]);
  });
});

describe("pickHiddenFields", () => {
  it("returns [] when the provider hasn't declared a hidden set", () => {
    expect(pickHiddenFields(wechatMeta, "stream")).toEqual([]);
  });
});

describe("pickWebhookOnlyFields", () => {
  it("returns webhook fields that aren't part of stream", () => {
    expect(pickWebhookOnlyFields(wechatMeta).sort()).toEqual([
      "bot_token",
      "relay_token",
    ]);
  });

  it("returns the full webhook set when stream and webhook are disjoint", () => {
    expect(pickWebhookOnlyFields(dingtalkMeta).sort()).toEqual([
      "sign_secret",
      "webhook_url",
    ]);
  });

  it("returns [] when the provider doesn't support webhook mode", () => {
    const streamOnly: ChannelKindMeta = {
      ...wechatMeta,
      supported_modes: ["stream"],
    };
    expect(pickWebhookOnlyFields(streamOnly)).toEqual([]);
  });
});

describe("pickWebhookRequiredFields", () => {
  it("returns the webhook required tuple verbatim", () => {
    expect(pickWebhookRequiredFields(wechatMeta)).toEqual(["bot_token"]);
  });

  it("returns [] when webhook is not supported", () => {
    const streamOnly: ChannelKindMeta = {
      ...wechatMeta,
      supported_modes: ["stream"],
    };
    expect(pickWebhookRequiredFields(streamOnly)).toEqual([]);
  });
});

describe("isDualMode", () => {
  it("is true when both modes are supported", () => {
    expect(isDualMode(dingtalkMeta)).toBe(true);
  });

  it("is false for single-transport providers", () => {
    expect(isDualMode(slackMeta)).toBe(false);
  });
});

describe("defaultMode", () => {
  it("uses the explicit default_mode when set", () => {
    expect(defaultMode(wechatMeta)).toBe("stream");
  });

  it("falls back to the first supported mode when default is missing", () => {
    const missing: ChannelKindMeta = {
      ...wechatMeta,
      default_mode: undefined,
      supported_modes: ["webhook"],
    };
    expect(defaultMode(missing)).toBe("webhook");
  });
});
