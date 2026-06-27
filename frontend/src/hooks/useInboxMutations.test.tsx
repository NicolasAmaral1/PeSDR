// frontend/src/hooks/useInboxMutations.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useSend, useTakeover } from "./useInboxMutations";

afterEach(() => vi.restoreAllMocks());

function wrap() {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

test("useSend posts text + a client_message_id and optimistically adds a pending bubble", async () => {
  const { qc, wrapper } = wrap();
  qc.setQueryData(["messages", "acme", "l1"], []);
  const f = vi.fn(async () => new Response(JSON.stringify({ outbound_id: "o1", external_id: "x", status: "sent" }), { status: 200 }));
  vi.stubGlobal("fetch", f);

  const { result } = renderHook(() => useSend("acme", "l1"), { wrapper });
  act(() => { result.current.mutate({ text: "olá" }); });

  // optimistic pending bubble appears synchronously in the cache
  await waitFor(() => {
    const msgs = qc.getQueryData(["messages", "acme", "l1"]) as any[];
    expect(msgs.some((m) => m._pending && m.text === "olá")).toBe(true);
  });

  // the POST carried a uuid client_message_id
  const calls = f.mock.calls as unknown as [string, RequestInit][];
  const body = JSON.parse(calls[0][1].body as string);
  expect(body.text).toBe("olá");
  expect(typeof body.client_message_id).toBe("string");
  expect(body.client_message_id.length).toBeGreaterThan(10);
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
});

test("useTakeover posts to the takeover route", async () => {
  const { wrapper } = wrap();
  const f = vi.fn(async () => new Response(JSON.stringify({ talk_id: "t1", handling_mode: "human" }), { status: 200 }));
  vi.stubGlobal("fetch", f);
  const { result } = renderHook(() => useTakeover("acme", "l1"), { wrapper });
  act(() => { result.current.mutate(); });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  const tcalls = f.mock.calls as unknown as [string, RequestInit][];
  expect(String(tcalls[0][0])).toBe("/api/console/tenants/acme/contacts/l1/takeover");
});
