export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let _redirect = () => {
  window.location.href = "/console/login";
};
export function _setRedirect(fn: () => void) {
  _redirect = fn;
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "include", redirect: "manual" });
  // require_console_user redirects (3xx) to /console/login on auth failure;
  // with redirect:"manual" that is an opaqueredirect (status 0). Also accept 401.
  if (res.type === "opaqueredirect" || res.status === 401) {
    _redirect();
    throw new ApiError(401, "unauthenticated");
  }
  if (!res.ok) {
    throw new ApiError(res.status, `GET ${path} -> ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "include",
    redirect: "manual",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.type === "opaqueredirect" || res.status === 401) {
    _redirect();
    throw new ApiError(401, "unauthenticated");
  }
  if (!res.ok) {
    let detail = `POST ${path} -> ${res.status}`;
    try {
      const data = (await res.json()) as { detail?: string };
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      // non-JSON error body; keep the default detail
    }
    throw new ApiError(res.status, detail);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}
