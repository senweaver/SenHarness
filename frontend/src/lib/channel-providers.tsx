/**
 * Frontend brand metadata for IM channel providers.
 *
 * The functional contract (parse / verify / outbound) lives on the
 * backend at app/services/channels/. This file maps each registered
 * `kind` to its UI-facing presentation: a brand icon, a Tailwind
 * color pair for the icon tile, and the i18n keys for the display
 * name + subtitle. The set of keys here mirrors the providers
 * imported in backend/app/services/channels/__init__.py — keep them
 * in lockstep when adding a new provider.
 */

import {
  IconBrandDingtalk,
  IconBrandDiscord,
  IconBrandQq,
  IconBrandSlack,
  IconBrandTeams,
  IconBrandTelegram,
  IconBrandWechat,
  IconWebhook,
} from "@tabler/icons-react";
import type { ComponentType, SVGProps } from "react";

import type { ChannelKind } from "@/hooks/use-channels";

export type ChannelProviderIcon = ComponentType<
  SVGProps<SVGSVGElement> & { size?: number | string }
>;

export interface ChannelProviderMeta {
  kind: ChannelKind;
  /** i18n key under settings.channels.kindName */
  nameKey: string;
  /** i18n key under settings.channels.kindSubtitle */
  subtitleKey: string;
  icon: ChannelProviderIcon;
  /** Tailwind classes for the icon tile background. */
  iconBg: string;
  /** Tailwind class for the icon foreground color. */
  iconFg: string;
}

const FeishuIcon: ChannelProviderIcon = ({ size = 24, ...props }) => {
  const dim = typeof size === "number" ? `${size}px` : size;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={dim}
      height={dim}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M3 12c4 -3 8 -3 12 0c4 3 6 3 6 3" />
      <path d="M3 7c4 -2 7 -2 11 0" />
      <path d="M3 17c5 1 9 1 14 -1" />
    </svg>
  );
};
FeishuIcon.displayName = "FeishuIcon";

// Lark shares Feishu's wave glyph but tilts the brand toward the
// international palette so the picker doesn't show two visually
// identical tiles next to each other.
const LarkIcon: ChannelProviderIcon = ({ size = 24, ...props }) => {
  const dim = typeof size === "number" ? `${size}px` : size;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={dim}
      height={dim}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M4 6c4 0 8 4 8 8s4 8 8 8" />
      <path d="M4 14c3 0 5 -2 5 -5" />
      <path d="M15 4c0 3 2 5 5 5" />
    </svg>
  );
};
LarkIcon.displayName = "LarkIcon";

/**
 * Ordered list of bundled providers. The order drives the picker
 * grid in the UI; put the providers most operators reach for first.
 */
export const CHANNEL_PROVIDERS: ChannelProviderMeta[] = [
  {
    kind: "slack",
    nameKey: "settings.channels.kindName.slack",
    subtitleKey: "settings.channels.kindSubtitle.slack",
    icon: IconBrandSlack,
    iconBg: "bg-[#4A154B]/10 dark:bg-[#4A154B]/30",
    iconFg: "text-[#4A154B] dark:text-[#ECB22E]",
  },
  {
    kind: "discord",
    nameKey: "settings.channels.kindName.discord",
    subtitleKey: "settings.channels.kindSubtitle.discord",
    icon: IconBrandDiscord,
    iconBg: "bg-[#5865F2]/10 dark:bg-[#5865F2]/25",
    iconFg: "text-[#5865F2]",
  },
  {
    kind: "teams",
    nameKey: "settings.channels.kindName.teams",
    subtitleKey: "settings.channels.kindSubtitle.teams",
    icon: IconBrandTeams,
    iconBg: "bg-[#4B53BC]/10 dark:bg-[#4B53BC]/30",
    iconFg: "text-[#4B53BC]",
  },
  {
    kind: "feishu",
    nameKey: "settings.channels.kindName.feishu",
    subtitleKey: "settings.channels.kindSubtitle.feishu",
    icon: FeishuIcon,
    iconBg: "bg-[#00D6B9]/10 dark:bg-[#00D6B9]/25",
    iconFg: "text-[#00B894] dark:text-[#00D6B9]",
  },
  {
    kind: "lark",
    nameKey: "settings.channels.kindName.lark",
    subtitleKey: "settings.channels.kindSubtitle.lark",
    icon: LarkIcon,
    iconBg: "bg-[#3370FF]/10 dark:bg-[#3370FF]/25",
    iconFg: "text-[#3370FF]",
  },
  {
    kind: "wecom",
    nameKey: "settings.channels.kindName.wecom",
    subtitleKey: "settings.channels.kindSubtitle.wecom",
    icon: IconBrandWechat,
    iconBg: "bg-[#0082EF]/10 dark:bg-[#0082EF]/25",
    iconFg: "text-[#0082EF]",
  },
  {
    kind: "wechat",
    nameKey: "settings.channels.kindName.wechat",
    subtitleKey: "settings.channels.kindSubtitle.wechat",
    icon: IconBrandWechat,
    iconBg: "bg-[#1AAD19]/10 dark:bg-[#1AAD19]/25",
    iconFg: "text-[#1AAD19]",
  },
  {
    kind: "dingtalk",
    nameKey: "settings.channels.kindName.dingtalk",
    subtitleKey: "settings.channels.kindSubtitle.dingtalk",
    icon: IconBrandDingtalk,
    iconBg: "bg-[#1677FF]/10 dark:bg-[#1677FF]/25",
    iconFg: "text-[#1677FF]",
  },
  {
    kind: "qq",
    nameKey: "settings.channels.kindName.qq",
    subtitleKey: "settings.channels.kindSubtitle.qq",
    icon: IconBrandQq,
    iconBg: "bg-[#12B7F5]/10 dark:bg-[#12B7F5]/25",
    iconFg: "text-[#12B7F5]",
  },
  {
    kind: "telegram",
    nameKey: "settings.channels.kindName.telegram",
    subtitleKey: "settings.channels.kindSubtitle.telegram",
    icon: IconBrandTelegram,
    iconBg: "bg-[#229ED9]/10 dark:bg-[#229ED9]/25",
    iconFg: "text-[#229ED9]",
  },
  {
    kind: "webhook",
    nameKey: "settings.channels.kindName.webhook",
    subtitleKey: "settings.channels.kindSubtitle.webhook",
    icon: IconWebhook,
    iconBg: "bg-[rgb(var(--color-muted))]",
    iconFg: "text-[rgb(var(--color-foreground))]",
  },
];

const PROVIDERS_BY_KIND: Record<ChannelKind, ChannelProviderMeta> =
  CHANNEL_PROVIDERS.reduce(
    (acc, p) => {
      acc[p.kind] = p;
      return acc;
    },
    {} as Record<ChannelKind, ChannelProviderMeta>,
  );

export function getChannelProvider(
  kind: ChannelKind | string,
): ChannelProviderMeta {
  return (
    PROVIDERS_BY_KIND[kind as ChannelKind] ??
    PROVIDERS_BY_KIND.webhook
  );
}

/**
 * Heuristic: a config-field name that contains any of these tokens
 * is sensitive and should render as a password input + masked in
 * existing-channel patches. Mirrors the masking logic on the backend
 * (`app/services/channel.py::mask_config`).
 */
const SENSITIVE_TOKENS = ["secret", "token", "password", "key"] as const;

export function isSensitiveField(field: string): boolean {
  const lower = field.toLowerCase();
  return SENSITIVE_TOKENS.some((t) => lower.includes(t));
}
