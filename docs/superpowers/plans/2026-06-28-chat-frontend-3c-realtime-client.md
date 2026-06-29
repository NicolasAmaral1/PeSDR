# Chat Frontend 3C — Realtime Client (live updates) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the operator inbox update live — new messages, takeovers, and contact changes appear without a manual refresh — by connecting the SPA to the existing WebSocket server and invalidating the right TanStack queries on each event.

**Architecture:** A `useInboxSocket(slug, instanceId)` hook opens a browser `WebSocket` to the existing `/ws/instances/{instanceId}` server (built in Plano 2B-i; cookie auth, same-origin). On each event envelope it invalidates the matching TanStack query keys (so the contact list / open conversation refetch). Reconnect uses exponential backoff and, per the spec's v1 reconnect rule, **refetches via REST** (no server-side replay) so missed events are caught up. A small "ao vivo / reconectando" indicator surfaces the connection state.

**Tech Stack:** React 18 + TypeScript + TanStack Query 5 + the browser `WebSocket` API + Vitest/@testing-library (3A/3B stack). Backend (unchanged): the WS route + Redis pub/sub from Plano 2B-i. Branch `dev/nicolas-chat-frontend` (3A+3B present on the branch).

## Global Constraints

- **Backend is DONE — do not modify it.** The WS contract (verbatim, from Plano 2B-i):
  - Route: `GET (WebSocket) /ws/instances/{instance_id}`. Auth is the **`pesdr_session` cookie**, sent automatically for a same-origin WebSocket. On auth failure the server `close(code=4401)` BEFORE accept. The client sends nothing; the server pushes JSON frames.
  - Event envelope: `{ "seq": int, "type": str, "instance_id": str, "lead_id": str|null, "payload": object }`.
  - Event types in 3C scope: `"message.created"`, `"talk.updated"`, `"contact.updated"`. (`"message.status_updated"` — delivery ✓✓ — is Plano 2B-ii / Pedro's track, NOT here; ignore unknown types gracefully.)
  - `seq` is a per-instance monotonic counter (Redis INCR).
- **Reconnect = REST refetch (spec v1 rule):** there is NO server-side seq replay. On (re)connect, invalidate the inbox queries for the tenant so the UI catches up via refetch. `seq` is used only to **dedup** live events client-side (ignore an event whose `seq <= lastSeen`).
- **WS URL (same origin):** `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/instances/${instanceId}`. In dev, Vite proxies `/ws` to the API; in prod the API serves both — same origin either way.
- **Query keys to invalidate** (must match the 3A `useInbox.ts` keys exactly): `["contacts", slug]` (prefix → all instance/filter variants), `["contact", slug, leadId]`, `["messages", slug, leadId]`.
- **Event → invalidations:**
  - `message.created` → `["messages", slug, leadId]` + `["contact", slug, leadId]` + `["contacts", slug]`
  - `talk.updated` → `["contact", slug, leadId]` + `["contacts", slug]`
  - `contact.updated` → `["contact", slug, leadId]` + `["contacts", slug]`
- **Reconnect backoff:** start 1000ms, double on each close, cap 15000ms; reset to 1000ms on a successful open. Do NOT reconnect after intentional unmount.
- **No new deps.** Use the native `WebSocket`. **Tests mock `WebSocket`** via `vi.stubGlobal` (jsdom has no WS server) and use `vi.useFakeTimers()` for the backoff.
- **YAGNI:** no optimistic in-place insert from the WS payload (the payload carries a preview, not the full row) — invalidate→refetch is the contract. No message-status handling (2B-ii).
- **Working dir for frontend commands is `frontend/`.** Run tests with `npm run test`; build with `npm run build`. Watch the vitest output for any `Errors`/`Uncaught` line, not just the pass count.
- **TDD; frequent commits.** Commit prefix `feat(chat-ui):`.

---

### Task 1: `useInboxSocket` hook (connect · dedup · invalidate · reconnect)

**Files:**
- Create: `frontend/src/hooks/useInboxSocket.ts`
- Test: `frontend/src/hooks/useInboxSocket.test.tsx`

**Interfaces:**
- Consumes: `useQueryClient` and the 3A query keys.
- Produces: `useInboxSocket(slug: string | undefined, instanceId: string | undefined): { connected: boolean }` — opens a `WebSocket` to the instance channel when both ids are present; parses each frame, dedups by `seq`, invalidates queries per event type; reconnects with backoff; on (re)open invalidates `["contacts", slug]` (+ `["messages", slug]`/`["contact", slug]` on a *re*connect) to catch up; cleans up on unmount/param change. Also exports `applyInboxEvent(qc, slug, env)` (pure, testable event→invalidation mapping) so the routing is unit-testable without a socket.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/hooks/useInboxSocket.test.tsx
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { applyInboxEvent } from "./useInboxSocket";

// --- pure event-routing tests (no socket) ---
function spyQC() {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries").mockImplementation(() => Promise.resolve());
  return { qc, spy };
}
const keys = (spy: ReturnType<typeof vi.fn>) =>
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
```

> Confirm `applyInboxEvent` is the exact exported name. Run `cd frontend && npm run test -- useInboxSocket` → FAIL (module not found).

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- useInboxSocket`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the hook + `applyInboxEvent`**

```ts
// frontend/src/hooks/useInboxSocket.ts
import { useEffect, useRef, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

export interface InboxEvent {
  seq: number;
  type: string;
  instance_id: string;
  lead_id: string | null;
  payload: Record<string, unknown>;
}

/** Pure event → query-invalidation mapping (unit-testable without a socket). */
export function applyInboxEvent(qc: QueryClient, slug: string, env: InboxEvent): void {
  const leadId = env.lead_id;
  switch (env.type) {
    case "message.created":
      if (leadId) qc.invalidateQueries({ queryKey: ["messages", slug, leadId] });
      if (leadId) qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
      qc.invalidateQueries({ queryKey: ["contacts", slug] });
      break;
    case "talk.updated":
    case "contact.updated":
      if (leadId) qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
      qc.invalidateQueries({ queryKey: ["contacts", slug] });
      break;
    default:
      // unknown / out-of-scope types (e.g. message.status_updated → 2B-ii) are ignored
      break;
  }
}

export function useInboxSocket(
  slug: string | undefined,
  instanceId: string | undefined,
): { connected: boolean } {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const lastSeqRef = useRef(0);

  useEffect(() => {
    if (!slug || !instanceId) return;
    let ws: WebSocket | null = null;
    let backoff = 1000;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let unmounted = false;
    let hasConnectedBefore = false;

    function connect() {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}/ws/instances/${instanceId}`);

      ws.onopen = () => {
        setConnected(true);
        backoff = 1000;
        // catch-up via REST refetch (v1 reconnect rule, no server replay)
        qc.invalidateQueries({ queryKey: ["contacts", slug] });
        if (hasConnectedBefore) {
          qc.invalidateQueries({ queryKey: ["messages", slug] });
          qc.invalidateQueries({ queryKey: ["contact", slug] });
        }
        hasConnectedBefore = true;
      };

      ws.onmessage = (e: MessageEvent) => {
        let env: InboxEvent;
        try {
          env = JSON.parse(e.data as string) as InboxEvent;
        } catch {
          return;
        }
        if (typeof env.seq === "number") {
          if (env.seq <= lastSeqRef.current) return; // dedup
          lastSeqRef.current = env.seq;
        }
        applyInboxEvent(qc, slug!, env);
      };

      ws.onclose = () => {
        setConnected(false);
        if (unmounted) return;
        backoff = Math.min(backoff * 2, 15000);
        reconnectTimer = setTimeout(connect, backoff);
      };

      ws.onerror = () => {
        ws?.close();
      };
    }

    connect();

    return () => {
      unmounted = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [slug, instanceId, qc]);

  return { connected };
}
```

- [ ] **Step 4: Run the pure-routing tests green, then add the socket-lifecycle tests**

Run: `cd frontend && npm run test -- useInboxSocket`
Expected: the 3 `applyInboxEvent` tests PASS.

Now add the socket-lifecycle tests (mock `WebSocket` + fake timers) to the SAME file:

```tsx
// append to frontend/src/hooks/useInboxSocket.test.tsx
import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { useInboxSocket } from "./useInboxSocket";

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
```

Run: `cd frontend && npm run test -- useInboxSocket`
Expected: all PASS (3 routing + 4 lifecycle).

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): useInboxSocket — live query invalidation + reconnect"
```

