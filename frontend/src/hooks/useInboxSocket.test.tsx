// frontend/src/hooks/useInboxSocket.test.tsx
import type React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { applyInboxEvent, useInboxSocket } from "./useInboxSocket";

// --- pure event-routing tests (no socket) ---
function spyQC() {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries").mockImplementation(() => Promise.resolve());
  return { qc, spy };
}
const keys = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown[] }).queryKey));

test("message.created invalidates messages + contact + contacts", () => {
  const { qc, spy } = spyQC();
  applyInboxEvent(qc, "acme", { seq: 1, type: "message.created", instance_id: "i1", lead_id: "l1", payload: {} });
  const k = keys(spy);
  expect(k).toContain(JSON.stringify(["messages", "acme", "l1"]));
  expect(k).toContain(JSON.stringify(["contact", "acme", "l1"]));
  expect(k).toContain(JSON.stringify(["contacts", "acme"]));
});

test("talk.updated invalidates contact + contacts (no messages)", () => {
  const { qc, spy } = spyQC();
  applyInboxEvent(qc, "acme", { seq: 2, type: "talk.updated", instance_id: "i1", lead_id: "l1", payload: {} });
  const k = keys(spy);
  expect(k).toContain(JSON.stringify(["contact", "acme", "l1"]));
  expect(k).toContain(JSON.stringify(["contacts", "acme"]));
  expect(k).not.toContain(JSON.stringify(["messages", "acme", "l1"]));
});

test("unknown event type is ignored (no invalidation)", () => {
  const { qc, spy } = spyQC();
  applyInboxEvent(qc, "acme", { seq: 3, type: "message.status_updated", instance_id: "i1", lead_id: "l1", payload: {} });
  expect(spy).not.toHaveBeenCalled();
});

// --- socket-lifecycle tests (mock WebSocket + fake timers) ---
class FakeWS {
  static instances: FakeWS[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  // test helpers
  open() { this.onopen?.(); }
  emit(env: unknown) { this.onmessage?.({ data: JSON.stringify(env) } as MessageEvent); }
}

function wrap() {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries").mockImplementation(() => Promise.resolve());
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, spy, wrapper };
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS as unknown as typeof WebSocket);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("opens a socket to the instance channel and reports connected on open", () => {
  const { wrapper } = wrap();
  const { result } = renderHook(() => useInboxSocket("acme", "i1"), { wrapper });
  expect(FakeWS.instances).toHaveLength(1);
  expect(FakeWS.instances[0].url).toContain("/ws/instances/i1");
  expect(result.current.connected).toBe(false);
  act(() => FakeWS.instances[0].open());
  expect(result.current.connected).toBe(true);
});

test("a live message.created event triggers query invalidation", () => {
  const { spy, wrapper } = wrap();
  renderHook(() => useInboxSocket("acme", "i1"), { wrapper });
  act(() => FakeWS.instances[0].open());
  spy.mockClear();
  act(() => FakeWS.instances[0].emit({ seq: 1, type: "message.created", instance_id: "i1", lead_id: "l1", payload: {} }));
  const k = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown[] }).queryKey));
  expect(k).toContain(JSON.stringify(["messages", "acme", "l1"]));
});

test("duplicate/older seq is ignored", () => {
  const { spy, wrapper } = wrap();
  renderHook(() => useInboxSocket("acme", "i1"), { wrapper });
  act(() => FakeWS.instances[0].open());
  act(() => FakeWS.instances[0].emit({ seq: 5, type: "talk.updated", instance_id: "i1", lead_id: "l1", payload: {} }));
  spy.mockClear();
  act(() => FakeWS.instances[0].emit({ seq: 5, type: "talk.updated", instance_id: "i1", lead_id: "l1", payload: {} }));
  expect(spy).not.toHaveBeenCalled();
});

test("reconnects with backoff after a close", () => {
  vi.useFakeTimers();
  const { wrapper } = wrap();
  renderHook(() => useInboxSocket("acme", "i1"), { wrapper });
  expect(FakeWS.instances).toHaveLength(1);
  act(() => FakeWS.instances[0].onclose?.());     // server dropped us
  act(() => vi.advanceTimersByTime(1000));        // first backoff
  expect(FakeWS.instances.length).toBeGreaterThanOrEqual(2); // reconnected
});
