# Chat Frontend 3B — Interactive Inbox (composer + takeover/release) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the read-only inbox into an operating tool: an operator can **take over** a conversation, **reply** (free-text WhatsApp message), and **release** it back to the AI — all wired to the backend HITL routes that already exist (Plano 2A).

**Architecture:** Pure frontend on the existing SPA (`frontend/`). Add `apiPost` to the api client, three TanStack `useMutation` hooks (takeover/release/send) with optimistic UI + cache invalidation, a `Composer` with the human-mode + 24h-window gate, and wire the (currently disabled) takeover/release buttons. No backend changes — the routes, idempotency, AI-suppression lock, and 24h handling are all done in Plano 2A.

**Tech Stack:** React 18 + TypeScript + TanStack Query 5 + Vitest/@testing-library (the 3A stack). Backend (unchanged): FastAPI routes under `/api/console/tenants/{slug}/contacts/{lead_id}/...`. Branch `dev/nicolas-chat-frontend` (3A merged into the branch; backend Planos 1+2A+2B-i present).

## Global Constraints

- **Backend is DONE — do not modify it.** The three routes (verbatim contracts):
  - `POST /api/console/tenants/{slug}/contacts/{lead_id}/takeover` → `200 {"talk_id": str, "handling_mode": "human"}` · `404` · `409 {"detail":"already human"}`
  - `POST .../release` → `200 {"talk_id": str, "handling_mode": "ai"}` · `404` · `409`
  - `POST .../send` body `{"text": str, "client_message_id": <uuid str>}` → `200 {"outbound_id": str, "external_id": str|null, "status": str}` · `404` · `409 {"detail":"take over the conversation first"}` · `422 {"detail":"24h window closed; template required"}` (or `"send failed: <Err>"`)
