"use client";

import { useEffect, useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  IconCircleCheckFilled,
  IconCircleX,
  IconGripVertical,
  IconHelp,
  IconLoader2,
  IconPencil,
  IconPlus,
  IconSearch,
  IconTrash,
} from "@tabler/icons-react";
import {
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useCreateProvider,
  useDeleteProvider,
  catalogKindForProvider,
  useProviderCatalog,
  useProviderModels,
  useProviders,
  useReorderProviders,
  useTestProvider,
  useUpdateProvider,
  type ProviderCatalogEntry,
  type ProviderCatalogModelStub,
  type ProviderRead,
} from "@/hooks/use-providers";
import { ProviderAvatar } from "@/components/providers/ProviderAvatar";
import { CredentialField } from "@/components/providers/CredentialField";
import { ModelManagementSection } from "@/components/providers/ModelManagementSection";
import { getCategory } from "@/components/providers/_modelMeta";
import { labelOf, descOf } from "@/components/providers/_localize";
import { BuiltinModelsPreview } from "@/components/providers/BuiltinModelsPreview";
import { AddCustomProviderDialog } from "@/components/providers/AddCustomProviderDialog";
import { ServedAliasesCard } from "@/components/providers/ServedAliasesCard";
import { cn } from "@/lib/utils";

function isCustomProvider(provider: ProviderRead | null): boolean {
  if (!provider) return false;
  if (provider.kind !== "custom") return false;
  const source = provider.metadata_json?.source;
  return typeof source === "string" ? source === "custom" : true;
}

