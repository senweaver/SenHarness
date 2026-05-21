"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  IconCheck,
  IconGripVertical,
  IconLoader2,
  IconPlus,
  IconRefresh,
  IconStarFilled,
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAddProviderModel,
  useApplyDiscoveredModels,
  useDeleteProviderModel,
  useDiscoverModels,
  useProviderModels,
  useReorderProviderModels,
  useUpdateProviderModel,
  type DiscoveredModel,
  type ModelCategory,
  type ProviderCatalogEntry,
  type ProviderRead,
} from "@/hooks/use-providers";
import {
  CategoryTabs,
  ModelCapabilityIcons,
  ModelToggleRow,
  type ModelLike,
  getCategory,
  inferCapabilities,
} from "@/components/providers/_modelMeta";
import { EditProviderModelDialog } from "@/components/providers/EditProviderModelDialog";

interface Props {
  provider: ProviderRead;
  catalogEntry: ProviderCatalogEntry | undefined;
}

type UnifiedRow = {
  model: string;
  label?: string | null;
  family?: string | null;
  category?: ModelCategory;
  capabilities: string[];
  contextWindow?: number | null;
  recommended: boolean;
  enabled: boolean;
  inDb: boolean;
  dbId?: string;
  sortOrder: number;
  source?: string;
};

