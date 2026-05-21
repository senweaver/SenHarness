"use client";

import { useEffect, useRef, useState } from "react";
import {
  IconDatabaseImport,
  IconFileText,
  IconLoader2,
  IconMusic,
  IconPhoto,
  IconVideo,
  IconX,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import type { AttachmentKind } from "@/hooks/use-attachments";
import { fetchAttachmentBlobUrl } from "@/hooks/use-attachments";
import { ImportAttachmentDialog } from "@/components/knowledge/ImportAttachmentDialog";
import { cn } from "@/lib/utils";

export interface AttachmentRef {
  id: string;
  filename: string;
  mime_type: string;
  kind: AttachmentKind;
  size_bytes: number;
}

function humanSize(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / (1024 * 1024)).toFixed(1)}MB`;
}

/** Render a single attachment referenced in a chat message. */
export function AttachmentView({
  att,
  onRemove,
  compact = false,
}: {
  att: AttachmentRef;
  onRemove?: () => void;
  compact?: boolean;
}) {
  if (att.kind === "image") {
    return <ImageAttachment att={att} onRemove={onRemove} compact={compact} />;
  }
  if (att.kind === "audio") {
    return <AudioAttachment att={att} onRemove={onRemove} />;
  }
  return <FileChip att={att} onRemove={onRemove} />;
}

/** Image loaded via authed fetch → object URL. */
function ImageAttachment({
  att,
  onRemove,
  compact,
}: {
  att: AttachmentRef;
  onRemove?: () => void;
  compact: boolean;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    let obj: string | null = null;
    (async () => {
      try {
        obj = await fetchAttachmentBlobUrl(att.id);
        if (mounted.current) setUrl(obj);
      } catch {
        if (mounted.current) setErr(true);
      }
    })();
    return () => {
      mounted.current = false;
      if (obj) URL.revokeObjectURL(obj);
    };
  }, [att.id]);

  return (
    <div
      className={cn(
        "relative inline-block overflow-hidden rounded-md border bg-black/5 dark:bg-white/5",
        compact ? "h-20" : "max-h-[320px] max-w-full",
      )}
    >
      {err ? (
        <div className="flex h-full items-center justify-center p-4 text-xs sh-muted">
          <IconPhoto className="size-5" />
        </div>
      ) : url ? (
        <img
          src={url}
          alt={att.filename}
          className={cn(
            "block",
            compact ? "h-20 w-20 object-cover" : "max-h-[320px] max-w-full",
          )}
        />
      ) : (
        <div className="flex size-20 items-center justify-center">
          <IconLoader2 className="size-4 animate-spin sh-muted" />
        </div>
      )}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="absolute right-1 top-1 rounded-full bg-black/60 p-0.5 text-white hover:bg-black/80"
          aria-label="remove"
        >
          <IconX className="size-3" />
        </button>
      )}
    </div>
  );
}

function AudioAttachment({
  att,
  onRemove,
}: {
  att: AttachmentRef;
  onRemove?: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let obj: string | null = null;
    (async () => {
      try {
        obj = await fetchAttachmentBlobUrl(att.id);
        setUrl(obj);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      if (obj) URL.revokeObjectURL(obj);
    };
  }, [att.id]);

  return (
    <div className="flex items-center gap-2 rounded-md border bg-black/5 p-2 dark:bg-white/5">
      <IconMusic className="size-4 sh-muted" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium">{att.filename}</div>
        {url ? (
          <audio controls className="mt-1 w-full max-w-[320px]" src={url} />
        ) : (
          <div className="mt-1 text-[10px] sh-muted">loading…</div>
        )}
      </div>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="rounded p-1 hover:bg-black/10 dark:hover:bg-white/10"
          aria-label="remove"
        >
          <IconX className="size-3" />
        </button>
      )}
    </div>
  );
}

function FileChip({
  att,
  onRemove,
}: {
  att: AttachmentRef;
  onRemove?: () => void;
}) {
  const icon =
    att.kind === "video" ? (
      <IconVideo className="size-3.5" />
    ) : (
      <IconFileText className="size-3.5" />
    );
  // Pending uploads (compose area) pass ``onRemove``; persisted/past message
  // attachments don't — that's the signal that "Import to KB" should appear.
  // Ingest only makes sense for textual kinds; audio/video/image → we hide
  // the button upfront instead of letting the server 415 us.
  const canIngest =
    !onRemove && (att.kind === "document" || att.kind === "other");
  return (
    <span className="inline-flex items-center gap-1 rounded-md border bg-black/5 px-2 py-1 text-[11px] dark:bg-white/5">
      {icon}
      <span className="max-w-[200px] truncate font-medium">{att.filename}</span>
      <span className="sh-muted">{humanSize(att.size_bytes)}</span>
      {canIngest && (
        <ImportAttachmentButton
          attachmentId={att.id}
          filename={att.filename}
        />
      )}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="-mr-1 ml-1 rounded p-0.5 hover:bg-black/10 dark:hover:bg-white/10"
          aria-label="remove"
        >
          <IconX className="size-3" />
        </button>
      )}
    </span>
  );
}

/** Inline chip action opening the import dialog. */
function ImportAttachmentButton({
  attachmentId,
  filename,
}: {
  attachmentId: string;
  filename: string;
}) {
  const t = useTranslations("knowledge.importAttachment");
  return (
    <ImportAttachmentDialog
      attachmentId={attachmentId}
      filename={filename}
      trigger={
        <button
          type="button"
          className="-mr-1 ml-1 rounded p-0.5 text-[rgb(var(--color-primary))] hover:bg-black/10 dark:hover:bg-white/10"
          aria-label={t("triggerAria")}
          title={t("triggerTitle")}
        >
          <IconDatabaseImport className="size-3" />
        </button>
      }
    />
  );
}