export default function ProvidersSettingsPage() {
  const t = useTranslations("settings.providers");
  const locale = useLocale();
  const { data: catalog = [], isLoading: catalogLoading } =
    useProviderCatalog();
  const { data: providers = [], isLoading: providersLoading } = useProviders();

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [addOpen, setAddOpen] = useState(false);

  // ─── Index providers by kind (built-ins) and stash custom rows separately ──
  const customProviders = useMemo(
    () => providers.filter((p) => isCustomProvider(p)),
    [providers],
  );
  const providerByKind = useMemo(() => {
    const map = new Map<string, ProviderRead>();
    for (const p of providers) {
      if (!isCustomProvider(p)) {
        const canonical = catalogKindForProvider(p.kind, catalog);
        if (!map.has(canonical)) {
          map.set(canonical, p);
        }
      }
    }
    return map;
  }, [providers, catalog]);

  // ─── Synthesise list rows: built-in catalog rows + per-instance customs ───
  type Row = {
    key: string; // unique selector key — `kind:<kind>` or `custom:<id>`
    label: string;
    entry: ProviderCatalogEntry;
    provider: ProviderRead | null;
    isCustom: boolean;
  };

  const allRows = useMemo<Row[]>(() => {
    const customCatalog = catalog.find((e) => e.kind === "custom");
    const customRows: Row[] = customCatalog
      ? customProviders.map((cp) => ({
          key: `custom:${cp.id}`,
          label: cp.name || labelOf(customCatalog, locale),
          entry: customCatalog,
          provider: cp,
          isCustom: true,
        }))
      : [];
    const builtinRows: Row[] = catalog
      .filter((e) => e.kind !== "custom")
      .map((e) => {
        const p = providerByKind.get(e.kind) ?? null;
        return {
          key: `kind:${e.kind}`,
          label: labelOf(e, locale),
          entry: e,
          provider: p,
          isCustom: false,
        };
      });
    // Configured rows (custom + builtin with a workspace row) live in
    // one bucket sorted by the admin-defined ``sort_order`` (ties
    // broken on ``created_at`` so newer rows surface predictably).
    // Unconfigured catalog entries stay in catalog declaration order
    // — drag-to-reorder applies only to actually-wired providers.
    const configured: Row[] = [...customRows];
    const unconfigured: Row[] = [];
    for (const row of builtinRows) {
      if (row.provider) configured.push(row);
      else unconfigured.push(row);
    }
    configured.sort((a, b) => {
      const ao = a.provider?.sort_order ?? 0;
      const bo = b.provider?.sort_order ?? 0;
      if (ao !== bo) return ao - bo;
      return (a.provider?.created_at ?? "").localeCompare(
        b.provider?.created_at ?? "",
      );
    });
    return [...configured, ...unconfigured];
  }, [catalog, providerByKind, customProviders, locale]);

  const configuredKeys = useMemo(
    () => allRows.filter((r) => r.provider !== null).map((r) => r.key),
    [allRows],
  );

  const visibleRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allRows;
    return allRows.filter((r) =>
      `${r.entry.kind} ${r.label} ${r.entry.display_name} ${r.entry.display_name_zh}`
        .toLowerCase()
        .includes(q),
    );
  }, [allRows, query]);

  const reorder = useReorderProviders();
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  // Drag-and-drop is only wired when no search filter is active —
  // partial visibility makes index-based reordering ambiguous and the
  // search box is the primary discovery affordance, not a reorder
  // surface. Configured rows are the only sortable bucket because the
  // resolver only consults persisted ``sort_order`` values.
  const dndEnabled = query.trim() === "" && configuredKeys.length > 1;

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIdx = configuredKeys.indexOf(String(active.id));
    const newIdx = configuredKeys.indexOf(String(over.id));
    if (oldIdx < 0 || newIdx < 0) return;
    const nextKeys = arrayMove(configuredKeys, oldIdx, newIdx);
    const idByKey = new Map<string, string>();
    for (const row of allRows) {
      if (row.provider) idByKey.set(row.key, row.provider.id);
    }
    const orderedIds = nextKeys
      .map((k) => idByKey.get(k))
      .filter((id): id is string => Boolean(id));
    reorder.mutate(
      { ordered_ids: orderedIds },
      {
        onError: () => toast.error(t("reorderFailed")),
      },
    );
  }

  useEffect(() => {
    if (selectedKey && visibleRows.find((r) => r.key === selectedKey)) {
      return;
    }
    setSelectedKey(visibleRows[0]?.key ?? null);
  }, [visibleRows, selectedKey]);

  const selectedRow =
    visibleRows.find((r) => r.key === selectedKey) ?? null;

  if (catalogLoading || providersLoading) {
    return (
      <div className="space-y-3 p-6">
        <Skeleton className="h-8" />
        <Skeleton className="h-72" />
      </div>
    );
  }

  return (
    <div className="-m-6 flex h-[calc(100vh-65px)] overflow-hidden">
      <aside className="flex w-[260px] shrink-0 flex-col border-r bg-card/30">
        <div className="flex items-center gap-1.5 p-2.5 border-b">
          <div className="relative flex-1">
            <IconSearch className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="pl-7 h-8 text-sm"
            />
          </div>
          <SimpleTooltip label={t("addCustom.title")}>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0"
              onClick={() => setAddOpen(true)}
            >
              <IconPlus className="size-4" />
            </Button>
          </SimpleTooltip>
        </div>

        <ul className="flex-1 overflow-y-auto px-1.5 py-2 space-y-0.5">
          {visibleRows.length === 0 ? (
            <li className="px-2 py-3 text-xs text-muted-foreground">
              {t("empty")}
            </li>
          ) : dndEnabled ? (
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={configuredKeys}
                strategy={verticalListSortingStrategy}
              >
                {visibleRows.map((row) =>
                  row.provider ? (
                    <SortableSidebarRow
                      key={row.key}
                      row={row}
                      selected={selectedKey === row.key}
                      onSelect={() => setSelectedKey(row.key)}
                    />
                  ) : (
                    <SidebarRow
                      key={row.key}
                      row={row}
                      selected={selectedKey === row.key}
                      onSelect={() => setSelectedKey(row.key)}
                    />
                  ),
                )}
              </SortableContext>
            </DndContext>
          ) : (
            visibleRows.map((row) => (
              <SidebarRow
                key={row.key}
                row={row}
                selected={selectedKey === row.key}
                onSelect={() => setSelectedKey(row.key)}
              />
            ))
          )}
        </ul>
      </aside>

      <section className="flex-1 overflow-y-auto">
        <div className="flex min-h-full flex-col">
          <div className="flex-1">
            {selectedRow ? (
              <ProviderDetail
                key={selectedRow.key}
                entry={selectedRow.entry}
                provider={selectedRow.provider}
                isCustom={selectedRow.isCustom}
                customLabel={selectedRow.label}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                {t("selectFromList")}
              </div>
            )}
          </div>
          <ServedAliasesCard />
        </div>
      </section>

      <AddCustomProviderDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onCreated={(created) => setSelectedKey(`custom:${created.id}`)}
      />
    </div>
  );
}