export function ModelManagementSection({ provider, catalogEntry }: Props) {
  const t = useTranslations("settings.providers.models");
  const { data: rows = [], isLoading } = useProviderModels(provider.id);
  const update = useUpdateProviderModel(provider.id);
  const apply = useApplyDiscoveredModels(provider.id);
  const add = useAddProviderModel(provider.id);
  const remove = useDeleteProviderModel(provider.id);
  const reorder = useReorderProviderModels(provider.id);
  const discover = useDiscoverModels();

  const [discoverResult, setDiscoverResult] = useState<{
    source: "remote" | "static";
    rows: DiscoveredModel[];
  } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [manualName, setManualName] = useState("");
  const [tab, setTab] = useState<"all" | ModelCategory>("all");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<UnifiedRow | null>(null);

  useEffect(() => {
    setDiscoverResult(null);
    setSelected(new Set());
    setManualName("");
    setTab("all");
    setQuery("");
    setEditing(null);
  }, [provider.id]);

  const builtinIndex = useMemo(() => {
    const map = new Map<
      string,
      { recommended: boolean; family?: string; category?: ModelCategory }
    >();
    for (const m of catalogEntry?.builtin_models ?? []) {
      map.set(m.model, {
        recommended: m.recommended,
        family: m.family,
        category: m.category,
      });
    }
    return map;
  }, [catalogEntry]);

  const matchesTab = (m: ModelLike) =>
    tab === "all" ? true : getCategory(m) === tab;

  const matchesQuery = (model: string, label?: string | null) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (
      model.toLowerCase().includes(q) ||
      (label ?? "").toLowerCase().includes(q)
    );
  };

  const unifiedRows = useMemo<UnifiedRow[]>(() => {
    const byModel = new Map<string, UnifiedRow>();
    for (const m of catalogEntry?.builtin_models ?? []) {
      byModel.set(m.model, {
        model: m.model,
        label: m.name,
        family: m.family ?? null,
        category: m.category,
        capabilities: m.capabilities ?? [],
        contextWindow: m.context_window ?? null,
        recommended: m.recommended,
        enabled: false,
        inDb: false,
        sortOrder: Number.MAX_SAFE_INTEGER,
      });
    }
    for (const r of rows) {
      const meta = byModel.get(r.model);
      const dbCaps = Array.isArray(r.metadata_json?.capabilities)
        ? (r.metadata_json.capabilities as string[])
        : [];
      const caps =
        dbCaps.length > 0 ? dbCaps : (meta?.capabilities ?? []);
      byModel.set(r.model, {
        model: r.model,
        label: r.label ?? meta?.label ?? null,
        family: r.family ?? meta?.family ?? null,
        category: meta?.category ?? getCategory(r),
        capabilities: caps,
        contextWindow: r.context_window ?? meta?.contextWindow ?? null,
        recommended: r.recommended || (meta?.recommended ?? false),
        enabled: r.enabled,
        inDb: true,
        dbId: r.id,
        sortOrder: r.sort_order ?? 0,
        source: r.source,
      });
    }
    return Array.from(byModel.values());
  }, [catalogEntry, rows]);

  const categoryCounts = useMemo(() => {
    const counts: Record<"all" | ModelCategory, number> = {
      all: 0,
      chat: 0,
      image: 0,
      video: 0,
      embedding: 0,
      asr: 0,
      tts: 0,
    };
    for (const r of unifiedRows) {
      const c = getCategory(r);
      counts[c]++;
      counts.all++;
    }
    return counts;
  }, [unifiedRows]);

  const visibleRows = useMemo<UnifiedRow[]>(() => {
    return unifiedRows
      .filter(matchesTab)
      .filter((r) => matchesQuery(r.model, r.label))
      .sort((a, b) => {
        // Already-persisted rows lead, in their stored order. Catalog-only
        // rows fall in after, recommended-first then alphabetical — same
        // landing position as today, just unified into one list so drag
        // works across the boundary.
        if (a.inDb !== b.inDb) return a.inDb ? -1 : 1;
        if (a.inDb) return a.sortOrder - b.sortOrder;
        if (a.recommended !== b.recommended) return a.recommended ? -1 : 1;
        return a.model.localeCompare(b.model);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unifiedRows, tab, query]);

  const newlyDiscovered = (discoverResult?.rows ?? [])
    .filter((m) => !m.in_db)
    .filter(matchesTab)
    .filter((m) => matchesQuery(m.model, m.label));

  const supportsDiscover = catalogEntry?.supports_discover ?? false;
  const totalEnabled = rows.filter((r) => r.enabled).length;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  async function runDiscover() {
    try {
      const r = await discover.mutateAsync(provider.id);
      if (r.error) {
        toast.error(t.has(`errors.${r.error}`) ? t(`errors.${r.error}`) : r.error);
        return;
      }
      setDiscoverResult({ source: r.source, rows: r.discovered });
      setSelected(
        new Set(
          r.discovered
            .filter((m) => !m.in_db && m.recommended)
            .map((m) => m.model),
        ),
      );
      toast.success(t("discoverSuccess", { count: r.discovered.length }));
    } catch {
      toast.error(t("discoverFailed"));
    }
  }

  async function applySelected() {
    if (selected.size === 0) {
      toast.error(t("selectAtLeastOne"));
      return;
    }
    try {
      await apply.mutateAsync({
        model_ids: Array.from(selected),
        replace: false,
      });
      toast.success(t("applySuccess", { count: selected.size }));
      setSelected(new Set());
      setDiscoverResult(null);
    } catch {
      toast.error(t("applyFailed"));
    }
  }

  async function addManual() {
    const name = manualName.trim();
    if (!name) return;
    try {
      await add.mutateAsync({ model: name, enabled: true });
      setManualName("");
      toast.success(t("manualAddSuccess"));
    } catch {
      toast.error(t("manualAddFailed"));
    }
  }

  async function toggleUnified(unified: UnifiedRow, value: boolean) {
    if (unified.inDb && unified.dbId) {
      try {
        await update.mutateAsync({
          modelId: unified.dbId,
          patch: { enabled: value },
        });
      } catch {
        toast.error(t("toggleFailed"));
      }
      return;
    }
    if (!value) return;
    try {
      await apply.mutateAsync({ model_ids: [unified.model], replace: false });
    } catch {
      toast.error(t("applyFailed"));
    }
  }

  async function deleteRow(unified: UnifiedRow) {
    if (!unified.dbId) return;
    if (unified.source !== "manual") {
      toast.error(t("cannotDeleteSystem"));
      return;
    }
    if (!confirm(t("confirmDelete", { model: unified.model }))) return;
    try {
      await remove.mutateAsync(unified.dbId);
      toast.success(t("deleteSuccess"));
    } catch {
      toast.error(t("deleteFailed"));
    }
  }

  async function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    // DnD ids are model names so catalog-only rows participate too — the
    // backend lazily upserts them with ``enabled=false`` on first sort.
    const ids = visibleRows.map((r) => r.model);
    const oldIdx = ids.indexOf(String(active.id));
    const newIdx = ids.indexOf(String(over.id));
    if (oldIdx === -1 || newIdx === -1) return;
    const next = arrayMove(ids, oldIdx, newIdx);
    try {
      await reorder.mutateAsync({ ordered_ids: next });
    } catch {
      toast.error(t("reorderFailed"));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold">{t("heading")}</h3>
        <span className="text-xs text-muted-foreground">
          {t("totalAvailable", { count: rows.length, enabled: totalEnabled })}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            className="h-8 w-44 text-xs"
          />
          {supportsDiscover ? (
            <Button
              variant="outline"
              size="sm"
              onClick={runDiscover}
              disabled={discover.isPending}
              className="h-8"
            >
              {discover.isPending ? (
                <IconLoader2 className="size-3.5 animate-spin" />
              ) : (
                <IconRefresh className="size-3.5" />
              )}
              {t("discover")}
            </Button>
          ) : null}
        </div>
      </div>

      <CategoryTabs active={tab} counts={categoryCounts} onChange={setTab} />

      {discoverResult && newlyDiscovered.length > 0 ? (
        <div className="rounded-md border bg-muted/40 p-3 space-y-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium">
              {t("discoveredHeading", {
                count: newlyDiscovered.length,
                source: discoverResult.source,
              })}
            </span>
            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  setSelected(
                    selected.size === newlyDiscovered.length
                      ? new Set()
                      : new Set(newlyDiscovered.map((m) => m.model)),
                  )
                }
              >
                {selected.size === newlyDiscovered.length
                  ? t("deselectAll")
                  : t("selectAll")}
              </Button>
              <Button
                size="sm"
                onClick={applySelected}
                disabled={selected.size === 0 || apply.isPending}
              >
                {apply.isPending ? (
                  <IconLoader2 className="size-3.5 animate-spin" />
                ) : (
                  <IconCheck className="size-3.5" />
                )}
                {t("applySelected", { count: selected.size })}
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 max-h-72 overflow-auto">
            {newlyDiscovered.map((m) => {
              const isOn = selected.has(m.model);
              const meta = builtinIndex.get(m.model);
              const caps = inferCapabilities(m);
              return (
                <label
                  key={m.model}
                  className="flex items-center gap-2 rounded px-2 py-1.5 cursor-pointer hover:bg-muted/60"
                >
                  <input
                    type="checkbox"
                    checked={isOn}
                    onChange={(e) => {
                      const next = new Set(selected);
                      if (e.target.checked) next.add(m.model);
                      else next.delete(m.model);
                      setSelected(next);
                    }}
                    className="size-3.5 rounded border accent-primary"
                  />
                  <span className="font-mono text-xs truncate flex-1">
                    {m.model}
                  </span>
                  {meta?.recommended || m.recommended ? (
                    <IconStarFilled className="size-3 text-amber-500 shrink-0" />
                  ) : null}
                  <ModelCapabilityIcons capabilities={caps} />
                </label>
              );
            })}
          </div>
        </div>
      ) : null}

      {isLoading ? (
        <Skeleton className="h-32" />
      ) : visibleRows.length === 0 ? (
        <p className="text-xs text-muted-foreground py-4 text-center">
          {t("enabledEmpty")}
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={visibleRows.map((r) => r.model)}
              strategy={verticalListSortingStrategy}
            >
              {visibleRows.map((u) => (
                <SortableModelRow
                  key={u.model}
                  row={u}
                  onToggle={(v) => toggleUnified(u, v)}
                  onEdit={u.inDb ? () => setEditing(u) : undefined}
                  onDelete={u.inDb ? () => deleteRow(u) : undefined}
                  pending={
                    update.isPending ||
                    apply.isPending ||
                    remove.isPending ||
                    reorder.isPending
                  }
                />
              ))}
            </SortableContext>
          </DndContext>
        </ul>
      )}

      <div className="space-y-2 pt-3 border-t mt-3">
        <Label className="text-sm">{t("manualHeading")}</Label>
        <p className="text-xs text-muted-foreground">{t("manualHint")}</p>
        <div className="flex gap-2">
          <Input
            value={manualName}
            placeholder={t("manualPlaceholder")}
            onChange={(e) => setManualName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") addManual();
            }}
          />
          <Button
            disabled={!manualName.trim() || add.isPending}
            onClick={addManual}
          >
            {add.isPending ? (
              <IconLoader2 className="size-3.5 animate-spin" />
            ) : (
              <IconPlus className="size-3.5" />
            )}
            {t("manualAdd")}
          </Button>
        </div>
      </div>

      {editing && editing.dbId ? (
        <EditProviderModelDialog
          open={true}
          onOpenChange={(o) => {
            if (!o) setEditing(null);
          }}
          providerId={provider.id}
          modelId={editing.dbId}
          modelKey={editing.model}
          initialLabel={editing.label ?? ""}
          initialContextWindow={editing.contextWindow ?? null}
          initialCapabilities={editing.capabilities}
        />
      ) : null}
    </div>
  );
}

function SortableModelRow({
  row,
  onToggle,
  onEdit,
  onDelete,
  pending,
}: {
  row: UnifiedRow;
  onToggle: (next: boolean) => void;
  onEdit?: () => void;
  onDelete?: () => void;
  pending: boolean;
}) {
  const t = useTranslations("settings.providers.models");
  const sortable = useSortable({ id: row.model });
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    sortable;

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };

  const handle = (
    <button
      type="button"
      {...attributes}
      {...listeners}
      className="cursor-grab text-muted-foreground hover:text-foreground touch-none"
      aria-label={t("dragHandle")}
    >
      <IconGripVertical className="size-3.5" />
    </button>
  );

  return (
    <div ref={setNodeRef} style={style}>
      <ModelToggleRow
        model={row.model}
        label={row.label}
        family={row.family}
        category={row.category}
        capabilities={row.capabilities}
        contextWindow={row.contextWindow}
        recommended={row.recommended}
        enabled={row.enabled}
        onToggle={onToggle}
        onEdit={onEdit}
        onDelete={onDelete}
        canDelete={row.source === "manual"}
        dragHandle={handle}
        pending={pending}
      />
    </div>
  );
}
