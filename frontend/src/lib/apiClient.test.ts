import { afterEach, describe, expect, test, vi } from "vitest";
import { apiGet, ApiError, _setRedirect } from "./apiClient";

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