- **Auth/transport:** every request goes through the api client with `credentials:"include", redirect:"manual"`; an `opaqueredirect` or `401` → redirect to `/console/login` (existing behavior). `POST` adds `headers:{"content-type":"application/json"}` + JSON body.
- **`client_message_id`** is a fresh UUID generated in the browser per send (idempotency key the backend dedupes on). Use `crypto.randomUUID()`.
- **The composer gate (exact):** the operator can send **iff** `detail.state === "human"` **AND** `detail.window_open === true`. Otherwise the composer input is disabled with a reason:
  - `state !== "human"` → "Assuma a conversa para responder."
  - `state === "human" && !window_open` → "Janela de 24h fechada — envio por template em breve." (the template picker is Plano 2B-ii / Pedro's track — NOT in 3B).
- **No WebSocket in 3B** (that's 3C). After a successful send/takeover/release, reconcile by **invalidating** the relevant TanStack queries (refetch), so the real server state replaces optimistic UI.
- **Optimistic send:** on submit, append a temporary pending bubble (`_pending: true`) to the messages cache immediately; on error roll it back + surface the error; on settled invalidate the messages query (the refetch brings the real persisted message and drops the optimistic one).
- **`ContactState` vocab + types** are already in `frontend/src/types.ts` (3A). `ContactDetailOut` already has `state`, `window_open`, `window_expires_at`.
- **Read-only siblings stay disabled:** `DetailsSidebar`'s "Resolver"/"Reatribuir" have no backend → leave disabled. Only takeover/release/send get wired in 3B.
- **Working dir for frontend commands is `frontend/`.** Run tests with `npm run test` (Vitest, non-watch); build check `npm run build`.
- **Precondition (handle before Task 1):** the branch has an uncommitted fix in `src/ai_sdr/web/login.py` (session cookie `path` `/console`→`/`, load-bearing so the SPA at `/inbox` receives the cookie). Commit it first: `git add src/ai_sdr/web/login.py && git commit -m "fix(console): broaden session cookie path to / so the inbox SPA is authenticated"`. (No test asserts the old path; a test for the new path is optional.)
- **TDD; frequent commits.** Commit prefix `feat(chat-ui):`.

---

### Task 1: `apiPost` in the api client (+ error detail)

**Files:**
- Modify: `frontend/src/lib/apiClient.ts`
- Test: `frontend/src/lib/apiClient.test.ts` (extend)

**Interfaces:**
- Consumes: the existing `ApiError`, `_redirect`/`_setRedirect`.
- Produces: `apiPost<T>(path: string, body: unknown): Promise<T>` — `fetch(path, {method:"POST", credentials:"include", redirect:"manual", headers:{"content-type":"application/json"}, body: JSON.stringify(body)})`. `opaqueredirect`/`401` → `_redirect()` + throw `ApiError(401,...)`. On other non-2xx, read the JSON body's `detail` (fallback to status text) and throw `ApiError(res.status, detail)`. On 2xx, return parsed JSON (or `{}` if body empty). `ApiError.status` lets callers branch on 409/422.

- [ ] **Step 1: Write the failing tests**

```ts
// add to frontend/src/lib/apiClient.test.ts
import { apiPost } from "./apiClient";

describe("apiPost", () => {
  test("posts json and returns parsed body", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", f);
    await expect(apiPost<{ ok: number }>("/api/x", { a: 1 })).resolves.toEqual({ ok: 1 });
    expect(f).toHaveBeenCalledWith("/api/x", expect.objectContaining({
      method: "POST",
      credentials: "include",
      redirect: "manual",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ a: 1 }),
    }));
  });

  test("throws ApiError carrying status + detail on 409", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "already human" }), { status: 409 })));
    await expect(apiPost("/api/x", {})).rejects.toMatchObject({ status: 409, message: "already human" });
  });

  test("opaqueredirect triggers login redirect", async () => {
    const redirect = vi.fn();
    _setRedirect(redirect);
    const opaque = { type: "opaqueredirect", status: 0, ok: false } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn(async () => opaque));
    await expect(apiPost("/api/x", {})).rejects.toBeInstanceOf(ApiError);
    expect(redirect).toHaveBeenCalledOnce();
  });
});
```

> Ensure `_setRedirect` and `ApiError` are imported at the top of the test file (3A already imports them).

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- apiClient`
Expected: FAIL — `apiPost` not exported.

- [ ] **Step 3: Implement `apiPost`**

```ts
// append to frontend/src/lib/apiClient.ts
export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "include",
    redirect: "manual",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.type === "opaqueredirect" || res.status === 401) {
    _redirect();
    throw new ApiError(401, "unauthenticated");
  }
  if (!res.ok) {
    let detail = `POST ${path} -> ${res.status}`;
    try {
      const data = (await res.json()) as { detail?: string };
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      // non-JSON error body; keep the default detail
    }
    throw new ApiError(res.status, detail);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm run test -- apiClient`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): apiPost (json POST, status+detail on error)"
```

---

### Task 2: Mutation hooks — takeover / release / send

**Files:**
- Create: `frontend/src/hooks/useInboxMutations.ts`
- Test: `frontend/src/hooks/useInboxMutations.test.tsx`

**Interfaces:**
- Consumes: `apiPost` (Task 1), `MessageOut` type, `useQueryClient`/`useMutation`.
- Produces:
  - `useTakeover(slug, leadId)` → `useMutation` that `POST .../takeover`; `onSuccess` invalidates `["contact", slug, leadId]` + `["contacts", slug]` (prefix match → all instances/filters).
  - `useRelease(slug, leadId)` → symmetric (`.../release`).
  - `useSend(slug, leadId)` → `mutationFn({text})` generates `client_message_id = crypto.randomUUID()` and `POST .../send {text, client_message_id}`. **Optimistic:** `onMutate` cancels + snapshots `["messages", slug, leadId]`, appends a pending `OptimisticMessage` (`{id: client_message_id, direction:"out", origin:"operator", text, media_type:"text", audio_url:null, transcription:null, at:<now ISO>, _pending:true}`); `onError` rolls back to the snapshot; `onSettled` invalidates `["messages", slug, leadId]`.
  - export type `OptimisticMessage = MessageOut & { _pending?: boolean }`.

- [ ] **Step 1: Write the failing test**

```tsx
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
  const body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
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
  expect(String(f.mock.calls[0][0])).toBe("/api/console/tenants/acme/contacts/l1/takeover");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- useInboxMutations`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the hooks**

```ts
// frontend/src/hooks/useInboxMutations.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPost } from "../lib/apiClient";
import type { MessageOut } from "../types";

const base = (slug: string, leadId: string) => `/api/console/tenants/${slug}/contacts/${leadId}`;

export type OptimisticMessage = MessageOut & { _pending?: boolean };

export function useTakeover(slug: string, leadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost(`${base(slug, leadId)}/takeover`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
      qc.invalidateQueries({ queryKey: ["contacts", slug] });
    },
  });
}

export function useRelease(slug: string, leadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost(`${base(slug, leadId)}/release`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
      qc.invalidateQueries({ queryKey: ["contacts", slug] });
    },
  });
}

export function useSend(slug: string, leadId: string) {
  const qc = useQueryClient();
  const key = ["messages", slug, leadId];
  return useMutation({
    mutationFn: ({ text }: { text: string }) => {
      const client_message_id = crypto.randomUUID();
      return apiPost(`${base(slug, leadId)}/send`, { text, client_message_id });
    },
    onMutate: async ({ text }: { text: string }) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<OptimisticMessage[]>(key) ?? [];
      const optimistic: OptimisticMessage = {
        id: crypto.randomUUID(),
        direction: "out",
        origin: "operator",
        text,
        media_type: "text",
        audio_url: null,
        transcription: null,
        at: new Date().toISOString(),
        _pending: true,
      };
      qc.setQueryData<OptimisticMessage[]>(key, [...previous, optimistic]);
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) qc.setQueryData(key, context.previous);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: key });
    },
  });
}
```

> `new Date().toISOString()` is allowed in the browser/jsdom (this is app code, not a workflow script). `crypto.randomUUID()` exists in Node 20+/jsdom under Vitest.

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm run test -- useInboxMutations`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): takeover/release/send mutation hooks (optimistic send)"
```

---

### Task 3: Composer component (gated send)

**Files:**
- Create: `frontend/src/components/Composer.tsx`
- Test: `frontend/src/components/Composer.test.tsx`

**Interfaces:**
- Consumes: `useSend` (Task 2), `ContactState`.
- Produces: `Composer({ slug, leadId, state, windowOpen })` — a textarea + send button. **Gate:** enabled iff `state === "human" && windowOpen`. When disabled, render a muted hint (`state !== "human"` → "Assuma a conversa para responder."; `state === "human" && !windowOpen` → "Janela de 24h fechada — envio por template em breve."). On submit (button click or Enter without Shift): call `useSend.mutate({text})`, clear the textarea optimistically; if the mutation errors, restore nothing but show an inline error message (read from the thrown `ApiError.message`, e.g. the 409/422 detail). Disable the send button while `isPending` and when the trimmed text is empty.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/Composer.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Composer } from "./Composer";

afterEach(() => vi.restoreAllMocks());

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

test("disabled with hint when not human", () => {
  wrap(<Composer slug="acme" leadId="l1" state="ai" windowOpen={true} />);
  expect(screen.getByText(/Assuma a conversa/)).toBeInTheDocument();
  expect(screen.getByTestId("composer-input")).toBeDisabled();
});

test("disabled with hint when 24h window closed", () => {
  wrap(<Composer slug="acme" leadId="l1" state="human" windowOpen={false} />);
  expect(screen.getByText(/Janela de 24h fechada/)).toBeInTheDocument();
  expect(screen.getByTestId("composer-input")).toBeDisabled();
});

test("sends when human + window open, then clears", async () => {
  const f = vi.fn(async () => new Response(JSON.stringify({ outbound_id: "o1", external_id: "x", status: "sent" }), { status: 200 }));
  vi.stubGlobal("fetch", f);
  wrap(<Composer slug="acme" leadId="l1" state="human" windowOpen={true} />);
  const input = screen.getByTestId("composer-input") as HTMLTextAreaElement;
  await userEvent.type(input, "olá mundo");
  await userEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(f).toHaveBeenCalled());
  expect(String(f.mock.calls[0][0])).toContain("/contacts/l1/send");
  await waitFor(() => expect(input.value).toBe(""));
});

