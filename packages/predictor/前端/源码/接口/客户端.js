const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

/** 前端只读：禁止发起会改数据的请求 */
const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function api(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  if (WRITE_METHODS.has(method)) {
    throw new Error("前端为只读展示，已禁止写操作");
  }
  const url = path.startsWith("http") ? path : `${API_BASE}${path}`;
  const res = await fetch(url, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
    method: "GET",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}
