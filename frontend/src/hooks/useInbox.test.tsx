import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useContacts } from "./useInbox";

afterEach(() => vi.restoreAllMocks());

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

test("useContacts builds the tenant-scoped URL with filters", async () => {
  const f = vi.fn(async () => new Response("[]", { status: 200 }));
  vi.stubGlobal("fetch", f);
  renderHook(() => useContacts("acme", "inst-1", { status: "human", q: "ana" }), { wrapper: wrap() });
  await waitFor(() => expect(f).toHaveBeenCalled());
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const calledUrl = (f.mock.calls as any[][])[0][0] as string;
  expect(calledUrl).toBe(
    "/api/console/tenants/acme/instances/inst-1/contacts?status=human&q=ana",
  );
});
