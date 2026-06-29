// frontend/src/live.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { act } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

class FakeWS {
  static instances: FakeWS[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) { this.url = url; FakeWS.instances.push(this); }
  close() { this.onclose?.(); }
  open() { this.onopen?.(); }
  emit(env: unknown) { this.onmessage?.({ data: JSON.stringify(env) } as MessageEvent); }
}

function mockFetch(routes: Record<string, unknown>) {
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length); // longest-first
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const k = keys.find((kk) => url.startsWith(kk));
    return new Response(JSON.stringify(k ? routes[k] : []), { status: 200 });
  }));
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS as unknown as typeof WebSocket);
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("opens a live socket for the resolved instance and shows 'ao vivo'", async () => {
  mockFetch({
    "/api/console/me": { user: { id: "u", username: "op" }, tenants: [{ slug: "acme", display_name: "Acme" }] },
    "/api/console/tenants/acme/instances": [{ id: "inst-1", channel_label: "main", display_name: "Main", phone_e164: null }],
    "/api/console/tenants/acme/instances/inst-1/contacts": [],
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><App /></QueryClientProvider>);
  await waitFor(() => expect(FakeWS.instances.length).toBeGreaterThanOrEqual(1));
  expect(FakeWS.instances[0].url).toContain("/ws/instances/inst-1");
  act(() => FakeWS.instances[0].open());
  await waitFor(() => expect(screen.getByText(/ao vivo/i)).toBeInTheDocument());
});
