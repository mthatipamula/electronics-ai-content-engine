export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** The service token is added by the proxy (Vite in dev, nginx in the container), never by the browser. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const p = await res.json();
      message = p.title ?? (Array.isArray(p.detail) ? p.detail.map((d: { msg: string }) => d.msg).join('; ') : p.detail) ?? message;
    } catch { /* not JSON */ }
    throw new ApiError(res.status, message);
  }
  return res.json();
}

export const post = <T,>(path: string, body: unknown) => api<T>(path, { method: 'POST', body: JSON.stringify(body) });