test("shows the backend error detail on failure", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "24h window closed; template required" }), { status: 422 })));
  wrap(<Composer slug="acme" leadId="l1" state="human" windowOpen={true} />);
  await userEvent.type(screen.getByTestId("composer-input"), "oi");
  await userEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(screen.getByText(/24h window closed/)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- Composer`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the Composer**

```tsx
// frontend/src/components/Composer.tsx
import { useState } from "react";
import { Send } from "lucide-react";
import { useSend } from "../hooks/useInboxMutations";
import type { ContactState } from "../types";

export function Composer({
  slug,
  leadId,
  state,
  windowOpen,
}: {
  slug: string;
  leadId: string;
  state: ContactState;
  windowOpen: boolean;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const send = useSend(slug, leadId);

  const enabled = state === "human" && windowOpen;
  const hint =
    state !== "human"
      ? "Assuma a conversa para responder."
      : !windowOpen
        ? "Janela de 24h fechada — envio por template em breve."
        : null;

  function submit() {
    const t = text.trim();
    if (!t || !enabled || send.isPending) return;
    setError(null);
    const sent = t;
    setText(""); // optimistic clear
    send.mutate(
      { text: sent },
      { onError: (e: unknown) => setError(e instanceof Error ? e.message : "Falha ao enviar.") },
    );
  }

  return (
    <div className="border-t border-slate-200 bg-white px-3 py-2.5">
      {error && <div className="mb-1 text-xs text-red-500">{error}</div>}
      <div className="flex items-end gap-2">
        <textarea
          data-testid="composer-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          disabled={!enabled}
          rows={1}
          placeholder={enabled ? "Escreva uma mensagem…" : (hint ?? "")}
          className="max-h-32 min-h-9 flex-1 resize-none rounded-md bg-slate-100 px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-accent disabled:opacity-60"
        />
        <button
          data-testid="composer-send"
          onClick={submit}
          disabled={!enabled || send.isPending || text.trim() === ""}
          className="grid h-9 w-9 place-items-center rounded-md bg-accent text-white disabled:opacity-40"
          title="Enviar"
        >
          <Send size={16} />
        </button>
      </div>
      {!enabled && hint && <div className="mt-1 text-[11px] text-slate-400">{hint}</div>}
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm run test -- Composer`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): Composer with human + 24h-window gate"
```

---

### Task 4: Wire takeover/release into ConversationHeader

**Files:**
- Modify: `frontend/src/components/ConversationHeader.tsx`
- Test: `frontend/src/components/ConversationHeader.test.tsx` (create)

**Interfaces:**
- Consumes: `useTakeover`, `useRelease` (Task 2), `ContactDetailOut`.
- Produces: `ConversationHeader({ detail, slug })` — the buttons are now **enabled and wired**. When `detail.state` is `ai`/`requires_review`/`awaiting` → show **Assumir** (enabled, calls `useTakeover`); when `state === "human"` → show **Devolver pra IA** (enabled, calls `useRelease`). Show the other button disabled (so layout is stable), or hide it — keep both rendered with the inactive one disabled. While a mutation `isPending`, disable both and show a subtle "…". On a 409 (conflict), the `onSuccess`-invalidate path won't fire; surface nothing intrusive — the `useTakeover`/`useRelease` already invalidate on success, and a 409 just means the server state differs, so trigger a `detail` refetch by calling the mutation's `onError` to invalidate `["contact", slug, leadId]` (reconcile silently).

- [ ] **Step 1: Write the failing test**

```tsx
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
  await waitFor(() => expect(String(f.mock.calls[0][0])).toContain("/contacts/l1/takeover"));
});

test("human state → Devolver is enabled and calls release", async () => {
  const f = vi.fn(async () => new Response(JSON.stringify({ talk_id: "t1", handling_mode: "ai" }), { status: 200 }));
  vi.stubGlobal("fetch", f);
  wrap(<ConversationHeader detail={{ ...base, state: "human" }} slug="acme" />);
  const btn = screen.getByTestId("btn-release");
  expect(btn).toBeEnabled();
  await userEvent.click(btn);
  await waitFor(() => expect(String(f.mock.calls[0][0])).toContain("/contacts/l1/release"));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- ConversationHeader`
Expected: FAIL — the 3A header had no `slug` prop and disabled buttons.

- [ ] **Step 3: Rewrite the header**

```tsx
// frontend/src/components/ConversationHeader.tsx
import { useTakeover, useRelease } from "../hooks/useInboxMutations";
import type { ContactDetailOut } from "../types";
import { StateBadge } from "./StateBadge";

export function ConversationHeader({ detail, slug }: { detail: ContactDetailOut; slug: string }) {
  const leadId = detail.lead_id;
  const takeover = useTakeover(slug, leadId);
  const release = useRelease(slug, leadId);
  const isHuman = detail.state === "human";
  const pending = takeover.isPending || release.isPending;
  const name = detail.display_name || detail.whatsapp_e164 || "Contato";

  return (
    <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2.5">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-slate-800">{name}</span>
        <StateBadge state={detail.state} />
      </div>
      <div className="flex items-center gap-2">
        <button
          data-testid="btn-takeover"
          onClick={() => takeover.mutate()}
          disabled={isHuman || pending}
          className="rounded bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
        >
          {takeover.isPending ? "Assumindo…" : "Assumir"}
        </button>
        <button
          data-testid="btn-release"
          onClick={() => release.mutate()}
          disabled={!isHuman || pending}
          className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600 disabled:opacity-40"
        >
          {release.isPending ? "Devolvendo…" : "Devolver pra IA"}
        </button>
      </div>
    </header>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm run test -- ConversationHeader`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): wire takeover/release in the conversation header"
```

---

### Task 5: Render Composer + pending bubbles in ConversationView; full B1 flow + build

**Files:**
- Modify: `frontend/src/components/ConversationView.tsx` (render `Composer`, pass `slug` to header)
- Modify: `frontend/src/components/MessageBubble.tsx` (render the `_pending` state)
- Modify: `frontend/src/App.tsx` (pass `slug` where `ConversationView` is rendered — confirm signature)
- Test: `frontend/src/components/ConversationView.test.tsx` (extend), `frontend/src/interactive.test.tsx` (create)

**Interfaces:**
- Consumes: `Composer` (Task 3), the mutation hooks, `OptimisticMessage`.
- Produces: `ConversationView` renders the real `Composer` (replacing the "Composer chega no 3B" placeholder) wired with `detail.state` + `detail.window_open`, and passes `slug` to `ConversationHeader`. `MessageBubble` accepts an optional `_pending` (via the `OptimisticMessage` shape) and renders a faint "enviando…" marker instead of a timestamp when pending. `MessageStream` already maps messages — it tolerates the extra `_pending` field (structural).

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/src/interactive.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ConversationView } from "./components/ConversationView";

afterEach(() => vi.restoreAllMocks());

function mockRoutes() {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/contacts/l1/messages")) return new Response("[]", { status: 200 });
    if (url.includes("/contacts/l1/talks")) return new Response("[]", { status: 200 });
    if (url.includes("/contacts/l1/send")) return new Response(JSON.stringify({ outbound_id: "o1", external_id: "x", status: "sent" }), { status: 200 });
    if (url.endsWith("/contacts/l1")) return new Response(JSON.stringify({ lead_id: "l1", display_name: "Ana", whatsapp_e164: "+55", state: "human", funnel_node: "q", active_talk_id: "t1", ai_reasoning: null, window_open: true, window_expires_at: null }), { status: 200 });
    return new Response("{}", { status: 200 });
    void init;
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
  // optimistic pending bubble shows immediately
  await userEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(screen.getByText("oi do operador")).toBeInTheDocument());
});
```

Also extend `ConversationView.test.tsx` (the 3A test that asserted `btn-takeover` disabled) — that assertion is now WRONG (the button is enabled in 3B). Update it to assert the composer renders:

```tsx
// in frontend/src/components/ConversationView.test.tsx — replace the disabled-takeover assertion
  expect(screen.getByTestId("composer-input")).toBeInTheDocument();
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- interactive ConversationView`
Expected: FAIL — Composer not rendered / `btn-takeover` no longer disabled.

- [ ] **Step 3: Implement**

In `MessageBubble.tsx`, change the type to accept the optimistic shape and render pending:

```tsx
// frontend/src/components/MessageBubble.tsx — update the import + signature + footer
import type { MessageOut } from "../types";
import { formatTime } from "../lib/format";

type BubbleMsg = MessageOut & { _pending?: boolean };

export function MessageBubble({ msg }: { msg: BubbleMsg }) {
  const out = msg.direction === "out";
  const senderLabel = msg.origin === "operator" ? "Você" : msg.origin === "ai" ? "IA" : null;
  return (
    <div className={`flex ${out ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[72%] rounded-lg px-3 py-2 text-sm shadow-sm ${out ? "bg-accent/10 text-slate-800" : "bg-white text-slate-800"}`}>
        {senderLabel && <div className="mb-0.5 text-[11px] font-semibold text-accent">{senderLabel}</div>}
        {msg.media_type === "audio" ? (
          <div className="space-y-1">
            {msg.audio_url && <audio controls src={msg.audio_url} className="h-8" />}
            {msg.transcription && <div className="text-xs italic text-slate-500">“{msg.transcription}”</div>}
          </div>
        ) : msg.media_type === "unsupported" ? (
          <div className="text-xs italic text-slate-400">mensagem não suportada (tipo: {msg.media_type})</div>
        ) : (
          <div className="whitespace-pre-wrap">{msg.text}</div>
        )}
        <div className="mt-1 text-right text-[10px] text-slate-400">
          {msg._pending ? "enviando…" : formatTime(msg.at)}
        </div>
      </div>
    </div>
  );
}
```

In `ConversationView.tsx`, render the header with `slug` and the real `Composer` (drop the placeholder):

```tsx
// frontend/src/components/ConversationView.tsx
import { useContactDetail, useMessages, useTalks } from "../hooks/useInbox";
import { ConversationHeader } from "./ConversationHeader";
import { MessageStream } from "./MessageStream";
import { Composer } from "./Composer";

export function ConversationView({ slug, leadId }: { slug: string | undefined; leadId: string }) {
  const detail = useContactDetail(slug, leadId);
  const messages = useMessages(slug, leadId);
  const talks = useTalks(slug, leadId);

  if (detail.isLoading) {
    return <div className="grid h-full place-items-center text-slate-400">Carregando conversa…</div>;
  }
  if (!detail.data) {
    return <div className="grid h-full place-items-center text-slate-400">Conversa indisponível.</div>;
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <ConversationHeader detail={detail.data} slug={slug ?? ""} />
      <MessageStream messages={messages.data ?? []} talks={talks.data ?? []} />
      <Composer
        slug={slug ?? ""}
        leadId={leadId}
        state={detail.data.state}
        windowOpen={detail.data.window_open}
      />
    </div>
  );
}
```

> `App.tsx` already renders `<ConversationView slug={slug} leadId={leadId} />` (3A) — no change needed there beyond confirming the prop names match. If `MessageStream`'s prop type is strictly `MessageOut[]`, the optimistic `OptimisticMessage[]` is assignable (it's a superset) since the messages cache may now hold pending items; if tsc complains, widen `MessageStream`'s `messages` prop to `(MessageOut & { _pending?: boolean })[]`.

- [ ] **Step 4: Run the B1 suite + full suite + build**

Run: `cd frontend && npm run test`
Expected: ALL pass (apiClient, useInboxMutations, Composer, ConversationHeader, interactive, ConversationView, + all 3A tests). Confirm there is **no "Errors N error"** line (a swallowed render crash). Then `npm run build` → tsc clean + bundle written.

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): render Composer + pending bubbles; interactive inbox end-to-end"
```

---

## Self-Review

**Spec coverage (operator-inbox spec §5/§7 → task):**
- Operator can take over / release → Tasks 2,4. Operator can reply (free text) → Tasks 2,3,5. `client_message_id` idempotency → Task 2. 24h-window gate on the composer → Task 3. Optimistic send + failure surfaced → Tasks 2,3,5. State badge reflects human after takeover (via invalidate→refetch) → Task 2/4. ✓
- **Deferred (NOT 3B):** template picker for closed window (Plano 2B-ii / Pedro); live updates without refetch (Plano 3C); delivery ✓✓ (2B-ii). The composer's closed-window path shows a "template em breve" hint instead of a picker — intended.

**Placeholder scan:** No "TBD". One precondition names a concrete fix to commit first (the `login.py` cookie path). The MessageStream prop-widening note is conditional with the exact type to use.

**Type consistency:** `useTakeover/useRelease/useSend(slug, leadId)` signatures consistent across Tasks 2/3/4/5. `OptimisticMessage = MessageOut & {_pending?}` defined in Task 2, consumed in Task 5. `ConversationHeader({detail, slug})` (Task 4) matches the render in Task 5. `Composer({slug, leadId, state, windowOpen})` (Task 3) matches the render in Task 5. Query keys `["messages"|"contact"|"contacts", slug, ...]` match the 3A hooks in `useInbox.ts`.

## Open items the implementer resolves against live code
1. Confirm `crypto.randomUUID()` is available under the repo's jsdom/Vitest (Node 22 — yes); if a test env lacks it, stub `crypto.randomUUID` in the test setup.
2. The 3A `ConversationView.test.tsx` asserted `btn-takeover` is **disabled** — that assertion is now false (3B enables it). Update it (Task 5 Step 1) — don't leave the stale assertion.
3. If `MessageStream`/`MessageBubble` strict types reject the optimistic `_pending` field, widen the prop type as noted (Task 5 Step 3), not by casting away types.
4. After takeover/release, reconciliation is via query invalidation (refetch) — there is no WS yet (3C). Confirm the contact-detail refetch flips `state` to `human` so the composer enables in the same session without a manual reload.
