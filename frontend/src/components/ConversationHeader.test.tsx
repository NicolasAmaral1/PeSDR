// frontend/src/components/ConversationHeader.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ConversationHeader } from "./ConversationHeader";
import type { ContactDetailOut } from "../types";

afterEach(() => vi.restoreAllMocks());

const base: ContactDetailOut = {
  lead_id: "l1", display_name: "Ana", whatsapp_e164: "+55", state: "ai",
  funnel_node: "q", active_talk_id: "t1", ai_reasoning: null, window_open: true, window_expires_at: null,
};

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

test("AI state → Assumir is enabled and calls takeover", async () => {
  const f = vi.fn(async () => new Response(JSON.stringify({ talk_id: "t1", handling_mode: "human" }), { status: 200 }));
  vi.stubGlobal("fetch", f);
  wrap(<ConversationHeader detail={base} slug="acme" />);
  const btn = screen.getByTestId("btn-takeover");
  expect(btn).toBeEnabled();
  await userEvent.click(btn);
  await waitFor(() => expect(String((f.mock.calls as unknown[][])[0][0])).toContain("/contacts/l1/takeover"));
});

test("human state → Devolver is enabled and calls release", async () => {
  const f = vi.fn(async () => new Response(JSON.stringify({ talk_id: "t1", handling_mode: "ai" }), { status: 200 }));
  vi.stubGlobal("fetch", f);
  wrap(<ConversationHeader detail={{ ...base, state: "human" }} slug="acme" />);
  const btn = screen.getByTestId("btn-release");
  expect(btn).toBeEnabled();
  await userEvent.click(btn);
  await waitFor(() => expect(String((f.mock.calls as unknown[][])[0][0])).toContain("/contacts/l1/release"));
});
