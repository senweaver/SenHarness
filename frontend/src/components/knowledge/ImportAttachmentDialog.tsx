"use client";

import { useEffect, useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconAlertTriangle,
  IconCheck,
  IconDatabaseImport,
  IconLoader2,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCollections,
  useIngestAttachment,
} from "@/hooks/use-knowledge";

interface Props {
  attachmentId: string;
  filename: string;
  trigger: React.ReactNode;
}

/**
 * `ImportAttachmentDialog` — lets the user drop an existing chat attachment
 * into any knowledge collection they have access to. Shows:
 *
 *   * Collection picker (disabled when the workspace has none — we link the
 *     user over to `/knowledge` to create one first).
 *   * Optional title override (defaults to the filename on blur/empty).
 *   * Backend error codes surfaced as targeted i18n messages so users see
 *     *"audio can't be indexed"* or *"pypdf not installed"* instead of a
 *     generic HTTP failure.
 */
export function ImportAttachmentDialog({
  attachmentId,
  filename,
  trigger,
}: Props) {
  const t = useTranslations("knowledge.importAttachment");
  const [open, setOpen] = useState(false);
  const [collectionId, setCollectionId] = useState<string>("");
  const [title, setTitle] = useState(filename);
  const { data: collections, isLoading } = useCollections();
  const ingest = useIngestAttachment();

  useEffect(() => {
    if (!open) {
      setTitle(filename);
    }
  }, [open, filename]);

  useEffect(() => {
    if (open && collections && collections.length > 0 && !collectionId) {
      const first = collections[0];
      if (first) setCollectionId(first.id);
    }
  }, [open, collections, collectionId]);

  const hasCollections = (collections?.length ?? 0) > 0;

  const submit = async () => {
    try {
      const doc = await ingest.mutateAsync({
        collectionId,
        attachmentId,
        title: title.trim() || null,
      });
      toast.success(t("success", { chunks: doc.chunk_count }));
      setOpen(false);
    } catch (err) {
      // TanStack's error type is unknown; we normalize manually.
      const detail = (err as { response?: { data?: { detail?: { code?: string; message?: string } } } })
        ?.response?.data?.detail;
      const code = detail?.code ?? "unknown";
      const msg = detail?.message;
      // Map known backend codes → i18n messages; fall back to raw message.
      const knownKey: Record<string, string> = {
        unsupported_kind: "error.unsupported_kind",
        unsupported_mime: "error.unsupported_mime",
        file_too_large: "error.file_too_large",
        pdf_lib_missing: "error.pdf_lib_missing",
        pdf_parse_failed: "error.pdf_parse_failed",
        pdf_empty: "error.pdf_empty",
        "attachment.blob_missing": "error.blob_missing",
      };
      const key = knownKey[code];
      if (key) {
        toast.error(t(key));
      } else {
        toast.error(msg ?? t("error.generic"));
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <span
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        role="button"
        tabIndex={-1}
      >
        {trigger}
      </span>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconDatabaseImport className="size-4 text-[rgb(var(--color-primary))]" />
            {t("title")}
          </DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="rounded-md border bg-black/5 p-2 text-xs dark:bg-white/5">
            <div className="sh-muted">{t("fileLabel")}</div>
            <div className="mt-0.5 break-all font-mono">{filename}</div>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="kb-collection">{t("collectionLabel")}</Label>
            {isLoading ? (
              <div className="flex items-center gap-2 text-xs sh-muted">
                <IconLoader2 className="size-3 animate-spin" />
                {t("loading")}
              </div>
            ) : hasCollections ? (
              <Select value={collectionId} onValueChange={setCollectionId}>
                <SelectTrigger id="kb-collection">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {collections!.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}
                      <span className="ml-1 text-[10px] sh-muted">
                        ({c.doc_count} docs)
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50/40 p-2 text-xs dark:border-amber-700 dark:bg-amber-950/20">
                <IconAlertTriangle className="size-4 text-amber-500" />
                <div className="flex-1">
                  <div>{t("noCollections")}</div>
                  <Link
                    href="/knowledge"
                    onClick={() => setOpen(false)}
                    className="mt-1 inline-block text-[rgb(var(--color-primary))] hover:underline"
                  >
                    {t("goCreate")}
                  </Link>
                </div>
              </div>
            )}
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="kb-title">{t("titleLabel")}</Label>
            <Input
              id="kb-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={filename}
              maxLength={255}
            />
            <p className="text-[11px] sh-muted">{t("titleHint")}</p>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setOpen(false)}
            disabled={ingest.isPending}
          >
            {t("cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={
              ingest.isPending ||
              !hasCollections ||
              !collectionId ||
              !title.trim()
            }
          >
            {ingest.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconCheck className="size-4" />
            )}
            {t("submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
