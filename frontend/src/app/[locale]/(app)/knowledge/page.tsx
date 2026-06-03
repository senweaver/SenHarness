"use client";

import { useState } from "react";
import {
  IconBook,
  IconFileText,
  IconFileUpload,
  IconLink,
  IconLoader2,
  IconPlus,
  IconSearch,
  IconSparkles,
  IconTrash,
  IconWorld,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/ui/page-header";
import {
  type DocSourceKind,
  type KnowledgeCollectionCard,
  type KnowledgeDocRead,
  useCollections,
  useCreateCollection,
  useDeleteCollection,
  useDeleteDoc,
  useDocs,
  useIngestAttachment,
  useIngestDoc,
  useSearchCollection,
} from "@/hooks/use-knowledge";
import { useUploadAttachment } from "@/hooks/use-attachments";

export default function KnowledgePage() {
  const t = useTranslations("knowledge");
  const tCommon = useTranslations("common");
  const { data: collections, isLoading } = useCollections();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const active = (collections ?? []).find((c) => c.id === activeId) ?? null;

  return (
    <div className="p-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button size="sm" onClick={() => setCreating((x) => !x)}>
            <IconPlus className="size-4" />
            {creating ? tCommon("cancel") : t("newCollection")}
          </Button>
        }
      />

      {creating && (
        <CreateCollectionForm onDone={() => setCreating(false)} />
      )}

      {isLoading && <Skeleton className="h-40" />}

      {!isLoading && (collections ?? []).length === 0 && !creating && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            <IconSparkles className="mx-auto mb-2 size-8" />
            {t("empty")}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-3 lg:grid-cols-[300px_1fr]">
        <CollectionList
          collections={collections ?? []}
          active={active}
          onSelect={setActiveId}
        />
        {active && <CollectionPanel collection={active} />}
      </div>
    </div>
  );
}

function CreateCollectionForm({ onDone }: { onDone: () => void }) {
  const t = useTranslations("knowledge");
  const tCommon = useTranslations("common");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useCreateCollection();

  const submit = async () => {
    if (!name.trim()) return;
    try {
      await create.mutateAsync({ name, description: description || null });
      toast.success(t("created"));
      onDone();
      setName("");
      setDescription("");
    } catch {
      toast.error(t("createFailed"));
    }
  };

  return (
    <Card className="mb-3">
      <CardContent className="grid gap-2 py-3 sm:grid-cols-[1fr_1fr_auto]">
        <Input
          placeholder={t("form.namePlaceholder")}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Input
          placeholder={t("form.descriptionPlaceholder")}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <div className="flex gap-1">
          <Button variant="ghost" onClick={onDone} disabled={create.isPending}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={submit} disabled={create.isPending || !name.trim()}>
            {create.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {tCommon("save")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function CollectionList({
  collections,
  active,
  onSelect,
}: {
  collections: KnowledgeCollectionCard[];
  active: KnowledgeCollectionCard | null;
  onSelect: (id: string) => void;
}) {
  const t = useTranslations("knowledge");
  const remove = useDeleteCollection();

  return (
    <div className="flex flex-col gap-2">
      {collections.map((c) => (
        <button
          key={c.id}
          onClick={() => onSelect(c.id)}
          className={`group rounded-md border p-3 text-left transition-colors ${
            active?.id === c.id
              ? "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary))]/5"
              : "hover:bg-black/5 dark:hover:bg-white/5"
          }`}
        >
          <div className="flex items-center gap-2">
            <IconBook className="size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{c.name}</div>
              {c.description && (
                <div className="truncate text-[11px] sh-muted">
                  {c.description}
                </div>
              )}
            </div>
          </div>
          <div className="mt-1 flex items-center gap-1 text-[10px] sh-muted">
            <span>{t("docCount", { count: c.doc_count })}</span>
            <span>·</span>
            <span>{t("chunkCount", { count: c.chunk_count })}</span>
            <span
              className="ml-auto hidden rounded px-1 hover:bg-red-500/20 group-hover:inline"
              onClick={async (e) => {
                e.stopPropagation();
                if (!confirm(t("confirmDelete"))) return;
                try {
                  await remove.mutateAsync(c.id);
                  toast.success(t("deleted"));
                } catch {
                  toast.error(t("deleteFailed"));
                }
              }}
              title={t("delete")}
            >
              <IconTrash className="size-3 text-red-600" />
            </span>
          </div>
        </button>
      ))}
    </div>
  );
}

function CollectionPanel({
  collection,
}: {
  collection: KnowledgeCollectionCard;
}) {
  const t = useTranslations("knowledge");
  const [tab, setTab] = useState<"docs" | "ingest" | "search">("docs");

  return (
    <div>
      <div className="mb-3 flex items-center gap-1 rounded-md border p-0.5 text-sm">
        <TabBtn
          active={tab === "docs"}
          onClick={() => setTab("docs")}
          label={t("tabs.docs")}
        />
        <TabBtn
          active={tab === "ingest"}
          onClick={() => setTab("ingest")}
          label={t("tabs.ingest")}
        />
        <TabBtn
          active={tab === "search"}
          onClick={() => setTab("search")}
          label={t("tabs.search")}
        />
      </div>

      {tab === "docs" && <DocsTab collection={collection} />}
      {tab === "ingest" && <IngestTab collection={collection} />}
      {tab === "search" && <SearchTab collection={collection} />}
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 rounded px-3 py-1.5 transition-colors ${
        active
          ? "bg-black/5 font-medium dark:bg-white/10"
          : "hover:bg-black/5 dark:hover:bg-white/5"
      }`}
    >
      {label}
    </button>
  );
}

function DocsTab({ collection }: { collection: KnowledgeCollectionCard }) {
  const t = useTranslations("knowledge");
  const { data, isLoading } = useDocs(collection.id);
  const remove = useDeleteDoc(collection.id);

  if (isLoading) return <Skeleton className="h-40" />;
  if (!data?.length) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm sh-muted">
          {t("docsEmpty")}
        </CardContent>
      </Card>
    );
  }

  const onDelete = async (doc: KnowledgeDocRead) => {
    if (!confirm(t("confirmDeleteDoc"))) return;
    try {
      await remove.mutateAsync(doc.id);
      toast.success(t("deleted"));
    } catch {
      toast.error(t("deleteFailed"));
    }
  };

  return (
    <div className="flex flex-col gap-2">
      {data.map((d) => (
        <Card key={d.id}>
          <CardContent className="flex items-center gap-2 py-3">
            {d.source_kind === "url" ? (
              <IconLink className="size-4 sh-muted" />
            ) : d.source_kind === "file" ? (
              <IconFileText className="size-4 sh-muted" />
            ) : (
              <IconFileText className="size-4 sh-muted" />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{d.title}</div>
              {d.source_uri && (
                <div className="truncate text-[11px] sh-muted">
                  {d.source_uri}
                </div>
              )}
              {d.error && (
                <div className="truncate text-[11px] text-red-600">
                  {d.error}
                </div>
              )}
            </div>
            <Badge
              variant={
                d.status === "ready"
                  ? "success"
                  : d.status === "failed"
                    ? "danger"
                    : "outline"
              }
            >
              {d.status}
            </Badge>
            <span className="text-[11px] sh-muted tabular-nums">
              {t("chunks", { n: d.chunk_count })}
            </span>
            <Button
              size="icon"
              variant="ghost"
              className="size-7"
              onClick={() => onDelete(d)}
              title={t("delete")}
            >
              <IconTrash className="size-3.5" />
            </Button>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function IngestTab({ collection }: { collection: KnowledgeCollectionCard }) {
  const t = useTranslations("knowledge");
  const ingest = useIngestDoc(collection.id);
  const upload = useUploadAttachment();
  const ingestAttachment = useIngestAttachment();

  const [sourceKind, setSourceKind] = useState<DocSourceKind>("text");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);

  // When the user picks a file and hasn't typed a title yet, pre-fill the
  // title from the filename so they can submit in one click.
  const onFilePick = (f: File | null) => {
    setFile(f);
    if (f && !title.trim()) {
      setTitle(f.name);
    }
  };

  // ``"file"`` mode runs a 2-step pipeline (upload → ingest_attachment) so
  // ``ingest.isPending`` doesn't cover the whole flow. We aggregate the
  // pending state ourselves.
  const submitting =
    ingest.isPending || upload.isPending || ingestAttachment.isPending;

  const submit = async () => {
    if (!title.trim()) {
      toast.error(t("missingTitle"));
      return;
    }
    if (sourceKind === "text" && !text.trim()) {
      toast.error(t("missingText"));
      return;
    }
    if (sourceKind === "url" && !url.trim()) {
      toast.error(t("missingUrl"));
      return;
    }
    if (sourceKind === "file" && !file) {
      toast.error(t("missingFile"));
      return;
    }

    try {
      if (sourceKind === "file" && file) {
        // Two-step: upload the file as an Attachment, then ingest into KB.
        const att = await upload.mutateAsync(file);
        const doc = await ingestAttachment.mutateAsync({
          collectionId: collection.id,
          attachmentId: att.id,
          title: title.trim(),
        });
        toast.success(
          doc.status === "ready"
            ? t("ingestSuccess", { chunks: doc.chunk_count })
            : `${doc.status}: ${doc.error ?? ""}`,
        );
        setTitle("");
        setFile(null);
        return;
      }

      const doc = await ingest.mutateAsync({
        title: title.trim(),
        source_kind: sourceKind,
        source_uri: sourceKind === "url" ? url.trim() : null,
        raw_text: sourceKind === "text" ? text : null,
      });
      toast.success(
        doc.status === "ready"
          ? t("ingestSuccess", { chunks: doc.chunk_count })
          : `${doc.status}: ${doc.error ?? ""}`,
      );
      setTitle("");
      setText("");
      setUrl("");
    } catch (err) {
      // Surface specific extract errors when ``ingest_attachment`` rejects.
      const detail = (
        err as {
          response?: { data?: { detail?: { code?: string; message?: string } } };
        }
      )?.response?.data?.detail;
      const code = detail?.code;
      const knownKey: Record<string, string> = {
        unsupported_kind: "ingest.errorUnsupportedKind",
        unsupported_mime: "ingest.errorUnsupportedMime",
        file_too_large: "ingest.errorFileTooLarge",
        pdf_lib_missing: "ingest.errorPdfLibMissing",
        pdf_parse_failed: "ingest.errorPdfParseFailed",
        pdf_empty: "ingest.errorPdfEmpty",
        docx_lib_missing: "ingest.errorDocxLibMissing",
        docx_parse_failed: "ingest.errorDocxParseFailed",
        docx_empty: "ingest.errorDocxEmpty",
        xlsx_lib_missing: "ingest.errorXlsxLibMissing",
        xlsx_parse_failed: "ingest.errorXlsxParseFailed",
        xlsx_empty: "ingest.errorXlsxEmpty",
        "attachment.blob_missing": "ingest.errorBlobMissing",
      };
      const key = code ? knownKey[code] : undefined;
      if (key) toast.error(t(key));
      else toast.error(detail?.message ?? t("ingestFailed"));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("ingest.title")}</CardTitle>
        <CardDescription>{t("ingest.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1.5">
            <Label>{t("ingest.source")}</Label>
            <Select
              value={sourceKind}
              onValueChange={(v) => setSourceKind(v as DocSourceKind)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="text">
                  <div className="flex items-center gap-1.5">
                    <IconFileText className="size-3.5" />
                    {t("ingest.sourceText")}
                  </div>
                </SelectItem>
                <SelectItem value="url">
                  <div className="flex items-center gap-1.5">
                    <IconWorld className="size-3.5" />
                    {t("ingest.sourceUrl")}
                  </div>
                </SelectItem>
                <SelectItem value="file">
                  <div className="flex items-center gap-1.5">
                    <IconFileUpload className="size-3.5" />
                    {t("ingest.sourceFile")}
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label>{t("ingest.docTitle")}</Label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("ingest.docTitlePlaceholder")}
            />
          </div>
        </div>

        {sourceKind === "url" && (
          <div className="grid gap-1.5">
            <Label>URL</Label>
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…"
              type="url"
            />
            <p className="text-[11px] sh-muted">{t("ingest.urlHint")}</p>
          </div>
        )}

        {sourceKind === "text" && (
          <div className="grid gap-1.5">
            <Label>{t("ingest.text")}</Label>
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={t("ingest.textPlaceholder")}
              className="min-h-[200px] font-mono text-[13px]"
            />
          </div>
        )}

        {sourceKind === "file" && (
          <div className="grid gap-1.5">
            <Label htmlFor="kb-file">{t("ingest.fileLabel")}</Label>
            <Input
              id="kb-file"
              type="file"
              accept=".pdf,.txt,.md,.csv,.json,.yaml,.yml,.xml,.html,.htm,.py,.js,.ts,.tsx,.css,.docx,.xlsx,.pptx,.toml,.sql,.sh"
              onChange={(e) => onFilePick(e.target.files?.[0] ?? null)}
              data-testid="kb-file-input"
            />
            <p className="text-[11px] sh-muted">{t("ingest.fileHint")}</p>
            {file && (
              <p className="text-[11px] sh-muted">
                <span className="font-mono">{file.name}</span>
                <span className="ml-2">({Math.round(file.size / 1024)} KB)</span>
              </p>
            )}
          </div>
        )}

        <div className="flex justify-end">
          <Button onClick={submit} disabled={submitting}>
            {submitting && <IconLoader2 className="size-4 animate-spin" />}
            {t("ingest.submit")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function SearchTab({ collection }: { collection: KnowledgeCollectionCard }) {
  const t = useTranslations("knowledge");
  const search = useSearchCollection();
  const [q, setQ] = useState("");
  const [topK, setTopK] = useState(5);

  const onRun = async () => {
    if (!q.trim()) return;
    try {
      await search.mutateAsync({
        collectionId: collection.id,
        query: q.trim(),
        top_k: topK,
      });
    } catch {
      toast.error(t("search.failed"));
    }
  };

  const hits = search.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("search.title")}</CardTitle>
        <CardDescription>{t("search.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("search.placeholder")}
              onKeyDown={(e) => {
                if (e.key === "Enter") onRun();
              }}
              className="pl-7"
            />
          </div>
          <Input
            type="number"
            min={1}
            max={10}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value) || 5)}
            className="w-[80px]"
          />
          <Button onClick={onRun} disabled={search.isPending || !q.trim()}>
            {search.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconSearch className="size-4" />
            )}
            {t("search.go")}
          </Button>
        </div>

        {hits.length === 0 && search.isSuccess && (
          <p className="py-4 text-center text-xs sh-muted">{t("search.empty")}</p>
        )}

        <ol className="flex flex-col gap-2">
          {hits.map((h, i) => (
            <li
              key={h.id}
              className="rounded-md border p-3 text-[13px]"
            >
              <div className="mb-1 flex items-center gap-2 text-[11px] sh-muted">
                <span className="font-mono">#{i + 1}</span>
                <span className="truncate font-medium">
                  {h.doc_title ?? "—"}
                </span>
                <span className="font-mono">ord={h.ord}</span>
                <span className="ml-auto rounded bg-green-500/10 px-1.5 py-0.5 font-mono tabular-nums text-green-700 dark:text-green-400">
                  {h.score.toFixed(3)}
                </span>
              </div>
              <p className="whitespace-pre-wrap break-words">{h.text}</p>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
