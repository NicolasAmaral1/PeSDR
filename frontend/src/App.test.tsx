// frontend/src/App.test.tsx  (updated in Task 6 — placeholder div replaced by ContactList)
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

afterEach(() => vi.restoreAllMocks());

function mockFetch(routes: Record<string, unknown>) {
  // Match the LONGEST matching prefix so .../instances/inst-1/contacts does not
  // collide with the shorter .../instances route (which would return the wrong shape).
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const key = keys.find((k) => url.startsWith(k));
    if (!key) return new Response("[]", { status: 200 });
    return new Response(JSON.stringify(routes[key]), { status: 200 });
  }));
}

function renderApp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><App /></QueryClientProvider>,
  );
}

test("boots, resolves tenant + instance, renders the shell with ContactList", async () => {
  mockFetch({
    "/api/console/me": { user: { id: "u", username: "op" }, tenants: [{ slug: "acme", display_name: "Acme" }] },
    "/api/console/tenants/acme/instances": [{ id: "inst-1", channel_label: "main", display_name: "Main", phone_e164: null }],
    "/api/console/tenants/acme/instances/inst-1/contacts": [],
  });
  renderApp();
  // ContactList renders a search input placeholder once the shell is ready
  await waitFor(() => expect(screen.getByPlaceholderText("Buscar contato…")).toBeInTheDocument());
  // conversation placeholder is shown until a contact is selected
  expect(screen.getByText("Selecione um contato")).toBeInTheDocument();
  // boot-sequence guard: the contacts query must use the RESOLVED slug + instance
  // (not undefined / the wrong one) — fetch the tenant-scoped contacts URL.
  await waitFor(() => {
    const urls = (vi.mocked(globalThis.fetch).mock.calls as unknown[][]).map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/tenants/acme/instances/inst-1/contacts"))).toBe(true);
  });
});
