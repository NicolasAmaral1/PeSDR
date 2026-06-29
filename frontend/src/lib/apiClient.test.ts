import { afterEach, describe, expect, test, vi } from "vitest";
import { apiGet, apiPost, ApiError, _setRedirect } from "./apiClient";

afterEach(() => vi.restoreAllMocks());

describe("apiGet", () => {
  test("returns json on 200", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ ok: 1 }), { status: 200 })));
    await expect(apiGet<{ ok: number }>("/api/x")).resolves.toEqual({ ok: 1 });
  });

  test("401 triggers redirect and rejects", async () => {
    const redirect = vi.fn();
    _setRedirect(redirect);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));
    await expect(apiGet("/api/x")).rejects.toBeInstanceOf(ApiError);
    expect(redirect).toHaveBeenCalledOnce();
  });

  test("opaqueredirect (backend bounce to /console/login) triggers redirect", async () => {
    const redirect = vi.fn();
    _setRedirect(redirect);
    // simulate fetch(redirect:"manual") meeting a 3xx → opaqueredirect (status 0)
    const opaque = { type: "opaqueredirect", status: 0, ok: false } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn(async () => opaque));
    await expect(apiGet("/api/x")).rejects.toBeInstanceOf(ApiError);
    expect(redirect).toHaveBeenCalledOnce();
  });

  test("sends credentials + manual redirect", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", f);
    await apiGet("/api/x");
    expect(f).toHaveBeenCalledWith("/api/x", expect.objectContaining({ credentials: "include", redirect: "manual" }));
  });
});

describe("apiPost", () => {
  test("posts json and returns parsed body", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", f);
    await expect(apiPost<{ ok: number }>("/api/x", { a: 1 })).resolves.toEqual({ ok: 1 });
    expect(f).toHaveBeenCalledWith("/api/x", expect.objectContaining({
      method: "POST",
      credentials: "include",
      redirect: "manual",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ a: 1 }),
    }));
  });

  test("throws ApiError carrying status + detail on 409", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "already human" }), { status: 409 })));
    await expect(apiPost("/api/x", {})).rejects.toMatchObject({ status: 409, message: "already human" });
  });

  test("opaqueredirect triggers login redirect", async () => {
    const redirect = vi.fn();
    _setRedirect(redirect);
    const opaque = { type: "opaqueredirect", status: 0, ok: false } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn(async () => opaque));
    await expect(apiPost("/api/x", {})).rejects.toBeInstanceOf(ApiError);
    expect(redirect).toHaveBeenCalledOnce();
  });
});
