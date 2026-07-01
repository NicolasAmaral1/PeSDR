// frontend/src/components/ContactList.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ContactList } from "./ContactList";
import type { ContactOut } from "../types";

afterEach(() => vi.restoreAllMocks());

const contacts: ContactOut[] = [
  { lead_id: "l1", display_name: "Ana", whatsapp_e164: "+5511999", last_message_at: "2026-06-26T12:00:00Z", last_message_preview: "oi", state: "human", funnel_node: "qualificacao", unread: 2 },
  { lead_id: "l2", display_name: null, whatsapp_e164: "+5511888", last_message_at: null, last_message_preview: null, state: "awaiting", funnel_node: null, unread: 0 },
];

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{children}</QueryClientProvider>);
}

test("renders contacts incl. the awaiting (no-Talk) contact, and selects on click", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(contacts), { status: 200 })));
  const onSelect = vi.fn();
  wrap(<ContactList slug="acme" instanceId="inst-1" selectedLeadId={null} onSelect={onSelect} />);
  await waitFor(() => expect(screen.getByText("Ana")).toBeInTheDocument());
  // awaiting contact with no name falls back to phone, and still renders
  expect(screen.getByText("+5511888")).toBeInTheDocument();
  expect(screen.getByText(/Aguardando/)).toBeInTheDocument();
  await userEvent.click(screen.getByText("Ana"));
  expect(onSelect).toHaveBeenCalledWith("l1");
});

test("status filter re-queries with the status param", async () => {
  const f = vi.fn(async () => new Response(JSON.stringify(contacts), { status: 200 }));
  vi.stubGlobal("fetch", f);
  wrap(<ContactList slug="acme" instanceId="inst-1" selectedLeadId={null} onSelect={() => {}} />);
  await waitFor(() => expect(f).toHaveBeenCalled());
  await userEvent.click(screen.getByTestId("filter-human"));
  await waitFor(() =>
    expect(f.mock.calls.some((c) => String((c as unknown[])[0]).includes("status=human"))).toBe(true),
  );
});
