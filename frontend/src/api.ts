import { useCallback, useEffect, useState } from "react";

export class ApiError extends Error {}

export async function api<T = any>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, options);
  const isJson = response.headers.get("content-type")?.includes("json");
  const body = isJson ? await response.json() : null;
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`;
    throw new ApiError(detail);
  }
  return body as T;
}

export function post<T = any>(url: string, body?: unknown, method = "POST"): Promise<T> {
  return api<T>(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** Fetch `url` on mount and whenever it changes. A null url fetches nothing. */
export function useApi<T = any>(url: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(url !== null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (url === null) {
      setData(null);
      setLoading(false);
      return;
    }
    let current = true;
    setLoading(true);
    api<T>(url)
      .then((result) => {
        if (current) {
          setData(result);
          setError(null);
        }
      })
      .catch((e: Error) => current && setError(e.message))
      .finally(() => current && setLoading(false));
    return () => {
      current = false;
    };
  }, [url, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, loading, reload };
}

/** "ja" -> "Japanese". Falls back to the code if the browser doesn't know it. */
export function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

export interface Card {
  id: number;
  kind: "movie" | "series";
  title: string;
  year: number | null;
  poster_url: string | null;
  source: string;
  on_jellyfin: boolean;
  removed_from_jellyfin: boolean;
  resolution: string | null;
}

export interface TmdbCard {
  tmdb_id: number;
  kind: "movie" | "series";
  title: string;
  year: number | null;
  summary: string;
  poster_url: string | null;
  score: number | null;
  status: "jellyfin" | "saved" | "excluded" | null;
  item_id: number | null;
}

export interface Location {
  id: number;
  origin: "jellyfin" | "scan";
  stored_path: string;
  local_path: string | null;
  available: boolean;
  reason: string | null;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
  resolution: string | null;
}

export interface JobStatus {
  running: boolean;
  name: string | null;
  message: string;
  result: any;
  error: string | null;
}

export function formatBytes(bytes: number | null): string {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** power).toFixed(power > 2 ? 1 : 0)} ${units[power]}`;
}
