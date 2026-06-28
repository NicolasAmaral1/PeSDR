import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ConversationView } from "./components/ConversationView";

afterEach(() => vi.restoreAllMocks());

function mockRoutes() {
  // Stateful: after the operator send commits, the messages endpoint echoes the
  // persisted operator message — so the onSettled refetch reconciles the
  // optimistic bubble with the authoritative server copy (the real flow).
  let sent = false;
  const realMsg = {
    id: "m-real", direction: "out", origin: "operator", text: "oi do operador",
    media_type: "text", audio_url: null, transcription: null, at: "2026-06-27T10:00:00Z",
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    // most-specific first to avoid prefix collisions
    if (url.includes("/contacts/l1/send")) {
      sent = true;
      return new Response(JSON.stringify({ outbound_id: "o1", external_id: "x", status: "sent" }), { status: 200 });
    }
    if (url.includes("/contacts/l1/messages")) return new Response(JSON.stringify(sent ? [realMsg] : []), { status: 200 });
    if (url.includes("/contacts/l1/talks")) return new Response("[]", { status: 200 });
    if (url.endsWith("/contacts/l1")) return new Response(JSON.stringify({ lead_id: "l1", display_name: "Ana", whatsapp_e164: "+55", state: "human", funnel_node: "q", active_talk_id: "t1", ai_reasoning: null, window_open: true, window_expires_at: null }), { status: 200 });
    void init;
    return new Response("{}", { status: 200 });
  }));
}

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

test("human + open window → composer is usable and a message can be sent", async () => {
  mockRoutes();
  wrap(<ConversationView slug="acme" leadId="l1" />);
  await waitFor(() => expect(screen.getByText("Ana")).toBeInTheDocument());
  const input = screen.getByTestId("composer-input") as HTMLTextAreaElement;
  expect(input).toBeEnabled();
  await userEvent.type(input, "oi do operador");
  // optimistic pending bubble shows immediately ("enviando…")
  await userEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(screen.getByText("oi do operador")).toBeInTheDocument());
  // reconcile guard: after onSettled refetch, the optimistic _pending bubble is
  // replaced by the persisted server message — so "enviando…" must disappear.
  // (If useSend's onSettled invalidate regressed, the pending bubble would
  // persist and this assertion would fail.)
  await waitFor(() => expect(screen.queryByText("enviando…")).not.toBeInTheDocument());
  expect(screen.getAllByText("oi do operador")).toHaveLength(1);
});