---

### Task 2: Wire the socket into App + a live-connection indicator + integration

**Files:**
- Create: `frontend/src/components/LiveIndicator.tsx`
- Modify: `frontend/src/App.tsx` (call `useInboxSocket`, render the indicator)
- Test: `frontend/src/components/LiveIndicator.test.tsx`, `frontend/src/live.test.tsx`

**Interfaces:**
- Consumes: `useInboxSocket` (Task 1).
- Produces: `LiveIndicator({ connected })` — a small dot + label: connected → green "ao vivo"; disconnected → amber "reconectando…". `App` calls `useInboxSocket(slug, effectiveInstanceId)` and renders `<LiveIndicator connected={...} />` in the contacts column header area (near/under the instance selector). When a live `message.created` arrives for the open contact, the conversation refetches (proven by the integration test).

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/src/components/LiveIndicator.test.tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { LiveIndicator } from "./LiveIndicator";

test("shows 'ao vivo' when connected", () => {
  render(<LiveIndicator connected={true} />);
  expect(screen.getByText(/ao vivo/i)).toBeInTheDocument();
});

test("shows 'reconectando' when disconnected", () => {
  render(<LiveIndicator connected={false} />);
  expect(screen.getByText(/reconectando/i)).toBeInTheDocument();
});
```

```tsx
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npm run test -- LiveIndicator live`
Expected: FAIL — `LiveIndicator` missing / App doesn't open a socket.

- [ ] **Step 3: Implement LiveIndicator + wire App**

```tsx
// frontend/src/components/LiveIndicator.tsx
export function LiveIndicator({ connected }: { connected: boolean }) {
  return (
    <div className="flex items-center gap-1.5 px-3 py-1 text-[11px] text-slate-500">
      <span
        className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-500" : "bg-amber-500"}`}
        aria-hidden
      />
      {connected ? "ao vivo" : "reconectando…"}
    </div>
  );
}
```

In `App.tsx`: import `useInboxSocket` + `LiveIndicator`, call the hook with the resolved slug + instance, and render the indicator under the instance selector. Add inside the component body (after `effectiveInstanceId` is computed):

```tsx
  const { connected } = useInboxSocket(slug, effectiveInstanceId);
