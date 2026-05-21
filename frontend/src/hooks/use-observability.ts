"use client";

import { useQuery } from "@tanstack/react-query";

import { API_BASE_URL } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

// ─── Prometheus parser ────────────────────────────────────────
// We consume our own exposition text directly rather than mount a full
// prometheus-client dep on the frontend. Our exposition is stable (see
// backend/app/core/prometheus.py) so the parser below covers exactly the
// shapes we emit: single-value counters + buckets/sum/count histograms.

export type LabelSet = Record<string, string>;

export interface CounterSample {
  labels: LabelSet;
  value: number;
}

export interface CounterMetric {
  name: string;
  help: string;
  samples: CounterSample[];
}

export interface HistogramSeries {
  labels: LabelSet;
  buckets: Array<{ le: string; count: number }>;
  sum: number;
  count: number;
}

export interface HistogramMetric {
  name: string;
  help: string;
  series: HistogramSeries[];
}

export interface PrometheusSnapshot {
  counters: Record<string, CounterMetric>;
  histograms: Record<string, HistogramMetric>;
  raw: string;
}

function parseLabels(raw: string): LabelSet {
  if (!raw) return {};
  const trimmed = raw.replace(/^\{|\}$/g, "");
  if (!trimmed) return {};
  // ridiculously simple parser; we control the producer so we don't need to
  // handle escape sequences here beyond the bare minimum.
  const out: LabelSet = {};
  const re = /([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(trimmed)) !== null) {
    const key = m[1];
    const value = m[2];
    if (!key || value === undefined) continue;
    out[key] = value.replace(/\\"/g, '"').replace(/\\n/g, "\n");
  }
  return out;
}

function key(labels: LabelSet, skip: string[] = []): string {
  return Object.keys(labels)
    .filter((k) => !skip.includes(k))
    .sort()
    .map((k) => `${k}=${labels[k]}`)
    .join("|");
}

export function parsePrometheus(text: string): PrometheusSnapshot {
  const counters: Record<string, CounterMetric> = {};
  const histograms: Record<string, HistogramMetric> = {};
  const helpByMetric: Record<string, string> = {};
  const typeByMetric: Record<string, "counter" | "histogram"> = {};

  const lines = text.split("\n");
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("# HELP ")) {
      const rest = line.slice(7);
      const sp = rest.indexOf(" ");
      if (sp > 0) helpByMetric[rest.slice(0, sp)] = rest.slice(sp + 1);
      continue;
    }
    if (line.startsWith("# TYPE ")) {
      const [, name, kind] = line.split(" ");
      if (name && (kind === "counter" || kind === "histogram")) {
        typeByMetric[name] = kind;
      }
      continue;
    }
    if (line.startsWith("#")) continue;

    // metric[{labels}] value
    const labelOpen = line.indexOf("{");
    let name: string;
    let labels: LabelSet;
    let valuePart: string;
    if (labelOpen === -1) {
      const sp = line.lastIndexOf(" ");
      name = line.slice(0, sp);
      labels = {};
      valuePart = line.slice(sp + 1);
    } else {
      const labelClose = line.indexOf("}", labelOpen);
      name = line.slice(0, labelOpen);
      labels = parseLabels(line.slice(labelOpen, labelClose + 1));
      valuePart = line.slice(labelClose + 1).trim();
    }
    const value = Number(valuePart);
    if (!Number.isFinite(value)) continue;

    // Histogram families end with _bucket / _sum / _count → collapse to the
    // base name and attach to histograms[name].
    for (const suffix of ["_bucket", "_sum", "_count"]) {
      if (name.endsWith(suffix)) {
        const base = name.slice(0, -suffix.length);
        if (typeByMetric[base] !== "histogram") continue;
        if (!histograms[base]) {
          histograms[base] = {
            name: base,
            help: helpByMetric[base] ?? "",
            series: [],
          };
        }
        const seriesKey = key(labels, ["le"]);
        let series = histograms[base].series.find(
          (s) => key(s.labels) === seriesKey,
        );
        if (!series) {
          const { le: _le, ...labelsWithoutLe } = labels;
          void _le;
          series = {
            labels: labelsWithoutLe,
            buckets: [],
            sum: 0,
            count: 0,
          };
          histograms[base].series.push(series);
        }
        if (suffix === "_bucket") {
          series.buckets.push({ le: labels.le ?? "+Inf", count: value });
        } else if (suffix === "_sum") {
          series.sum = value;
        } else if (suffix === "_count") {
          series.count = value;
        }
        break;
      }
    }

    if (typeByMetric[name] === "counter") {
      if (!counters[name]) {
        counters[name] = {
          name,
          help: helpByMetric[name] ?? "",
          samples: [],
        };
      }
      const metric = counters[name];
      if (metric) metric.samples.push({ labels, value });
    }
  }

  // Sort histogram buckets by le numeric order for deterministic rendering.
  for (const hist of Object.values(histograms)) {
    for (const series of hist.series) {
      series.buckets.sort((a, b) => {
        const av = a.le === "+Inf" ? Infinity : Number(a.le);
        const bv = b.le === "+Inf" ? Infinity : Number(b.le);
        return av - bv;
      });
    }
  }

  return { counters, histograms, raw: text };
}

// ─── Hook ─────────────────────────────────────────────────────
/** Poll the Prometheus exposition endpoint and parse the payload. */
export function usePrometheusSnapshot(refetchMs = 15_000) {
  return useQuery<PrometheusSnapshot>({
    queryKey: ["observability", "prometheus"],
    queryFn: async () => {
      const token = useAuthStore.getState().accessToken;
      const workspaceId = useWorkspaceStore.getState().activeWorkspaceId;
      const headers: Record<string, string> = { Accept: "text/plain" };
      if (token) headers.Authorization = `Bearer ${token}`;
      if (workspaceId) headers["X-Workspace-Id"] = workspaceId;
      const res = await fetch(`${API_BASE_URL}/api/v1/metrics/prometheus`, {
        headers,
        credentials: "include",
      });
      if (!res.ok) throw new Error(`prometheus scrape failed: ${res.status}`);
      return parsePrometheus(await res.text());
    },
    refetchInterval: refetchMs,
  });
}

// ─── Helpers ──────────────────────────────────────────────────
/** Approximate a histogram percentile from bucket counts. */
export function histogramPercentile(
  series: HistogramSeries,
  p: number,
): number | null {
  if (series.count === 0) return null;
  const target = series.count * p;
  const buckets = series.buckets;
  for (let i = 0; i < buckets.length; i++) {
    const bucket = buckets[i];
    if (!bucket) continue;
    if (bucket.count >= target) {
      const le = bucket.le;
      return le === "+Inf"
        ? Number(buckets[buckets.length - 2]?.le ?? NaN)
        : Number(le);
    }
  }
  return null;
}

/** Sum counter samples that match a predicate. */
export function sumCounter(
  metric: CounterMetric | undefined,
  pred?: (labels: LabelSet) => boolean,
): number {
  if (!metric) return 0;
  return metric.samples.reduce(
    (acc, s) => (pred && !pred(s.labels) ? acc : acc + s.value),
    0,
  );
}
