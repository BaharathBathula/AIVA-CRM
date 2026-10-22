"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { aivaRequest } from "@/lib/api";

/** Small JSON helpers around the authenticated AIVA proxy. */
export const api = {
  get: <T>(path: string) => aivaRequest<T>(path),
  post: <T>(path: string, body?: unknown) =>
    aivaRequest<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => aivaRequest<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) => aivaRequest<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  del: <T = void>(path: string) => aivaRequest<T>(path, { method: "DELETE" }),
};

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Try again.";
}

/**
 * Loads a resource and keeps it fresh. Pass `null` to skip loading.
 * `reload()` refetches without clearing what is on screen.
 */
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const latest = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    // Only the most recent request may update state (avoids stale or duplicate responses).
    const ticket = ++latest.current;
    setLoading(true);
    try {
      const result = await aivaRequest<T>(path);
      if (ticket === latest.current) {
        setData(result);
        setError(null);
      }
    } catch (caught) {
      if (ticket === latest.current) setError(errorMessage(caught));
    } finally {
      if (ticket === latest.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, error, loading, reload: load, setData };
}

/** Debounces a fast-changing value such as a search box. */
export function useDebounced<T>(value: T, delay = 250) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function query(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
