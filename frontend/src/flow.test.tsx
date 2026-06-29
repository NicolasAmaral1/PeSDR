// frontend/src/flow.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

afterEach(() => vi.restoreAllMocks());

test("end-to-end read-only: boot → pick contact → read conversation", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/api/console/me")) return new Response(JSON.stringify({ user: { id: "u", username: "op" }, tenants: [{ slug: "acme", display_name: "Acme" }] }), { status: 200 });
    if (url.endsWith("/instances")) return new Response(JSON.stringify([{ id: "inst-1", channel_label: "main", display_name: "Main", phone_e164: null }]), { status: 200 });
    if (url.includes("/contacts/l1/messages")) return new Response(JSON.stringify([{ id: "m1", direction: "in", origin: "lead", text: "oi", media_type: "text", audio_url: null, transcription: null, at: "2026-06-26T10:00:00Z" }]), { status: 200 });
    if (url.includes("/contacts/l1/talks")) return new Response("[]", { status: 200 });
    if (url.endsWith("/contacts/l1")) return new Response(JSON.stringify({ lead_id: "l1", display_name: "Ana", whatsapp_e164: "+55", state: "human", funnel_node: "humano", active_talk_id: "t1", ai_reasoning: null, window_open: true, window_expires_at: null }), { status: 200 });
    if (url.includes("/contacts")) return new Response(JSON.stringify([{ lead_id: "l1", display_name: "Ana", whatsapp_e164: "+55", last_message_at: "2026-06-26T10:00:00Z", last_message_preview: "oi", state: "human", funnel_node: "humano", unread: 0 }]), { status: 200 });
    return new Response("[]", { status: 200 });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><App /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByText("Ana")).toBeInTheDocument());
  await userEvent.click(screen.getByText("Ana"));
  await waitFor(() => expect(screen.getAllByText("oi").length).toBeGreaterThan(0));
  expect(screen.getByTestId("btn-takeover")).toBeDisabled();
});
