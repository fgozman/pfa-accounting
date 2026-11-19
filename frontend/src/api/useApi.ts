/** Small data-loading hook with explicit loading/error/empty states. */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
}

export function useApi<T>(
  path: string | null,
  query?: Record<string, string | number | boolean | undefined>,
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [nonce, setNonce] = useState(0);
  const requestId = useRef(0);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    if (!path) {
      setData(null);
      return;
    }
    const current = ++requestId.current;
    setLoading(true);
    setError(null);
    apiFetch<T>(path, { query })
      .then((body) => {
        if (requestId.current === current) setData(body);
      })
      .catch((caught: unknown) => {
        if (requestId.current !== current) return;
        setError(caught instanceof ApiError ? caught : null);
      })
      .finally(() => {
        if (requestId.current === current) setLoading(false);
      });
    // query is a plain object literal per render; depend on its serialisation
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, JSON.stringify(query ?? {})]);

  return { data, loading, error, reload };
}