type SidebarRowProps = {
  row: {
    key: string;
    label: string;
    entry: ProviderCatalogEntry;
    provider: ProviderRead | null;
  };
  selected: boolean;
  onSelect: () => void;
};

function SidebarRow({ row, selected, onSelect }: SidebarRowProps) {
  const p = row.provider;
  const isOn = p?.enabled === true && p.has_key;
  const isPartial = p?.enabled === true && !p.has_key;
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        className={cn(
          "w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-left transition",
          selected
            ? "bg-black/5 font-medium text-foreground dark:bg-white/10"
            : "text-muted-foreground hover:bg-black/5 hover:text-foreground dark:hover:bg-white/10",
        )}
      >
        <ProviderAvatar
          displayName={row.label}
          family={row.entry.family}
          size="sm"
        />
        <span className="flex-1 truncate">{row.label}</span>
        {isOn ? (
          <span
            className="size-2 rounded-full bg-emerald-500"
            aria-label="enabled"
          />
        ) : isPartial ? (
          <span
            className="size-2 rounded-full bg-amber-500"
            aria-label="missing key"
          />
        ) : null}
      </button>
    </li>
  );
}

function SortableSidebarRow({ row, selected, onSelect }: SidebarRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: row.key });
  const p = row.provider;
  const isOn = p?.enabled === true && p.has_key;
  const isPartial = p?.enabled === true && !p.has_key;
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };
  return (
    <li ref={setNodeRef} style={style}>
      <div
        className={cn(
          "group/row flex items-center gap-1.5 rounded-md px-1 py-1.5 transition",
          selected
            ? "bg-black/5 dark:bg-white/10"
            : "hover:bg-black/5 dark:hover:bg-white/10",
        )}
      >
        <button
          type="button"
          {...attributes}
          {...listeners}
          aria-label="drag to reorder"
          className="cursor-grab opacity-30 transition group-hover/row:opacity-100"
        >
          <IconGripVertical className="size-3.5 text-muted-foreground" />
        </button>
        <button
          type="button"
          onClick={onSelect}
          aria-current={selected ? "true" : undefined}
          className={cn(
            "flex-1 flex items-center gap-2 rounded-sm px-1 py-0.5 text-sm text-left",
            selected
              ? "font-medium text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <ProviderAvatar
            displayName={row.label}
            family={row.entry.family}
            size="sm"
          />
          <span className="flex-1 truncate">{row.label}</span>
          {isOn ? (
            <span
              className="size-2 rounded-full bg-emerald-500"
              aria-label="enabled"
            />
          ) : isPartial ? (
            <span
              className="size-2 rounded-full bg-amber-500"
              aria-label="missing key"
            />
          ) : null}
        </button>
      </div>
    </li>
  );
}

function ProviderDetail({
  entry,
  provider,
  isCustom,
  customLabel,
}: {
  entry: ProviderCatalogEntry;
  provider: ProviderRead | null;
  isCustom: boolean;
  customLabel: string;
}) {
  const t = useTranslations("settings.providers");
  const tSettings = useTranslations("settings");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const create = useCreateProvider();
  const update = useUpdateProvider(provider?.id ?? "");
  const remove = useDeleteProvider();
  const test = useTestProvider(provider?.id ?? "");
  const { data: models = [] } = useProviderModels(provider?.id);

  const display = isCustom ? customLabel : labelOf(entry, locale);

  const embeddingCatalogOptions = useMemo<ProviderCatalogModelStub[]>(
    () => entry.builtin_models.filter((m) => getCategory(m) === "embedding"),
    [entry.builtin_models],
  );
  const supportsEmbeddings =
    embeddingCatalogOptions.length > 0 || entry.family === "openai-compatible";

  const initialEmbeddingModel =
    typeof provider?.metadata_json?.embedding_model === "string"
      ? (provider.metadata_json.embedding_model as string)
      : "";

  const [baseUrl, setBaseUrl] = useState(
    provider?.base_url ?? entry.default_base_url ?? "",
  );
  const [apiKey, setApiKey] = useState("");
  const [customHeaders, setCustomHeaders] = useState(
    JSON.stringify(provider?.metadata_json?.custom_headers ?? {}, null, 2),
  );
  const [enabled, setEnabled] = useState(provider?.enabled ?? false);
  const [embeddingModel, setEmbeddingModel] = useState(initialEmbeddingModel);
  const [dirty, setDirty] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState(provider?.name ?? display);

  useEffect(() => {
    setBaseUrl(provider?.base_url ?? entry.default_base_url ?? "");
    setApiKey("");
    setCustomHeaders(
      JSON.stringify(provider?.metadata_json?.custom_headers ?? {}, null, 2),
    );
    setEnabled(provider?.enabled ?? false);
    setEmbeddingModel(
      typeof provider?.metadata_json?.embedding_model === "string"
        ? (provider.metadata_json.embedding_model as string)
        : "",
    );
    setDirty(false);
    setRenameValue(provider?.name ?? display);
  }, [
    entry.kind,
    entry.default_base_url,
    display,
    provider?.id,
    provider?.name,
    provider?.base_url,
    provider?.enabled,
    provider?.metadata_json,
  ]);

  function metadataPatch(): Record<string, unknown> | undefined {
    const current = provider?.metadata_json ?? {};
    const currentEmb =
      typeof current.embedding_model === "string"
        ? current.embedding_model
        : "";
    if (currentEmb === embeddingModel) return undefined;
    const next: Record<string, unknown> = { ...current };
    if (embeddingModel) next.embedding_model = embeddingModel;
    else delete next.embedding_model;
    return next;
  }

  async function ensureProviderRow(opts: { enabled: boolean }) {
    if (provider) {
      return provider.id;
    }
    if (!apiKey) {
      toast.error(t("requireKeyToEnable"));
      return null;
    }
    const created = await create.mutateAsync({
      kind: entry.kind,
      name: display,
      base_url: baseUrl || null,
      enabled: opts.enabled,
      credential_type: entry.credential_type,
      country_code: entry.country_code,
      api_key: apiKey,
      metadata_json: embeddingModel
        ? { embedding_model: embeddingModel }
        : undefined,
    });
    return created.id;
  }

  async function save() {
    try {
      if (!provider) {
        const id = await ensureProviderRow({ enabled });
        if (id) {
          toast.success(tSettings("created"));
          setApiKey("");
          setDirty(false);
        }
        return;
      }
      const patch = metadataPatch();
      await update.mutateAsync({
        base_url: baseUrl || null,
        enabled,
        api_key: apiKey || null,
        ...(patch !== undefined ? { metadata_json: patch } : {}),
      });
      toast.success(tSettings("saved"));
      setDirty(false);
      setApiKey("");
    } catch {
      toast.error(tSettings(provider ? "saveFailed" : "createFailed"));
    }
  }

  async function toggleEnabled(v: boolean) {
    setEnabled(v);
    try {
      if (!provider) {
        if (!apiKey) {
          setEnabled(false);
          toast.error(t("requireKeyToEnable"));
          return;
        }
        const id = await ensureProviderRow({ enabled: v });
        if (id) {
          toast.success(tSettings("created"));
          setApiKey("");
          setDirty(false);
        }
        return;
      }
      await update.mutateAsync({ enabled: v });
    } catch {
      setEnabled(!v);
      toast.error(tSettings("saveFailed"));
    }
  }

  async function runTest(model: string) {
    if (!provider) {
      toast.error(t("test.failed", { reason: t("requireSaveFirst") }));
      return;
    }
    try {
      const r = await test.mutateAsync({ model: model || undefined });
      if (r.ok) {
        toast.success(t("test.successWithLatency", { ms: r.latency_ms ?? 0 }));
      } else {
        const code = r.error ?? "unknown";
        const mapped = t.has(`models.errors.${code}`)
          ? t(`models.errors.${code}`)
          : code;
        const reason = r.detail
          ? `${mapped} — ${r.detail.slice(0, 160)}`
          : mapped;
        toast.error(t("test.failed", { reason }));
      }
    } catch {
      toast.error(t("test.networkError"));
    }
  }

  async function deleteSelf() {
    if (!provider) return;
    if (!confirm(tSettings("confirmDelete"))) return;
    try {
      await remove.mutateAsync(provider.id);
      toast.success(tSettings("deleted"));
    } catch {
      toast.error(tSettings("deleteFailed"));
    }
  }

  async function applyRename() {
    if (!provider) return;
    const v = renameValue.trim();
    if (!v || v === provider.name) {
      setRenameOpen(false);
      return;
    }
    try {
      await update.mutateAsync({ name: v });
      toast.success(tSettings("saved"));
      setRenameOpen(false);
    } catch {
      toast.error(tSettings("saveFailed"));
    }
  }

  const enabledChatModels = useMemo(
    () =>
      models
        .filter((m) => m.enabled)
        .map((m) => ({ id: m.model, label: m.label ?? m.model })),
    [models],
  );

  const credentialType = entry.credential_type;
  const canDelete = isCustom && Boolean(provider);
  const canRename = isCustom && Boolean(provider);
  const description = descOf(entry, locale);

  return (
    <div className="mx-auto max-w-2xl px-6 py-5 space-y-5">
      <header className="flex items-center gap-3">
        <ProviderAvatar
          displayName={display}
          family={entry.family}
          size="lg"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-semibold truncate">{display}</h2>
            {entry.signup_url ? (
              <SimpleTooltip label={t("getKey")}>
                <a
                  href={entry.signup_url}
                  target="_blank"
                  rel="noopener"
                  className="text-muted-foreground hover:text-foreground"
                >
                  <IconHelp className="size-4" />
                </a>
              </SimpleTooltip>
            ) : null}
          </div>
          {description ? (
            <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
              {description}
            </p>
          ) : null}
        </div>
        {canRename ? (
          <SimpleTooltip label={t("actions.rename")}>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                setRenameValue(provider?.name ?? display);
                setRenameOpen(true);
              }}
              className="text-muted-foreground hover:text-foreground"
            >
              <IconPencil className="size-4" />
            </Button>
          </SimpleTooltip>
        ) : null}
        {canDelete ? (
          <SimpleTooltip label={tCommon("delete")}>
            <Button
              variant="ghost"
              size="icon"
              onClick={deleteSelf}
              disabled={remove.isPending}
              className="text-muted-foreground hover:text-destructive"
            >
              <IconTrash className="size-4" />
            </Button>
          </SimpleTooltip>
        ) : null}
        <Switch
          checked={enabled}
          onCheckedChange={toggleEnabled}
          aria-label={t("fields.enabled")}
        />
      </header>

      <div className="space-y-3.5">
        <CredentialField
          kind={entry.kind}
          type={credentialType}
          hasKey={provider?.has_key ?? false}
          keyHint={provider?.api_key_hint ?? null}
          value={apiKey}
          onChange={(v) => {
            setApiKey(v);
            setDirty(true);
          }}
          customHeaders={customHeaders}
          onCustomHeadersChange={(v) => {
            setCustomHeaders(v);
            setDirty(true);
          }}
          trailingAction={
            <ConnectivityCheckPopover
              models={enabledChatModels}
              disabled={!provider || !provider.has_key}
              pending={test.isPending}
              onPick={runTest}
            />
          }
        />

        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Label className="text-sm">{t("fields.baseUrl")}</Label>
            <SimpleTooltip
              label={t("credentials.baseUrlHint")}
              contentClassName="max-w-xs leading-relaxed text-[11px]"
            >
              <button
                type="button"
                tabIndex={-1}
                className="text-muted-foreground hover:text-foreground"
              >
                <IconHelp className="size-3.5" />
              </button>
            </SimpleTooltip>
          </div>
          <Input
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value);
              setDirty(true);
            }}
            placeholder={entry.default_base_url ?? "https://..."}
            spellCheck={false}
            autoComplete="off"
          />
        </div>

        {supportsEmbeddings ? (
          <EmbeddingModelField
            options={embeddingCatalogOptions}
            value={embeddingModel}
            onChange={(v) => {
              setEmbeddingModel(v);
              setDirty(true);
            }}
          />
        ) : null}

        <div className="flex justify-end">
          <Button
            disabled={
              (!dirty && provider !== null) ||
              create.isPending ||
              update.isPending ||
              (!provider && !apiKey)
            }
            onClick={save}
          >
            {create.isPending || update.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : null}
            {tCommon("save")}
          </Button>
        </div>
      </div>

      {provider ? (
        <>
          <Separator />
          <ModelManagementSection
            provider={provider}
            catalogEntry={entry}
          />
        </>
      ) : (
        <>
          <Separator />
          <BuiltinModelsPreview entry={entry} />
        </>
      )}

      <RenameDialog
        open={renameOpen}
        value={renameValue}
        onValueChange={setRenameValue}
        onCancel={() => setRenameOpen(false)}
        onSubmit={applyRename}
        pending={update.isPending}
      />
    </div>
  );
}

