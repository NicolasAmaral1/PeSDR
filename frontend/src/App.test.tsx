// frontend/src/App.test.tsx  (replace the Task-2 smoke)
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

afterEach(() => vi.restoreAllMocks());

function mockFetch(routes: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const key = Object.keys(routes).find((k) => url.startsWith(k));
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

test("boots, resolves tenant + instance, renders the shell", async () => {
  mockFetch({
    "/api/console/me": { user: { id: "u", username: "op" }, tenants: [{ slug: "acme", display_name: "Acme" }] },
    "/api/console/tenants/acme/instances": [{ id: "inst-1", channel_label: "main", display_name: "Main", phone_e164: null }],
  });
  renderApp();
  await waitFor(() => expect(screen.getByTestId("contacts-pane")).toBeInTheDocument());
  expect(screen.getByTestId("contacts-pane")).toHaveAttribute("data-instance", "inst-1");
  expect(screen.getByTestId("contacts-pane")).toHaveAttribute("data-slug", "acme");
});
