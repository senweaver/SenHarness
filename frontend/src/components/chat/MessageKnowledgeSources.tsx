"use client";

/**
 * Compact "Cited from N knowledge sources" disclosure rendered under an
 * assistant message that was grounded against the workspace knowledge
 * library.
 *
 * Mirrors the Perplexity / Claude / Notion AI pattern — the rich
 * in-flow ``ToolCallCard`` already shows the retrieval step inline; this
 * block is the auditable summary of "which chunks actually back this
 * answer", aggregated across every ``knowledge_search`` invocation in
 * the same turn and de-duplicated by (doc, chunk_ord).
 */

import { IconFileText } from "@tabler/icons-react";
import { useState } from "react";
import { useTranslations } from "next-intl";

import {
  Sources,
  SourcesContent,
  SourcesTrigger,
} from "@/components/ai-elements/sources";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export interface KnowledgeHit {
  doc_title: string;
  ord: number | null;
  text: string;
  score: number;
  collection_name: string | null;
}

interface Props {
  hits: KnowledgeHit[];
  className?: string;
}

/** Collect KB hits from one assistant message's tool parts.
 *
 *  Walks every ``tool-knowledge_search`` part on the message, pulls the
 *  ``hits`` array out of its output, and de-duplicates by
 *  ``doc_title + chunk_ord`` — the same chunk surfaced by two parallel
 *  searches should render once. Stable order: first occurrence wins. */
export function collectKnowledgeHits(
  parts: ReadonlyArray<{
    type?: string;
    output?: unknown;
  }>,
): KnowledgeHit[] {
  const seen = new Set<string>();
  const out: KnowledgeHit[] = [];
  for (const part of parts) {
    if (part?.type !== "tool-knowledge_search") continue;
    const output = part.output;
    if (!output || typeof output !== "object") continue;
    const r = output as Record<string, unknown>;
    if (r.ok === false) continue;
    const collection =
      typeof r.collection_name === "string" ? r.collection_name : null;
    const rawHits = Array.isArray(r.hits) ? r.hits : [];
    for (const raw of rawHits) {
      if (!raw || typeof raw !== "object") continue;
      const hit = raw as Record<string, unknown>;
      const doc_title =
        typeof hit.doc_title === "string" ? hit.doc_title : "(untitled)";
      const ord = typeof hit.ord === "number" ? hit.ord : null;
      const text = typeof hit.text === "string" ? hit.text : "";
      const score = typeof hit.score === "number" ? hit.score : 0;
      const key = `${doc_title}::${ord ?? ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        doc_title,
        ord,
        text,
        score,
        collection_name: collection,
      });
    }
  }
  return out;
}

export function MessageKnowledgeSources({ hits, className }: Props) {
  const t = useTranslations("chat.compose");
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  if (hits.length === 0) return null;

  return (
    <Sources className={cn("mt-1 w-full min-w-0", className)}>
      <SourcesTrigger
        count={hits.length}
        label={t("sourcesUsed", { count: hits.length })}
      />
      <SourcesContent className="min-w-0">
        <ol className="m-0 flex min-w-0 flex-col gap-1 p-0">
          {hits.map((hit, idx) => {
            const open = expandedIdx === idx;
            const scoreColor =
              hit.score >= 0.7
                ? "text-green-600 dark:text-green-400"
                : hit.score >= 0.4
                  ? "text-amber-600 dark:text-amber-400"
                  : "sh-muted";
            return (
              <li key={`${hit.doc_title}-${hit.ord ?? idx}`} className="min-w-0 list-none">
                <button
                  type="button"
                  onClick={() => setExpandedIdx(open ? null : idx)}
                  className={cn(
                    "flex w-full min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-left transition-colors",
                    "hover:bg-black/5 dark:hover:bg-white/5",
                    open && "bg-black/[0.04] dark:bg-white/[0.04]",
                  )}
                  aria-expanded={open}
                >
                  <Badge variant="outline" className="shrink-0 font-mono">
                    {idx + 1}
                  </Badge>
                  <IconFileText className="size-3.5 shrink-0 sh-muted" />
                  <span className="min-w-0 flex-1 truncate text-[12px] font-medium">
                    {hit.doc_title}
                  </span>
                  {hit.ord != null ? (
                    <span className="shrink-0 text-[10px] sh-muted">
                      {t("sourcesChunk", { ord: hit.ord })}
                    </span>
                  ) : null}
                  <span
                    className={cn(
                      "shrink-0 font-mono text-[10px] tabular-nums",
                      scoreColor,
                    )}
                    title={t("sourcesScore")}
                  >
                    {hit.score.toFixed(2)}
                  </span>
                </button>
                {open && hit.text ? (
                  <div className="mx-1.5 mt-1 min-w-0 rounded-md border border-[rgb(var(--color-primary))]/20 bg-[rgb(var(--color-primary))]/5 px-2 py-1.5">
                    {hit.collection_name ? (
                      <div className="mb-1 text-[10px] sh-muted">
                        {hit.collection_name}
                      </div>
                    ) : null}
                    <p className="whitespace-pre-wrap break-words text-[11px] leading-relaxed">
                      {hit.text}
                    </p>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      </SourcesContent>
    </Sources>
  );
}
