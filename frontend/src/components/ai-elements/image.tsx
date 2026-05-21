"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Image` primitive).
 *
 * Wrapper around the AI SDK ``Experimental_GeneratedImage`` part that the
 * harness can produce via the ``generate_image`` tool. Renders a base64 blob
 * inline with sensible defaults (rounded card, max-width, lazy-load).
 *
 * Accepts either:
 *   - A ``GeneratedImagePart`` (``{ base64, mediaType }``) — the canonical
 *     shape from ``ai`` runtime parts.
 *   - A plain ``src`` URL — useful for showing an artifact already persisted
 *     to the workspace.
 */

import type { ImgHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export interface GeneratedImagePart {
  /** Standard base64 (no ``data:`` prefix). */
  base64?: string;
  /** Raw bytes (mutually exclusive with base64). */
  uint8Array?: Uint8Array;
  /** MIME type for the data URL. */
  mediaType?: string;
}

export type ImageProps = Omit<
  ImgHTMLAttributes<HTMLImageElement>,
  "src" | "ref"
> & {
  /** Provide either a ``part`` (preferred for tool outputs) or ``src`` for
   *  a regular URL. */
  part?: GeneratedImagePart;
  src?: string;
  alt?: string;
};

/**
 * Render an inline image.
 *
 * Falls back gracefully when the part is missing both ``base64`` and a URL
 * — emits a small "image unavailable" placeholder instead of a broken
 * ``<img>`` so streaming tool outputs that haven't materialised yet don't
 * leak browser error chrome into the transcript.
 */
export function Image({
  part,
  src,
  alt,
  className,
  loading = "lazy",
  decoding = "async",
  ...props
}: ImageProps) {
  const resolvedSrc = (() => {
    if (src) return src;
    if (!part) return null;
    if (part.base64 && part.mediaType) {
      return `data:${part.mediaType};base64,${part.base64}`;
    }
    if (part.uint8Array && part.mediaType) {
      // Modern browsers can build a Blob URL synchronously; we don't keep
      // the URL alive across renders — caller should ``part`` only the
      // first stable shape they have.
      try {
        // Cast widens the buffer-aware Uint8Array to a plain BlobPart;
        // newer @types/node distinguishes ArrayBuffer vs SharedArrayBuffer.
        const blob = new Blob([part.uint8Array as BlobPart], {
          type: part.mediaType,
        });
        return URL.createObjectURL(blob);
      } catch {
        return null;
      }
    }
    return null;
  })();

  if (!resolvedSrc) {
    return (
      <div
        className={cn(
          "flex h-32 w-full items-center justify-center rounded-md border border-dashed text-[11px] sh-muted",
          className,
        )}
        role="img"
        aria-label={alt ?? "image unavailable"}
      >
        image unavailable
      </div>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- harness renders
    // base64 / blob URLs that the Next image optimiser can't intercept.
    <img
      {...props}
      src={resolvedSrc}
      alt={alt ?? ""}
      loading={loading}
      decoding={decoding}
      className={cn(
        "h-auto max-w-full overflow-hidden rounded-md border",
        className,
      )}
    />
  );
}