function EmbeddingModelField({
  options,
  value,
  onChange,
}: {
  options: ProviderCatalogModelStub[];
  value: string;
  onChange: (next: string) => void;
}) {
  const t = useTranslations("settings.providers");
  const defaultModel = options.find((m) => m.recommended)?.model ?? options[0]?.model;
  const placeholder = defaultModel
    ? t("fields.embeddingModelPlaceholderWithDefault", { model: defaultModel })
    : t("fields.embeddingModelPlaceholder");
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <Label className="text-sm">{t("fields.embeddingModel")}</Label>
        <SimpleTooltip
          label={t("credentials.embeddingModelHint")}
          contentClassName="max-w-xs leading-relaxed text-[11px]"
        >
          <button
            type="button"
            tabIndex={-1}
            className="text-muted-foreground hover:text-foreground"
          >
            <IconHelp className="size-3.5" />
          </button>
        </SimpleTooltip>
      </div>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
      />
      {options.length > 0 ? (
        <div className="flex flex-wrap gap-1 pt-1">
          {options.map((m) => (
            <button
              key={m.model}
              type="button"
              onClick={() => onChange(m.model)}
              className={cn(
                "rounded-md border px-2 py-0.5 font-mono text-[11px] transition",
                value === m.model
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-transparent bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {m.model}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ConnectivityCheckPopover({
  models,
  disabled,
  pending,
  onPick,
}: {
  models: { id: string; label: string }[];
  disabled: boolean;
  pending: boolean;
  onPick: (model: string) => void;
}) {
  const t = useTranslations("settings.providers.connectivity");
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <SimpleTooltip label={t("run")}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={disabled}
            aria-label={t("run")}
            className="size-7"
          >
            {pending ? (
              <IconLoader2 className="size-3.5 animate-spin" />
            ) : (
              <IconCircleCheckFilled className="size-3.5" />
            )}
          </Button>
        </SimpleTooltip>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-2">
        <div className="px-2 pt-1 pb-1.5 text-xs font-medium">
          {t("popoverTitle")}
        </div>
        {models.length === 0 ? (
          <div className="flex items-center gap-1.5 px-2 py-3 text-xs text-muted-foreground">
            <IconCircleX className="size-3.5" />
            {t("empty")}
          </div>
        ) : (
          <ul className="max-h-64 overflow-y-auto">
            {models.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    onPick(m.id);
                  }}
                  className="w-full rounded-sm px-2 py-1.5 text-left text-sm hover:bg-muted/60"
                >
                  <span className="block truncate">{m.label}</span>
                  <span className="block truncate text-[10px] text-muted-foreground font-mono">
                    {m.id}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}

function RenameDialog({
  open,
  value,
  onValueChange,
  onCancel,
  onSubmit,
  pending,
}: {
  open: boolean;
  value: string;
  onValueChange: (v: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
  pending: boolean;
}) {
  const t = useTranslations("settings.providers.actions");
  const tCommon = useTranslations("common");
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
    >
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("renameTitle")}</DialogTitle>
        </DialogHeader>
        <Input
          autoFocus
          value={value}
          onChange={(e) => onValueChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onSubmit();
            }
          }}
        />
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={onSubmit} disabled={pending}>
            {pending ? <IconLoader2 className="size-4 animate-spin" /> : null}
            {tCommon("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
