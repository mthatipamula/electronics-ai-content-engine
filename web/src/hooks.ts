import { useCallback, useEffect, useState } from 'react';

export function useLoad<T>(load: () => Promise<T>, deps: unknown[], pollMs?: (data: T | null) => number | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);
  useEffect(() => {
    let live = true;
    run().then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e: Error) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [run, tick]);
  useEffect(() => {
    const ms = pollMs?.(data);
    if (!ms) return;
    const t = setTimeout(() => setTick((x) => x + 1), ms);
    return () => clearTimeout(t);
  }, [data, pollMs]);
  return { data, error, reload: () => setTick((x) => x + 1) };
}

export function useReviewer(): [string, (n: string) => void] {
  const [name, setName] = useState(() => { try { return localStorage.getItem('reviewer') ?? ''; } catch { return ''; } });
  const save = (n: string) => { setName(n); try { localStorage.setItem('reviewer', n); } catch { /* unavailable */ } };
  return [name, save];
}
