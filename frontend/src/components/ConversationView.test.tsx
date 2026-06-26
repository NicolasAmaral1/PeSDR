import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ConversationView } from "./ConversationView";

afterEach(() => vi.restoreAllMocks());

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{children}</QueryClientProvider>);
}

test("loads detail + messages and shows disabled takeover", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/contacts/l1")) return new Response(JSON.stringify({ lead_id: "l1", display_name: "Ana", whatsapp_e164: "+55", state: "human", funnel_node: "humano", active_talk_id: "t1", ai_reasoning: null, window_open: true, window_expires_at: null }), { status: 200 });
    if (url.includes("/messages")) return new Response(JSON.stringify([{ id: "m1", direction: "in", origin: "lead", text: "oi", media_type: "text", audio_url: null, transcription: null, at: "2026-06-26T10:00:00Z" }]), { status: 200 });
    if (url.includes("/talks")) return new Response("[]", { status: 200 });
    return new Response("{}", { status: 200 });
  }));
  wrap(<ConversationView slug="acme" leadId="l1" />);
  await waitFor(() => expect(screen.getByText("Ana")).toBeInTheDocument());
  expect(screen.getByText("oi")).toBeInTheDocument();
  expect(screen.getByTestId("btn-takeover")).toBeDisabled();
});
