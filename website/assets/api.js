/* Thin fetch helper for the MemALL gateway REST API.
   Loopback SPA data routes (/health, /memories, /agents, ...) need no token;
   state-changing calls echo the CSRF token from GET /ui/session. */

const DEFAULT_BASE = localStorage.getItem("memall.api") || "http://127.0.0.1:9920";

export function apiBase() {
  const u = new URLSearchParams(location.search).get("api_url");
  return (u || DEFAULT_BASE).replace(/\/+$/, "");
}

export function setApiBase(v) { localStorage.setItem("memall.api", v); }

let csrf = "";
async function ensureCsrf(fetchImpl) {
  if (csrf) return csrf;
  try {
    const r = await fetchImpl(`${apiBase()}/ui/session`);
    csrf = (await r.json()).csrf_token || "";
  } catch { csrf = ""; }
  return csrf;
}

export async function get(path, fetchImpl = fetch) {
  const r = await fetchImpl(`${apiBase()}${path}`, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}

export async function post(path, body, fetchImpl = fetch) {
  const token = await ensureCsrf(fetchImpl);
  const r = await fetchImpl(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-MemAll-CSRF": token },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}

export async function ping(fetchImpl = fetch) {
  const r = await fetchImpl(`${apiBase()}/health`, { signal: AbortSignal.timeout ? AbortSignal.timeout(2500) : undefined });
  if (!r.ok) throw new Error("unhealthy");
  return r.json();
}
