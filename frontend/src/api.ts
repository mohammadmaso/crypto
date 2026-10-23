import { useCallback, useEffect, useState } from "react";

export async function api<T = any>(path: string, opts?: { method?: string; body?: unknown }): Promise<T> {
  const r = await fetch("/api" + path, {
    method: opts?.method ?? (opts?.body !== undefined ? "POST" : "GET"),
    headers: { "Content-Type": "application/json" },
    body: opts?.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error(typeof data === "object" && data?.detail ? String(data.detail) : `HTTP ${r.status}`);
  return data as T;
}

export function useApi<T = any>(path: string, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => api<T>(path).then(setData).then(() => setError(null)).catch((e) => setError(e.message)), [path]);
  useEffect(() => {
    load();
    if (!refreshMs) return;
    const t = setInterval(load, refreshMs);
    return () => clearInterval(t);
  }, [load, refreshMs]);
  return { data, error, reload: load };
}

export const pct = (x: number | null | undefined, d = 1) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
export const num = (x: number | null | undefined, d = 2) => (x == null ? "–" : x.toLocaleString(undefined, { maximumFractionDigits: d }));
