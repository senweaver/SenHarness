"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import {
  IconCheck,
  IconGauge,
  IconLoader2,
  IconPlayerPlay,
  IconRobot,
} from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgents } from "@/hooks/use-agents";
import {
  useCompareRuntimes,
  useRegisteredRuntimes,
  type RuntimeCompareCandidate,
  type RuntimeCompareResult,
} from "@/hooks/use-runtimes";

const VERDICT_BADGE: Record<
  "pass" | "warn" | "fail",
  "success" | "warning" | "danger"
> = {
  pass: "success",
  warn: "warning",
  fail: "danger",
};

export function CompareRuntimesCard() {
  const t = useTranslations("settings.runtimes.compare");
  const agents = useAgents();
  const runtimes = useRegisteredRuntimes();

  const [agentId, setAgentId] = useState<string>("");
  const [prompt, setPrompt] = useState<string>(
    t("defaultPrompt"),
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [includeEval, setIncludeEval] = useState(true);
  const [result, setResult] = useState<RuntimeCompareResult | null>(null);

  const compare = useCompareRuntimes(agentId);

  const runtimeKinds = useMemo(
    () => (runtimes.data ?? []).map((r) => r.kind),
    [runtimes.data],
  );

  const toggle = (kind: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else if (next.size < 4) next.add(kind);
      return next;
    });
  };

  const canRun =
    !!agentId && prompt.trim().length > 0 && selected.size >= 1;

  const submit = async () => {
    if (!canRun) return;
    setResult(null);
    const res = await compare.mutateAsync({
      prompt: prompt.trim(),
      runtimes: [...selected],
      include_eval: includeEval,
    });
    setResult(res);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <IconGauge className="size-4 text-[rgb(var(--color-primary))]" />
          {t("title")}
        </CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1.5">
            <Label htmlFor="cmp-agent">{t("agent")}</Label>
            <Select value={agentId} onValueChange={setAgentId}>
              <SelectTrigger id="cmp-agent">
                <SelectValue placeholder={t("agentPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {(agents.data ?? []).map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-end justify-between gap-2">
            <div className="grid gap-1.5">
              <Label>{t("runtimes")}</Label>
              <div className="flex flex-wrap gap-1.5">
                {runtimes.isLoading && <Skeleton className="h-7 w-40" />}
                {runtimeKinds.map((k) => {
                  const active = selected.has(k);
                  return (
                    <button
                      key={k}
                      type="button"
                      onClick={() => toggle(k)}
                      className={
                        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition " +
                        (active
                          ? "border-[rgb(var(--color-primary))] text-[rgb(var(--color-primary))] bg-black/5 dark:bg-white/5"
                          : "sh-muted hover:bg-black/5 dark:hover:bg-white/5")
                      }
                    >
                      {active && <IconCheck className="size-3" />}
                      {k}
                    </button>
                  );
                })}
              </div>
              <p className="text-[11px] sh-muted">{t("maxHint")}</p>
            </div>
            <div className="flex items-center gap-2">
              <Label htmlFor="cmp-eval" className="text-xs">
                {t("includeEval")}
              </Label>
              <Switch
                id="cmp-eval"
                checked={includeEval}
                onCheckedChange={setIncludeEval}
              />
            </div>
          </div>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="cmp-prompt">{t("prompt")}</Label>
          <Textarea
            id="cmp-prompt"
            rows={3}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t("promptPlaceholder")}
          />
        </div>

        <div className="flex justify-end">
          <Button onClick={submit} disabled={!canRun || compare.isPending}>
            {compare.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : (
              <IconPlayerPlay className="size-4" />
            )}
            {t("run")}
          </Button>
        </div>

        {result && result.candidates.length > 0 && (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {result.candidates.map((c) => (
              <CandidateCard key={c.runtime} candidate={c} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CandidateCard({ candidate }: { candidate: RuntimeCompareCandidate }) {
  const t = useTranslations("settings.runtimes.compare");
  const input = candidate.tokens.input ?? 0;
  const output = candidate.tokens.output ?? 0;
  return (
    <div className="flex flex-col rounded-md border p-3">
      <div className="mb-1 flex items-center gap-2">
        <IconRobot className="size-3.5" />
        <span className="text-sm font-semibold">{candidate.runtime}</span>
        {candidate.ok ? (
          <Badge variant="success">OK</Badge>
        ) : (
          <Badge variant="danger">{candidate.error ?? "fail"}</Badge>
        )}
      </div>
      <dl className="mb-2 grid grid-cols-3 gap-1 text-[11px]">
        <div>
          <dt className="sh-muted">{t("latency")}</dt>
          <dd className="font-mono">{candidate.latency_ms}ms</dd>
        </div>
        <div>
          <dt className="sh-muted">{t("tokens")}</dt>
          <dd className="font-mono">
            {input}→{output}
          </dd>
        </div>
        <div>
          <dt className="sh-muted">{t("cost")}</dt>
          <dd className="font-mono">${candidate.cost_usd.toFixed(4)}</dd>
        </div>
      </dl>
      {candidate.verdict && (
        <div className="mb-2 flex items-center gap-1.5 text-[11px]">
          <Badge variant={VERDICT_BADGE[candidate.verdict.verdict]}>
            {candidate.verdict.verdict}
          </Badge>
          <span className="sh-muted">
            {t("score")}: {candidate.verdict.score.toFixed(2)}
          </span>
          {candidate.verdict.nli_agreement !== null && (
            <span className="sh-muted">
              NLI: {candidate.verdict.nli_agreement.toFixed(2)}
            </span>
          )}
        </div>
      )}
      <div className="max-h-40 overflow-y-auto rounded border bg-black/5 p-2 text-[11px] leading-relaxed dark:bg-white/5">
        {candidate.final_text ? (
          <p className="whitespace-pre-wrap">{candidate.final_text}</p>
        ) : (
          <p className="sh-muted">{t("noOutput")}</p>
        )}
      </div>
      {candidate.verdict?.reasons?.length ? (
        <ul className="mt-2 text-[10px] sh-muted">
          {candidate.verdict.reasons.slice(0, 4).map((r, i) => (
            <li key={i}>· {r}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
