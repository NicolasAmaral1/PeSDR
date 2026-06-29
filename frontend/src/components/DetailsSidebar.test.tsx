// frontend/src/components/DetailsSidebar.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DetailsSidebar } from "./DetailsSidebar";

afterEach(() => vi.restoreAllMocks());

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{children}</QueryClientProvider>);
}

test("shows funnel + AI context + window state", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    lead_id: "l1", display_name: "Ana", whatsapp_e164: "+55", state: "human",
    funnel_node: "qualificacao", active_talk_id: "t1", ai_reasoning: "lead pediu preço",
    window_open: false, window_expires_at: "2026-06-26T12:00:00Z",
  }), { status: 200 })));
  wrap(<DetailsSidebar slug="acme" leadId="l1" />);
  await waitFor(() => expect(screen.getByText("qualificacao")).toBeInTheDocument());
  expect(screen.getByText(/lead pediu preço/)).toBeInTheDocument();
  expect(screen.getByText(/Janela fechada/)).toBeInTheDocument();
});