```

Then change the `selector` node so the indicator renders right under the InstanceSelector. Replace the `selector` useMemo's returned JSX with a fragment:

```tsx
  const selector = useMemo(
    () => (
      <div>
        <InstanceSelector
          instances={instances.data ?? []}
          value={effectiveInstanceId}
          onChange={setInstanceId}
        />
        <LiveIndicator connected={connected} />
      </div>
    ),
    [instances.data, effectiveInstanceId, connected],
  );
```

> Confirm the current `App.tsx` `selector` useMemo shape before editing (3A/3B built it). Keep `setInstanceId` wired. The `connected` value must be in the `useMemo` dependency array so the indicator re-renders on connect/disconnect.

- [ ] **Step 4: Run the full suite + build**

Run: `cd frontend && npm run test`
Expected: ALL pass (LiveIndicator + live + all 3A/3B tests). Confirm **no `Errors`/`Uncaught` line** in the raw output. Then `npm run build` → tsc clean + bundle written.

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): wire live socket into App + ao-vivo indicator"
```

---

## Self-Review

**Spec coverage (operator-inbox spec §8 realtime → task):**
- WS client connects to `/ws/instances/{id}` → Tasks 1,2. Events `message.created`/`talk.updated`/`contact.updated` drive live updates → Task 1 (`applyInboxEvent`). Reconnect = REST refetch + `seq` dedup → Task 1. Connection-state feedback → Task 2 (LiveIndicator). ✓
- **Deferred (NOT 3C):** `message.status_updated` / delivery ✓✓ (Plano 2B-ii / Pedro) — unknown types are ignored gracefully. Server-side seq replay — intentionally not done (v1 = client refetch). Optimistic in-place insert from WS payload — not done (invalidate→refetch is the contract).

**Placeholder scan:** No "TBD". Two confirm-against-live-code notes: the `App.tsx` `selector` useMemo shape (Task 2 Step 3) and the exact 3A query keys (matched in Global Constraints). WS is mocked in tests (jsdom has no WS) — the FakeWS pattern is spelled out.

**Type consistency:** `useInboxSocket(slug, instanceId) -> {connected}` and `applyInboxEvent(qc, slug, env)` consistent across Task 1 (def) and Task 2 (use). `InboxEvent` fields match the backend envelope (`seq/type/instance_id/lead_id/payload`). Invalidation keys (`["messages"|"contact"|"contacts", slug, ...]`) match the 3A `useInbox.ts` keys and the 3B mutation-hook keys.

## Open items the implementer resolves against live code
1. Confirm the current `App.tsx` `selector` `useMemo` structure (3A/3B) and splice in the `LiveIndicator` without breaking the InstanceSelector wiring or its memo deps.
2. jsdom `WebSocket`: tests stub it; there is no real WS in unit tests. The real connection is exercised manually (the demo server) — a real-WS smoke is optional and not part of this plan's automated suite.
3. When Pedro's 2B-ii lands `message.status_updated`, extend `applyInboxEvent` with a case that invalidates `["messages", slug, leadId]` (delivery tick) — out of scope here, noted for continuity.
4. Chattiness: every `message.created` invalidates `["contacts", slug]` (a list refetch). Fine for a low-volume pilot; if it becomes noisy, debounce the contacts invalidation later (YAGNI now).
