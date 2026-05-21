"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Loader` primitive).
 *
 * A small, theme-aware spinner used while the kernel is warming up or a
 * tool is fetching remote data. Pure presentation — no client state.
 *
 * For full-bleed page skeletons prefer ``<Skeleton>`` from ``components/ui``;
 * the Loader is sized for inline contexts (next to a label, inside a
 * button, etc.).
 */

import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

interface LoaderIconProps {
  size?: number;
}

const LoaderIcon = ({ size = 16 }: LoaderIconProps) => (
  <svg
    height={size}
    width={size}
    viewBox="0 0 16 16"
    strokeLinejoin="round"
    style={{ color: "currentcolor" }}
    aria-hidden="true"
    focusable="false"
  >
    <g clipPath="url(#senharness-loader-clip)">
      <path d="M8 0V4" stroke="currentColor" strokeWidth="1.5" />
      <path d="M8 16V12" opacity="0.5" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M3.29773 1.52783L5.64887 4.7639"
        opacity="0.9"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M12.7023 1.52783L10.3511 4.7639"
        opacity="0.1"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M12.7023 14.472L10.3511 11.236"
        opacity="0.4"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M3.29773 14.472L5.64887 11.236"
        opacity="0.6"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M15.6085 5.52783L11.8043 6.7639"
        opacity="0.2"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M0.391602 10.472L4.19583 9.23598"
        opacity="0.7"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M15.6085 10.4722L11.8043 9.2361"
        opacity="0.3"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M0.391602 5.52783L4.19583 6.7639"
        opacity="0.8"
        stroke="currentColor"
        strokeWidth="1.5"
      />
    </g>
    <defs>
      <clipPath id="senharness-loader-clip">
        <rect fill="white" height="16" width="16" />
      </clipPath>
    </defs>
  </svg>
);

export type LoaderProps = HTMLAttributes<HTMLDivElement> & {
  size?: number;
};

export const Loader = ({ className, size = 16, ...props }: LoaderProps) => (
  <div
    className={cn(
      "inline-flex animate-spin items-center justify-center sh-muted",
      className,
    )}
    role="status"
    {...props}
  >
    <LoaderIcon size={size} />
  </div>
);
