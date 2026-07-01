# Chat Frontend 3A — Scaffold + Read-Only Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the operator Chat SPA and the read-only inbox: log in, pick an instance, see the contact list (with state badges + filters), open a contact, and read the conversation (continuous stream with Talk delimiters) — no actions, no realtime yet.

**Architecture:** A Vite + React + TypeScript SPA (Tailwind, hand-rolled chat bubbles, TanStack Query, targeted Radix for the instance dropdown) lives in `frontend/`, built to `src/ai_sdr/web/static/inbox/` and served by the same FastAPI app via `StaticFiles` at `/inbox`. The SPA consumes the existing tenant-scoped read API (`/api/console/tenants/{slug}/...`, Planos 1/2A) using the existing session cookie; two small read endpoints are added (`/me` bootstrap, `/contacts/{lead_id}/talks` for delimiters). Interactivity (3B) and realtime (3C) are separate plans.

**Tech Stack:** Vite 5, React 18, TypeScript 5, Tailwind CSS 3, @radix-ui/react-dropdown-menu, lucide-react, @tanstack/react-query 5, Vitest + @testing-library/react + jsdom. Backend: FastAPI (existing), pytest. Node 20, npm. Builds on branch `dev/nicolas-chat-frontend` (backend Planos 1+2A+2B-i merged; DB at `0036`).

## Global Constraints

- **Contact-based model:** the list is anchored on the **Contact (Lead)**; the conversation is the continuous message stream; **Talks are inline delimiters**. A contact with no active Talk still renders (`awaiting`/`closed`).
- **API is tenant-scoped:** every data call is `/api/console/tenants/{slug}/...`. The SPA learns its `slug` from `GET /api/console/me`. v1 is **single-tenant-context** (one tenant active at a time; cross-tenant is platform-admin only, later).
- **Auth = existing session cookie.** All `fetch` calls use `credentials: "include"`. A `401` from any API call means not-authenticated → redirect the browser to `/console/login`. No new auth system.
- **Exact API response types (mirror verbatim in `frontend/src/types.ts`):**
  - `ContactState = "ai" | "requires_review" | "human" | "awaiting" | "closed"`
  - `InstanceOut { id: string; channel_label: string; display_name: string | null; phone_e164: string | null }`
  - `ContactOut { lead_id: string; display_name: string | null; whatsapp_e164: string | null; last_message_at: string | null; last_message_preview: string | null; state: ContactState; funnel_node: string | null; unread: number }`
  - `MessageOut { id: string; direction: "in" | "out"; origin: "lead" | "ai" | "operator"; text: string | null; media_type: string; audio_url: string | null; transcription: string | null; at: string }`
  - `ContactDetailOut { lead_id: string; display_name: string | null; whatsapp_e164: string | null; state: ContactState; funnel_node: string | null; active_talk_id: string | null; ai_reasoning: string | null; window_open: boolean; window_expires_at: string | null }`
- **Avelum brand tokens (CSS vars, applied lightly — the chat keeps a WhatsApp-Web feel):** `--accent:#0057FF; --accent-action:#0038FF; --teal:#44CDCE; --brand-gradient:linear-gradient(52deg,#0057FF,#1A63F7,#F9C5B4); --font-display:'Syne'; --font-body:'Madefor Text',system-ui;`. Accent blue on selection/header/badge; teal on funnel tag. Logo Avelum is a **gradient placeholder** (real SVG asset pending — do not block on it).
- **Read-only:** NO send/takeover/release/composer in 3A. Action affordances may render **disabled** (wired in 3B). NO WebSocket in 3A (3C).
- **State badges vocab:** `ai`→🟢 IA, `requires_review`→🟠 Revisão, `human`→🔵 Humano, `awaiting`→🆕 Aguardando, `closed`→⚪ Encerrada.
- **Working dir for frontend commands is `frontend/`.** Run backend tests with `uv run pytest ...` from repo root. Run frontend tests with `npm run test` (Vitest, non-watch) from `frontend/`.
- **TDD; frequent commits.** Backend commits: `feat(chat-api): …`; frontend commits: `feat(chat-ui): …`.

---

### Task 1: Backend bootstrap endpoint `GET /api/console/me`

**Files:**
- Create: `src/ai_sdr/api/routes/console_me.py`
- Modify: `src/ai_sdr/main.py` (register the router)
- Create test: `tests/integration/test_console_me.py`

**Interfaces:**
- Produces: `GET /api/console/me` (behind cookie auth, NOT tenant-scoped) → `{"user": {"id": str, "username": str}, "tenants": [{"slug": str, "display_name": str}]}` — the authenticated operator + every tenant they have a `UserTenantAccess` row for (platform admins get all tenants). 401 if no/invalid cookie. This is how the SPA discovers its `slug`(s).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_console_me.py
"""GET /api/console/me returns the authed user + their accessible tenants.
Mirrors the cookie-auth seeding of tests/integration/test_console_leads_page.py."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_me_returns_user_and_tenants(authed_inbox_client):
    client, ctx = authed_inbox_client
    resp = await client.get("/api/console/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["username"] == ctx["user"].username
    slugs = [t["slug"] for t in body["tenants"]]
    assert ctx["slug"] in slugs


async def test_me_unauthenticated_redirects(app):
    from httpx import ASGITransport, AsyncClient

    # require_console_user REDIRECTS to /console/login on missing cookie
    # (it does NOT return 401). Assert the redirect, with follow disabled.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        resp = await client.get("/api/console/me")
    assert resp.status_code in (302, 303, 307)
    assert "/console/login" in resp.headers.get("location", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_console_me.py -q`
Expected: FAIL — route 404.

- [ ] **Step 3: Implement the endpoint**

```python
# src/ai_sdr/api/routes/console_me.py
"""Bootstrap endpoint: who am I + which tenants can I access.

The SPA calls this first to discover its tenant slug(s); every other data
call is tenant-scoped at /api/console/tenants/{slug}/...
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import db_session
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.web.auth import require_console_user

router = APIRouter(prefix="/api/console")


class TenantBrief(BaseModel):
    slug: str
    display_name: str


class MeOut(BaseModel):
    user: dict
    tenants: list[TenantBrief]


@router.get("/me", response_model=MeOut)
async def get_me(
    user: Annotated[User, Depends(require_console_user)],
    db: Annotated[AsyncSession, Depends(db_session)],
) -> MeOut:
    if getattr(user, "is_platform_admin", False):
        rows = (await db.execute(select(Tenant))).scalars().all()
    else:
        rows = (
            await db.execute(
                select(Tenant)
                .join(UserTenantAccess, UserTenantAccess.tenant_id == Tenant.id)
                .where(UserTenantAccess.user_id == user.id)
            )
        ).scalars().all()
    return MeOut(
        user={"id": str(user.id), "username": user.username},
        tenants=[TenantBrief(slug=t.slug, display_name=t.display_name) for t in rows],
    )
```

Then register it in `src/ai_sdr/main.py` `create_app()` alongside the other routers:

```python
    from ai_sdr.api.routes.console_me import router as console_me_router
    app.include_router(console_me_router)
```

> `require_console_user` exists in `ai_sdr.web.auth` (the cookie-auth dependency `require_tenant_access` builds on). CONFIRMED behavior: on missing/invalid cookie it **redirects to `/console/login`** (raises `_redirect_to_login()`, a 3xx) — it does NOT return 401. The SPA handles this in Task 4 via `fetch(redirect:"manual")` + `opaqueredirect` detection. Keep the redirect test as written.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_console_me.py -q`
Expected: PASS (adjust the unauth status assertion to the real convention if needed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/api/routes/console_me.py src/ai_sdr/main.py tests/integration/test_console_me.py
git commit -m "feat(chat-api): GET /api/console/me bootstrap (user + accessible tenants)"
```

---

### Task 2: Frontend scaffold (Vite + React + TS + Tailwind + Vitest + Avelum theme)

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/index.html`, `frontend/postcss.config.js`, `frontend/tailwind.config.ts`, `frontend/vitest.config.ts`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/index.css`, `frontend/src/theme.css`, `frontend/src/test/setup.ts`
- Create test: `frontend/src/App.test.tsx`
- Create: `frontend/.gitignore`

**Interfaces:**
- Produces: a runnable Vite app (`npm run dev`), a production build (`npm run build` → `../src/ai_sdr/web/static/inbox/`), and a green Vitest smoke (`npm run test`). The Avelum CSS vars are global.

- [ ] **Step 1: Create the project config files**

```jsonc
// frontend/package.json
{
  "name": "pesdr-chat-inbox",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@radix-ui/react-dropdown-menu": "^2.1.2",
    "@tanstack/react-query": "^5.59.0",
    "lucide-react": "^0.454.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.5.0",
    "@testing-library/react": "^16.0.1",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.11",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.2",
    "autoprefixer": "^10.4.20",
    "jsdom": "^25.0.1",
    "postcss": "^8.4.47",
    "tailwindcss": "^3.4.13",
    "typescript": "^5.6.2",
    "vite": "^5.4.8",
    "vitest": "^2.1.2"
  }
}
```

```ts
// frontend/vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build into the FastAPI static dir; SPA is served at /inbox.
export default defineConfig({
  base: "/inbox/",
  plugins: [react()],
  build: {
    outDir: "../src/ai_sdr/web/static/inbox",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
```

```jsonc
// frontend/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2021",
    "useDefineForClassFields": true,
    "lib": ["ES2021", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

```jsonc
// frontend/tsconfig.node.json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "noEmit": true
  },
  "include": ["vite.config.ts", "vitest.config.ts"]
}
```

```ts
// frontend/vitest.config.ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
```

```js
// frontend/postcss.config.js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

```ts
// frontend/tailwind.config.ts
import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: "var(--accent)",
        "accent-action": "var(--accent-action)",
        teal: "var(--teal)",
      },
      fontFamily: {
        display: ["var(--font-display)"],
        body: ["var(--font-body)"],
      },
    },
  },
  plugins: [],
} satisfies Config;
```

```html
<!-- frontend/index.html -->
<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Avelum Labs — Inbox</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

```
# frontend/.gitignore
node_modules
dist
```

- [ ] **Step 2: Create the theme + entry + smoke App**

```css
/* frontend/src/theme.css */
:root {
  --accent: #0057ff;
  --accent-action: #0038ff;
  --teal: #44cdce;
  --brand-gradient: linear-gradient(52deg, #0057ff, #1a63f7, #f9c5b4);
  --font-display: "Syne", system-ui, sans-serif;
  --font-body: "Madefor Text", system-ui, sans-serif;
}
```

```css
/* frontend/src/index.css */
@tailwind base;
@tailwind components;
@tailwind utilities;

html, body, #root { height: 100%; margin: 0; }
body { font-family: var(--font-body); }
```

```tsx
// frontend/src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./lib/queryClient";
import App from "./App";
import "./theme.css";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
```

```tsx
// frontend/src/App.tsx
export default function App() {
  return (
    <div className="h-full grid place-items-center text-slate-500">
      <span data-testid="app-boot">Avelum Labs — Inbox</span>
    </div>
  );
}
```

> `main.tsx` imports `./lib/queryClient` which is created in Task 4. To keep Task 2 self-contained and buildable NOW, create a minimal stub `frontend/src/lib/queryClient.ts` here:
> ```ts
> import { QueryClient } from "@tanstack/react-query";
> export const queryClient = new QueryClient();
> ```
> Task 4 replaces it with the configured client.

- [ ] **Step 3: Create the test setup + smoke test**

```ts
// frontend/src/test/setup.ts
import "@testing-library/jest-dom/vitest";
```

```tsx
// frontend/src/App.test.tsx
import { render, screen } from "@testing-library/react";
import App from "./App";

test("App boots and renders the brand wordmark", () => {
  render(<App />);
  expect(screen.getByTestId("app-boot")).toHaveTextContent("Avelum Labs");
});
```

- [ ] **Step 4: Install + run the smoke test + build**

```bash
cd frontend && npm install
npm run test
npm run build
```
Expected: `npm run test` → 1 passed; `npm run build` → writes `../src/ai_sdr/web/static/inbox/index.html` + assets.

> Add `src/ai_sdr/web/static/inbox/` to the repo `.gitignore` (build artifact) — do NOT commit the build output. From repo root: append `src/ai_sdr/web/static/inbox/` to `.gitignore`.

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend .gitignore && git commit -m "feat(chat-ui): Vite+React+TS+Tailwind scaffold + Avelum theme + vitest smoke"
```

---

### Task 3: Serve the built SPA from FastAPI (`/inbox`)

**Files:**
- Modify: `src/ai_sdr/main.py` (mount StaticFiles at `/inbox` when the build dir exists)
- Create test: `tests/integration/test_inbox_static_mount.py`

**Interfaces:**
- Produces: when `src/ai_sdr/web/static/inbox/index.html` exists, the app serves the SPA at `/inbox` (and `/inbox/` returns the index). When it doesn't exist (e.g. CI without a frontend build), the app boots fine and `/inbox` is simply absent — the mount is conditional.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_inbox_static_mount.py
"""When a built SPA exists, FastAPI serves it at /inbox; otherwise the app
still boots and the route is absent (conditional mount)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_inbox_mounted_when_build_exists(tmp_path, monkeypatch):
    from ai_sdr import main as main_mod

    build = tmp_path / "inbox"
    build.mkdir()
    (build / "index.html").write_text("<!doctype html><title>x</title>")
    monkeypatch.setattr(main_mod, "_inbox_static_dir", lambda: build)

    app = main_mod.create_app()
    paths = [getattr(r, "path", "") for r in app.routes]
    assert any(p.startswith("/inbox") for p in paths)


def test_app_boots_without_build(monkeypatch):
    from ai_sdr import main as main_mod

    monkeypatch.setattr(main_mod, "_inbox_static_dir", lambda: Path("/nonexistent/inbox"))
    app = main_mod.create_app()  # must not raise
    assert app is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_inbox_static_mount.py -q`
Expected: FAIL — `_inbox_static_dir` not defined.

- [ ] **Step 3: Implement the conditional mount**

In `src/ai_sdr/main.py`, add near the top:

```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles


def _inbox_static_dir() -> Path:
    # src/ai_sdr/main.py -> src/ai_sdr/web/static/inbox
    return Path(__file__).parent / "web" / "static" / "inbox"
```

In `create_app()`, after the `include_router(...)` calls and before `return app`:

```python
    inbox_dir = _inbox_static_dir()
    if (inbox_dir / "index.html").exists():
        app.mount("/inbox", StaticFiles(directory=str(inbox_dir), html=True), name="inbox")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_inbox_static_mount.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/main.py tests/integration/test_inbox_static_mount.py
git commit -m "feat(chat-api): conditionally serve the built inbox SPA at /inbox"
```

---

### Task 4: API client + types + TanStack query client + data hooks

**Files:**
- Create: `frontend/src/types.ts`, `frontend/src/lib/apiClient.ts`, `frontend/src/lib/queryClient.ts` (replace stub), `frontend/src/hooks/useInbox.ts`
- Create test: `frontend/src/lib/apiClient.test.ts`, `frontend/src/hooks/useInbox.test.tsx`

**Interfaces:**
- Consumes: the read API (`/api/console/me`, `/api/console/tenants/{slug}/instances`, `/instances/{id}/contacts`, `/contacts/{lead_id}`, `/contacts/{lead_id}/messages`) and the types from Global Constraints.
- Produces:
  - `apiGet<T>(path: string): Promise<T>` — `fetch(path, {credentials:"include", redirect:"manual"})`. **Unauthenticated detection:** the backend `require_console_user` REDIRECTS (3xx) to `/console/login` on auth failure (it does NOT return 401), so with `redirect:"manual"` that surfaces as `res.type === "opaqueredirect"` (status 0). Treat `res.type === "opaqueredirect"` OR `res.status === 401` as unauthenticated → call `redirectToLogin()` and reject with `ApiError`. On other non-2xx throw `ApiError`. `redirectToLogin()` sets `window.location.href = "/console/login"` (exported + overridable for tests via `_setRedirect`).
  - `queryClient` — a configured `QueryClient` (no refetch-on-focus storm: `staleTime: 10_000`, `retry: 1`).
  - hooks: `useMe()`, `useInstances(slug)`, `useContacts(slug, instanceId, filters)`, `useContactDetail(slug, leadId)`, `useMessages(slug, leadId)` — thin `useQuery` wrappers returning the typed data. `filters = { status?: ContactState | "all"; funnel?: string; q?: string }`.

- [ ] **Step 1: Write the types + failing apiClient test**

```ts
// frontend/src/types.ts
export type ContactState = "ai" | "requires_review" | "human" | "awaiting" | "closed";

export interface MeOut {
  user: { id: string; username: string };
  tenants: { slug: string; display_name: string }[];
}
export interface InstanceOut {
  id: string;
  channel_label: string;
  display_name: string | null;
  phone_e164: string | null;
}
export interface ContactOut {
  lead_id: string;
  display_name: string | null;
  whatsapp_e164: string | null;
  last_message_at: string | null;
  last_message_preview: string | null;
  state: ContactState;
  funnel_node: string | null;
  unread: number;
}
export interface MessageOut {
  id: string;
  direction: "in" | "out";
  origin: "lead" | "ai" | "operator";
  text: string | null;
  media_type: string;
  audio_url: string | null;
  transcription: string | null;
  at: string;
}
export interface ContactDetailOut {
  lead_id: string;
  display_name: string | null;
  whatsapp_e164: string | null;
  state: ContactState;
  funnel_node: string | null;
  active_talk_id: string | null;
  ai_reasoning: string | null;
  window_open: boolean;
  window_expires_at: string | null;
}
export interface TalkBand {
  talk_id: string;
  status: string;
  funnel_node: string | null;
  created_at: string;
}
```

```ts
// frontend/src/lib/apiClient.test.ts
import { afterEach, describe, expect, test, vi } from "vitest";
import { apiGet, ApiError, _setRedirect } from "./apiClient";

afterEach(() => vi.restoreAllMocks());

describe("apiGet", () => {
  test("returns json on 200", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ ok: 1 }), { status: 200 })));
    await expect(apiGet<{ ok: number }>("/api/x")).resolves.toEqual({ ok: 1 });
  });

  test("401 triggers redirect and rejects", async () => {
    const redirect = vi.fn();
    _setRedirect(redirect);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));
    await expect(apiGet("/api/x")).rejects.toBeInstanceOf(ApiError);
    expect(redirect).toHaveBeenCalledOnce();
  });

  test("opaqueredirect (backend bounce to /console/login) triggers redirect", async () => {
    const redirect = vi.fn();
    _setRedirect(redirect);
    // simulate fetch(redirect:"manual") meeting a 3xx → opaqueredirect (status 0)
    const opaque = { type: "opaqueredirect", status: 0, ok: false } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn(async () => opaque));
    await expect(apiGet("/api/x")).rejects.toBeInstanceOf(ApiError);
    expect(redirect).toHaveBeenCalledOnce();
  });

  test("sends credentials + manual redirect", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", f);
    await apiGet("/api/x");
    expect(f).toHaveBeenCalledWith("/api/x", expect.objectContaining({ credentials: "include", redirect: "manual" }));
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm run test -- apiClient`
Expected: FAIL — `./apiClient` not found.

- [ ] **Step 3: Implement apiClient + queryClient + hooks**

```ts
// frontend/src/lib/apiClient.ts
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let _redirect = () => {
  window.location.href = "/console/login";
};
export function _setRedirect(fn: () => void) {
  _redirect = fn;
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "include", redirect: "manual" });
  // require_console_user redirects (3xx) to /console/login on auth failure;
  // with redirect:"manual" that is an opaqueredirect (status 0). Also accept 401.
  if (res.type === "opaqueredirect" || res.status === 401) {
    _redirect();
    throw new ApiError(401, "unauthenticated");
  }
  if (!res.ok) {
    throw new ApiError(res.status, `GET ${path} -> ${res.status}`);
  }
  return (await res.json()) as T;
}
```

```ts
// frontend/src/lib/queryClient.ts
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: false },
  },
});
```

```ts
// frontend/src/hooks/useInbox.ts
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/apiClient";
import type {
  ContactDetailOut, ContactOut, InstanceOut, MeOut, MessageOut, TalkBand, ContactState,
} from "../types";

const base = (slug: string) => `/api/console/tenants/${slug}`;

export interface ContactFilters {
  status?: ContactState | "all";
  funnel?: string;
  q?: string;
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => apiGet<MeOut>("/api/console/me") });
}

export function useInstances(slug: string | undefined) {
  return useQuery({
    queryKey: ["instances", slug],
    enabled: !!slug,
    queryFn: () => apiGet<InstanceOut[]>(`${base(slug!)}/instances`),
  });
}

export function useContacts(
  slug: string | undefined,
  instanceId: string | undefined,
  filters: ContactFilters,
) {
  const params = new URLSearchParams();
  if (filters.status && filters.status !== "all") params.set("status", filters.status);
  if (filters.funnel) params.set("funnel", filters.funnel);
  if (filters.q) params.set("q", filters.q);
  const qs = params.toString();
  return useQuery({
    queryKey: ["contacts", slug, instanceId, filters],
    enabled: !!slug && !!instanceId,
    queryFn: () =>
      apiGet<ContactOut[]>(
        `${base(slug!)}/instances/${instanceId}/contacts${qs ? `?${qs}` : ""}`,
      ),
  });
}

export function useContactDetail(slug: string | undefined, leadId: string | undefined) {
  return useQuery({
    queryKey: ["contact", slug, leadId],
    enabled: !!slug && !!leadId,
    queryFn: () => apiGet<ContactDetailOut>(`${base(slug!)}/contacts/${leadId}`),
  });
}

export function useMessages(slug: string | undefined, leadId: string | undefined) {
  return useQuery({
    queryKey: ["messages", slug, leadId],
    enabled: !!slug && !!leadId,
    queryFn: () => apiGet<MessageOut[]>(`${base(slug!)}/contacts/${leadId}/messages`),
  });
}

export function useTalks(slug: string | undefined, leadId: string | undefined) {
  return useQuery({
    queryKey: ["talks", slug, leadId],
    enabled: !!slug && !!leadId,
    queryFn: () => apiGet<TalkBand[]>(`${base(slug!)}/contacts/${leadId}/talks`),
  });
}
```

- [ ] **Step 4: Add a hook test + run green**

```tsx
// frontend/src/hooks/useInbox.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useContacts } from "./useInbox";

afterEach(() => vi.restoreAllMocks());

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

test("useContacts builds the tenant-scoped URL with filters", async () => {
  const f = vi.fn(async () => new Response("[]", { status: 200 }));
  vi.stubGlobal("fetch", f);
  renderHook(() => useContacts("acme", "inst-1", { status: "human", q: "ana" }), { wrapper: wrap() });
  await waitFor(() => expect(f).toHaveBeenCalled());
  expect(f.mock.calls[0][0]).toBe(
    "/api/console/tenants/acme/instances/inst-1/contacts?status=human&q=ana",
  );
});
```

Run: `cd frontend && npm run test`
Expected: all PASS (apiClient + hook + App smoke).

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): apiClient (401->login), types, query hooks"
```

---

### Task 5: AppShell + InstanceSelector + bootstrap wiring

**Files:**
- Create: `frontend/src/components/AppShell.tsx`, `frontend/src/components/InstanceSelector.tsx`, `frontend/src/components/Brandmark.tsx`
- Modify: `frontend/src/App.tsx` (bootstrap: load `/me`, pick first tenant + instance, render the shell)
- Create test: `frontend/src/components/InstanceSelector.test.tsx`, `frontend/src/App.test.tsx` (extend)

**Interfaces:**
- Consumes: `useMe`, `useInstances`.
- Produces: `AppShell` (3-pane grid: contacts | conversation | sidebar, dark instance selector on top of column 1); `InstanceSelector({ instances, value, onChange })` (Radix dropdown, dark, Syne wordmark + gradient `Brandmark` placeholder); `App` orchestrates: on boot, `useMe` → take `tenants[0].slug` → `useInstances(slug)` → default `selectedInstanceId = instances[0]?.id`; holds `selectedLeadId` state (null in 3A). While `useMe` loads, render a centered spinner; on `useMe` error that's not 401, render a small error panel.

- [ ] **Step 1: Write the failing InstanceSelector test**

```tsx
// frontend/src/components/InstanceSelector.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { InstanceSelector } from "./InstanceSelector";
import type { InstanceOut } from "../types";

const instances: InstanceOut[] = [
  { id: "a", channel_label: "main", display_name: "Main", phone_e164: "+551199" },
  { id: "b", channel_label: "vendas", display_name: "Vendas", phone_e164: null },
];

test("shows the selected instance and switches on pick", async () => {
  const onChange = vi.fn();
  render(<InstanceSelector instances={instances} value="a" onChange={onChange} />);
  expect(screen.getByTestId("instance-current")).toHaveTextContent("Main");
  await userEvent.click(screen.getByTestId("instance-trigger"));
  await userEvent.click(screen.getByText("Vendas"));
  expect(onChange).toHaveBeenCalledWith("b");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- InstanceSelector`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement Brandmark, InstanceSelector, AppShell, and wire App**

```tsx
// frontend/src/components/Brandmark.tsx
export function Brandmark() {
  return (
    <div className="flex items-center gap-2">
      <span
        className="grid h-7 w-7 place-items-center rounded-md font-display text-sm font-bold text-white"
        style={{ background: "var(--brand-gradient)" }}
        aria-hidden
      >
        a
      </span>
      <span className="font-display text-sm font-semibold tracking-tight text-white">
        Avelum Labs
      </span>
    </div>
  );
}
```

```tsx
// frontend/src/components/InstanceSelector.tsx
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { ChevronDown } from "lucide-react";
import type { InstanceOut } from "../types";
import { Brandmark } from "./Brandmark";

export function InstanceSelector({
  instances,
  value,
  onChange,
}: {
  instances: InstanceOut[];
  value: string | undefined;
  onChange: (id: string) => void;
}) {
  const current = instances.find((i) => i.id === value);
  const label = (i: InstanceOut) => i.display_name || i.channel_label;
  return (
    <Dropdown.Root>
      <Dropdown.Trigger
        data-testid="instance-trigger"
        className="flex w-full items-center justify-between gap-3 bg-slate-900 px-3 py-3 text-left"
      >
        <Brandmark />
        <span className="flex items-center gap-1 text-xs text-slate-300">
          <span data-testid="instance-current">{current ? label(current) : "—"}</span>
          <ChevronDown size={14} />
        </span>
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content
          align="end"
          className="z-50 min-w-48 rounded-md border border-slate-700 bg-slate-900 p-1 text-slate-100 shadow-xl"
        >
          {instances.map((i) => (
            <Dropdown.Item
              key={i.id}
              onSelect={() => onChange(i.id)}
              className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-accent/30"
            >
              {label(i)}
            </Dropdown.Item>
          ))}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}
```

```tsx
// frontend/src/components/AppShell.tsx
import type { ReactNode } from "react";

export function AppShell({
  selector,
  contacts,
  conversation,
  sidebar,
}: {
  selector: ReactNode;
  contacts: ReactNode;
  conversation: ReactNode;
  sidebar: ReactNode;
}) {
  return (
    <div className="grid h-full grid-cols-[320px_1fr_300px] bg-slate-100">
      <aside className="flex min-h-0 flex-col border-r border-slate-200 bg-white">
        {selector}
        <div className="min-h-0 flex-1 overflow-y-auto">{contacts}</div>
      </aside>
      <main className="flex min-h-0 flex-col bg-[#efeae2]">{conversation}</main>
      <aside className="min-h-0 overflow-y-auto border-l border-slate-200 bg-white">
        {sidebar}
      </aside>
    </div>
  );
}
```

```tsx
// frontend/src/App.tsx
import { useMemo, useState } from "react";
import { useMe, useInstances } from "./hooks/useInbox";
import { AppShell } from "./components/AppShell";
import { InstanceSelector } from "./components/InstanceSelector";

export default function App() {
  const me = useMe();
  const slug = me.data?.tenants[0]?.slug;
  const instances = useInstances(slug);
  const [instanceId, setInstanceId] = useState<string | undefined>(undefined);
  const [leadId, setLeadId] = useState<string | null>(null);

  const effectiveInstanceId = instanceId ?? instances.data?.[0]?.id;

  const selector = useMemo(
    () => (
      <InstanceSelector
        instances={instances.data ?? []}
        value={effectiveInstanceId}
        onChange={setInstanceId}
      />
    ),
    [instances.data, effectiveInstanceId],
  );

  if (me.isLoading) {
    return <div className="grid h-full place-items-center text-slate-400" data-testid="boot-spinner">Carregando…</div>;
  }
  if (me.isError) {
    return <div className="grid h-full place-items-center text-red-500">Falha ao carregar a sessão.</div>;
  }

  return (
    <AppShell
      selector={selector}
      contacts={<div data-testid="contacts-pane" data-instance={effectiveInstanceId ?? ""} data-slug={slug ?? ""} />}
      conversation={
        leadId ? (
          <div data-testid="conversation-pane" data-lead={leadId} />
        ) : (
          <div className="grid h-full place-items-center text-slate-400">Selecione um contato</div>
        )
      }
      sidebar={<div data-testid="sidebar-pane" />}
    />
  );
  // setLeadId is wired to ContactList in Task 6.
  void setLeadId;
}
```

> The `void setLeadId;` line silences `noUnusedLocals` until Task 6 wires the click. Remove it in Task 6.

- [ ] **Step 4: Extend App.test.tsx + run green**

```tsx
// frontend/src/App.test.tsx  (replace the Task-2 smoke)
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

afterEach(() => vi.restoreAllMocks());

function mockFetch(routes: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const key = Object.keys(routes).find((k) => url.startsWith(k));
    if (!key) return new Response("[]", { status: 200 });
    return new Response(JSON.stringify(routes[key]), { status: 200 });
  }));
}

function renderApp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><App /></QueryClientProvider>,
  );
}

test("boots, resolves tenant + instance, renders the shell", async () => {
  mockFetch({
    "/api/console/me": { user: { id: "u", username: "op" }, tenants: [{ slug: "acme", display_name: "Acme" }] },
    "/api/console/tenants/acme/instances": [{ id: "inst-1", channel_label: "main", display_name: "Main", phone_e164: null }],
  });
  renderApp();
  await waitFor(() => expect(screen.getByTestId("contacts-pane")).toBeInTheDocument());
  expect(screen.getByTestId("contacts-pane")).toHaveAttribute("data-instance", "inst-1");
  expect(screen.getByTestId("contacts-pane")).toHaveAttribute("data-slug", "acme");
});
```

Run: `cd frontend && npm run test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): AppShell + InstanceSelector + /me bootstrap"
```

---

### Task 6: ContactList + filters + search + ContactRow + StateBadge

**Files:**
- Create: `frontend/src/components/StateBadge.tsx`, `frontend/src/components/ContactRow.tsx`, `frontend/src/components/ContactList.tsx`, `frontend/src/lib/format.ts`
- Modify: `frontend/src/App.tsx` (render `ContactList`, hold filters, wire `onSelect` → `setLeadId`)
- Create test: `frontend/src/components/ContactList.test.tsx`, `frontend/src/components/StateBadge.test.tsx`

**Interfaces:**
- Consumes: `useContacts`, the `ContactOut`/`ContactState` types.
- Produces:
  - `StateBadge({ state })` → emoji+label per the badges vocab (`ai`→🟢 IA, `requires_review`→🟠 Revisão, `human`→🔵 Humano, `awaiting`→🆕 Aguardando, `closed`→⚪ Encerrada), each with a `data-state` attr.
  - `ContactRow({ contact, selected, onClick })` → avatar (initials), name (or phone fallback), last-message preview, time (`formatTime`), state badge, funnel tag (teal) when `funnel_node`, unread pill when `unread>0`.
  - `ContactList({ slug, instanceId, selectedLeadId, onSelect })` → a status filter bar (Todas/🆕/🟢/🟠/🔵), a separate funnel filter `<select>` (built from the distinct `funnel_node`s present), a search input (debounced into the `q` filter), and the list of `ContactRow`. Renders an empty-state when no contacts and a skeleton/loading line while fetching. **A contact with `state==="awaiting"` (no active Talk) MUST render.**
  - `formatTime(iso: string | null): string` and `initials(name, phone): string` in `lib/format.ts`.

- [ ] **Step 1: Write failing tests**

```tsx
// frontend/src/components/StateBadge.test.tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { StateBadge } from "./StateBadge";

test.each([
  ["ai", "IA"],
  ["requires_review", "Revisão"],
  ["human", "Humano"],
  ["awaiting", "Aguardando"],
  ["closed", "Encerrada"],
] as const)("renders %s as %s", (state, label) => {
  render(<StateBadge state={state} />);
  expect(screen.getByText(new RegExp(label))).toBeInTheDocument();
});
```

```tsx
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
    expect(f.mock.calls.some((c) => String(c[0]).includes("status=human"))).toBe(true),
  );
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npm run test -- ContactList StateBadge`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement format helpers, StateBadge, ContactRow, ContactList; wire App**

```ts
// frontend/src/lib/format.ts
export function formatTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}
export function initials(name: string | null, phone: string | null): string {
  const src = (name || phone || "?").trim();
  const parts = src.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return src.slice(0, 2).toUpperCase();
}
```

```tsx
// frontend/src/components/StateBadge.tsx
import type { ContactState } from "../types";

const MAP: Record<ContactState, { emoji: string; label: string; cls: string }> = {
  ai: { emoji: "🟢", label: "IA", cls: "text-emerald-700 bg-emerald-50" },
  requires_review: { emoji: "🟠", label: "Revisão", cls: "text-amber-700 bg-amber-50" },
  human: { emoji: "🔵", label: "Humano", cls: "text-accent bg-accent/10" },
  awaiting: { emoji: "🆕", label: "Aguardando", cls: "text-slate-600 bg-slate-100" },
  closed: { emoji: "⚪", label: "Encerrada", cls: "text-slate-400 bg-slate-50" },
};

export function StateBadge({ state }: { state: ContactState }) {
  const m = MAP[state];
  return (
    <span data-state={state} className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ${m.cls}`}>
      <span aria-hidden>{m.emoji}</span>
      {m.label}
    </span>
  );
}
```

```tsx
// frontend/src/components/ContactRow.tsx
import type { ContactOut } from "../types";
import { formatTime, initials } from "../lib/format";
import { StateBadge } from "./StateBadge";

export function ContactRow({
  contact,
  selected,
  onClick,
}: {
  contact: ContactOut;
  selected: boolean;
  onClick: () => void;
}) {
  const name = contact.display_name || contact.whatsapp_e164 || "Sem nome";
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-slate-50 ${selected ? "bg-accent/5" : ""}`}
    >
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-slate-200 text-xs font-semibold text-slate-600">
        {initials(contact.display_name, contact.whatsapp_e164)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium text-slate-800">{name}</span>
          <span className="shrink-0 text-[11px] text-slate-400">{formatTime(contact.last_message_at)}</span>
        </span>
        <span className="mt-0.5 flex items-center justify-between gap-2">
          <span className="truncate text-xs text-slate-500">{contact.last_message_preview || "—"}</span>
          {contact.unread > 0 && (
            <span className="grid h-4 min-w-4 shrink-0 place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">
              {contact.unread}
            </span>
          )}
        </span>
        <span className="mt-1 flex items-center gap-1.5">
          <StateBadge state={contact.state} />
          {contact.funnel_node && (
            <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-teal" style={{ background: "color-mix(in srgb, var(--teal) 15%, transparent)" }}>
              {contact.funnel_node}
            </span>
          )}
        </span>
      </span>
    </button>
  );
}
```

```tsx
// frontend/src/components/ContactList.tsx
import { useMemo, useState } from "react";
import { useContacts, type ContactFilters } from "../hooks/useInbox";
import type { ContactState } from "../types";
import { ContactRow } from "./ContactRow";

const STATUS_TABS: { key: ContactState | "all"; label: string; testid: string }[] = [
  { key: "all", label: "Todas", testid: "filter-all" },
  { key: "awaiting", label: "🆕", testid: "filter-awaiting" },
  { key: "ai", label: "🟢", testid: "filter-ai" },
  { key: "requires_review", label: "🟠", testid: "filter-review" },
  { key: "human", label: "🔵", testid: "filter-human" },
];

export function ContactList({
  slug,
  instanceId,
  selectedLeadId,
  onSelect,
}: {
  slug: string | undefined;
  instanceId: string | undefined;
  selectedLeadId: string | null;
  onSelect: (leadId: string) => void;
}) {
  const [status, setStatus] = useState<ContactState | "all">("all");
  const [q, setQ] = useState("");
  const [funnel, setFunnel] = useState<string>("");

  const filters: ContactFilters = { status, q: q || undefined, funnel: funnel || undefined };
  const { data, isLoading } = useContacts(slug, instanceId, filters);

  const funnels = useMemo(
    () => Array.from(new Set((data ?? []).map((c) => c.funnel_node).filter(Boolean))) as string[],
    [data],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-slate-100 p-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Buscar contato…"
          className="w-full rounded-md bg-slate-100 px-3 py-1.5 text-sm outline-none focus:ring-1 focus:ring-accent"
        />
        <div className="mt-2 flex items-center gap-1">
          {STATUS_TABS.map((t) => (
            <button
              key={t.key}
              data-testid={t.testid}
              onClick={() => setStatus(t.key)}
              className={`rounded px-2 py-1 text-xs ${status === t.key ? "bg-accent text-white" : "bg-slate-100 text-slate-600"}`}
            >
              {t.label}
            </button>
          ))}
          {funnels.length > 0 && (
            <select
              value={funnel}
              onChange={(e) => setFunnel(e.target.value)}
              className="ml-auto rounded bg-slate-100 px-1.5 py-1 text-xs text-slate-600"
            >
              <option value="">Funil: todos</option>
              {funnels.map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading && <div className="p-4 text-xs text-slate-400">Carregando contatos…</div>}
        {!isLoading && (data?.length ?? 0) === 0 && (
          <div className="p-4 text-xs text-slate-400">Nenhum contato.</div>
        )}
        {data?.map((c) => (
          <ContactRow
            key={c.lead_id}
            contact={c}
            selected={c.lead_id === selectedLeadId}
            onClick={() => onSelect(c.lead_id)}
          />
        ))}
      </div>
    </div>
  );
}
```

Then in `App.tsx`: import `ContactList`, remove the `void setLeadId;` line, and replace the `contacts` prop with the real list:

```tsx
      contacts={
        <ContactList
          slug={slug}
          instanceId={effectiveInstanceId}
          selectedLeadId={leadId}
          onSelect={setLeadId}
        />
      }
```

- [ ] **Step 4: Run tests green**

Run: `cd frontend && npm run test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): ContactList + filters/search + ContactRow + StateBadge"
```

---

### Task 7: Backend `GET /contacts/{lead_id}/talks` (Talk bands for delimiters)

**Files:**
- Modify: `src/ai_sdr/api/routes/console_inbox.py` (add the endpoint), `src/ai_sdr/api/schemas/console_inbox.py` (add `TalkBandOut`)
- Create test: `tests/integration/test_console_talks.py`

**Interfaces:**
- Produces: `GET /api/console/tenants/{slug}/contacts/{lead_id}/talks` → `list[TalkBandOut]` ordered by `created_at ASC`, where `TalkBandOut { talk_id: uuid; status: str; funnel_node: str | None; created_at: datetime }`. Tenant-safe (the lead's `tenant_id` must equal the resolved tenant, else 404 — mirror `get_contact_detail`). The frontend uses each band's `created_at` to draw an inline delimiter where the message stream crosses into that Talk.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_console_talks.py
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_talks_returns_bands_for_lead(authed_inbox_client, seeded_talk_factory, db_session):
    client, ctx = authed_inbox_client
    await seeded_talk_factory(lead_id=ctx["lead_id"], handling_mode="ai", status="active")
    await db_session.commit()
    resp = await client.get(f"/api/console/tenants/{ctx['slug']}/contacts/{ctx['lead_id']}/talks")
    assert resp.status_code == 200
    bands = resp.json()
    assert len(bands) >= 1
    assert {"talk_id", "status", "funnel_node", "created_at"} <= set(bands[0].keys())


async def test_talks_cross_tenant_404(authed_inbox_client):
    client, ctx = authed_inbox_client
    import uuid
    resp = await client.get(f"/api/console/tenants/{ctx['slug']}/contacts/{uuid.uuid4()}/talks")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/integration/test_console_talks.py -q`
Expected: FAIL — route 404 for the first test.

- [ ] **Step 3: Implement schema + endpoint**

Add to `src/ai_sdr/api/schemas/console_inbox.py`:

```python
class TalkBandOut(BaseModel):
    talk_id: uuid.UUID
    status: str
    funnel_node: str | None
    created_at: datetime
```

Add to `src/ai_sdr/api/routes/console_inbox.py` (mirror `get_contact_detail`'s tenant-safety guard, and read the `Talk` model for the exact column names — `treeflow_id`/funnel field and `created_at`):

```python
@router.get("/contacts/{lead_id}/talks", response_model=list[TalkBandOut])
async def list_contact_talks(
    lead_id: uuid.UUID,
    ctx: TenantCtx,
    db: DbSession,
) -> list[TalkBandOut]:
    tenant, _user = ctx
    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id))
    ).scalar_one_or_none()
    if lead is None or lead.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail=f"contact {lead_id} not found")
    rows = (
        await db.execute(
            select(Talk)
            .where(Talk.tenant_id == tenant.id, Talk.lead_id == lead_id)
            .order_by(Talk.created_at.asc())
        )
    ).scalars().all()
    return [
        TalkBandOut(
            talk_id=t.id,
            status=t.status,
            funnel_node=getattr(t, "treeflow_id", None),
            created_at=t.created_at,
        )
        for t in rows
    ]
```

> Read `src/ai_sdr/models/talk.py` to confirm the field that represents the funnel/treeflow (the inbox repo's `derive_state`/`funnel_node` derivation will show which column — match it so the delimiter's funnel tag agrees with the contact list's). Import `TalkBandOut` and `Talk` at the top if not already imported.

- [ ] **Step 4: Run green**

Run: `uv run pytest tests/integration/test_console_talks.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/api/routes/console_inbox.py src/ai_sdr/api/schemas/console_inbox.py tests/integration/test_console_talks.py
git commit -m "feat(chat-api): GET contacts/{lead}/talks (Talk bands for delimiters)"
```

---

### Task 8: ConversationView read-only (Header + MessageStream + bubbles + TalkDelimiter)

**Files:**
- Create: `frontend/src/components/ConversationHeader.tsx`, `frontend/src/components/MessageBubble.tsx`, `frontend/src/components/TalkDelimiter.tsx`, `frontend/src/components/MessageStream.tsx`, `frontend/src/components/ConversationView.tsx`
- Modify: `frontend/src/App.tsx` (render `ConversationView` when `leadId` set)
- Create test: `frontend/src/components/MessageStream.test.tsx`, `frontend/src/components/ConversationView.test.tsx`

**Interfaces:**
- Consumes: `useContactDetail`, `useMessages`, `useTalks`; the `MessageOut`/`TalkBand`/`ContactDetailOut` types.
- Produces:
  - `ConversationHeader({ detail })` → contact name, `StateBadge`, and **disabled** "Assumir / Devolver" buttons (wired in 3B; render with `disabled` + a tooltip title "em breve").
  - `MessageBubble({ msg })` → right-aligned for `direction==="out"` (accent-tinted; label "Você" for `origin==="operator"`, "IA" for `origin==="ai"`), left for `direction==="in"`. If `media_type==="audio"`: render an audio element from `audio_url` (when present) + the `transcription` text beneath. If `media_type==="unsupported"`: render an italic "mensagem não suportada" line. Else the `text`.
  - `TalkDelimiter({ band })` → a centered chip "— conversa · {funnel_node ?? status} · {date} —".
  - `MessageStream({ messages, talks })` → renders messages in time order, inserting a `TalkDelimiter` immediately before the first message whose `at >= band.created_at` for each band (so each Talk's start is marked). A day-separator is optional; the Talk delimiters are the required markers.
  - `ConversationView({ slug, leadId })` → loads detail + messages + talks, renders header + stream; empty/loading states.

- [ ] **Step 1: Write failing tests**

```tsx
// frontend/src/components/MessageStream.test.tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { MessageStream } from "./MessageStream";
import type { MessageOut, TalkBand } from "../types";

const messages: MessageOut[] = [
  { id: "m1", direction: "in", origin: "lead", text: "oi", media_type: "text", audio_url: null, transcription: null, at: "2026-06-26T10:00:00Z" },
  { id: "m2", direction: "out", origin: "ai", text: "olá!", media_type: "text", audio_url: null, transcription: null, at: "2026-06-26T10:01:00Z" },
  { id: "m3", direction: "out", origin: "operator", text: "aqui é a Ana", media_type: "text", audio_url: null, transcription: null, at: "2026-06-27T09:00:00Z" },
];
const talks: TalkBand[] = [
  { talk_id: "t1", status: "closed", funnel_node: "boas-vindas", created_at: "2026-06-26T09:59:00Z" },
  { talk_id: "t2", status: "active", funnel_node: "humano", created_at: "2026-06-27T08:59:00Z" },
];

test("renders bubbles and a delimiter at each Talk boundary", () => {
  render(<MessageStream messages={messages} talks={talks} />);
  expect(screen.getByText("oi")).toBeInTheDocument();
  expect(screen.getByText("aqui é a Ana")).toBeInTheDocument();
  // one delimiter per talk band
  expect(screen.getAllByTestId("talk-delimiter")).toHaveLength(2);
  // operator bubble labelled "Você"
  expect(screen.getByText("Você")).toBeInTheDocument();
});
```

```tsx
// frontend/src/components/ConversationView.test.tsx
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npm run test -- MessageStream ConversationView`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement the components + wire App**

```tsx
// frontend/src/components/TalkDelimiter.tsx
import type { TalkBand } from "../types";

export function TalkDelimiter({ band }: { band: TalkBand }) {
  const date = new Date(band.created_at).toLocaleDateString("pt-BR");
  return (
    <div data-testid="talk-delimiter" className="my-3 flex items-center justify-center">
      <span className="rounded-full bg-slate-200/80 px-3 py-1 text-[11px] text-slate-600">
        — conversa · {band.funnel_node ?? band.status} · {date} —
      </span>
    </div>
  );
}
```

```tsx
// frontend/src/components/MessageBubble.tsx
import type { MessageOut } from "../types";
import { formatTime } from "../lib/format";

export function MessageBubble({ msg }: { msg: MessageOut }) {
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
        <div className="mt-1 text-right text-[10px] text-slate-400">{formatTime(msg.at)}</div>
      </div>
    </div>
  );
}
```

```tsx
// frontend/src/components/MessageStream.tsx
import { Fragment } from "react";
import type { MessageOut, TalkBand } from "../types";
import { MessageBubble } from "./MessageBubble";
import { TalkDelimiter } from "./TalkDelimiter";

export function MessageStream({ messages, talks }: { messages: MessageOut[]; talks: TalkBand[] }) {
  const sortedMsgs = [...messages].sort((a, b) => a.at.localeCompare(b.at));
  const sortedTalks = [...talks].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const placed = new Set<string>();

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-4">
      {sortedMsgs.map((m) => {
        // any talk band whose start is <= this message and not yet placed → emit before it
        const due = sortedTalks.filter((t) => !placed.has(t.talk_id) && t.created_at <= m.at);
        due.forEach((t) => placed.add(t.talk_id));
        return (
          <Fragment key={m.id}>
            {due.map((t) => (
              <TalkDelimiter key={t.talk_id} band={t} />
            ))}
            <MessageBubble msg={m} />
          </Fragment>
        );
      })}
      {/* trailing talks that start after the last message (e.g. a freshly opened Talk) */}
      {sortedTalks
        .filter((t) => !placed.has(t.talk_id))
        .map((t) => (
          <TalkDelimiter key={t.talk_id} band={t} />
        ))}
    </div>
  );
}
```

```tsx
// frontend/src/components/ConversationHeader.tsx
import type { ContactDetailOut } from "../types";
import { StateBadge } from "./StateBadge";

export function ConversationHeader({ detail }: { detail: ContactDetailOut }) {
  const name = detail.display_name || detail.whatsapp_e164 || "Contato";
  return (
    <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2.5">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-slate-800">{name}</span>
        <StateBadge state={detail.state} />
      </div>
      <div className="flex items-center gap-2">
        <button data-testid="btn-takeover" disabled title="em breve (3B)" className="rounded bg-accent px-3 py-1 text-xs font-medium text-white opacity-40">
          Assumir
        </button>
        <button data-testid="btn-release" disabled title="em breve (3B)" className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-500 opacity-40">
          Devolver pra IA
        </button>
      </div>
    </header>
  );
}
```

```tsx
// frontend/src/components/ConversationView.tsx
import { useContactDetail, useMessages, useTalks } from "../hooks/useInbox";
import { ConversationHeader } from "./ConversationHeader";
import { MessageStream } from "./MessageStream";

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
      <ConversationHeader detail={detail.data} />
      <MessageStream messages={messages.data ?? []} talks={talks.data ?? []} />
      <div className="border-t border-slate-200 bg-white px-4 py-3 text-center text-xs text-slate-400">
        Composer chega no 3B (assumir + responder)
      </div>
    </div>
  );
}
```

Then in `App.tsx`, replace the `conversation` prop's `leadId ? (...)` branch:

```tsx
      conversation={
        leadId ? (
          <ConversationView slug={slug} leadId={leadId} />
        ) : (
          <div className="grid h-full place-items-center text-slate-400">Selecione um contato</div>
        )
      }
```

- [ ] **Step 4: Run green**

Run: `cd frontend && npm run test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): read-only ConversationView (header + stream + bubbles + Talk delimiters)"
```

---

### Task 9: DetailsSidebar + full read-only flow + build green

**Files:**
- Create: `frontend/src/components/DetailsSidebar.tsx`
- Modify: `frontend/src/App.tsx` (render `DetailsSidebar` when `leadId` set)
- Create test: `frontend/src/components/DetailsSidebar.test.tsx`, `frontend/src/flow.test.tsx`

**Interfaces:**
- Consumes: `useContactDetail`.
- Produces: `DetailsSidebar({ slug, leadId })` → contact block (name, phone), funnel stage (teal tag from `funnel_node`), AI context (`ai_reasoning` if present, else "—"), 24h window line (`window_open ? "Janela aberta" : "Janela fechada"` + `window_expires_at` when present), and **disabled** action buttons (Devolver / Resolver / Reatribuir) wired in 3B. When no `leadId`, render an empty hint.

- [ ] **Step 1: Write failing tests**

```tsx
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
```

```tsx
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
  await waitFor(() => expect(screen.getByText("oi")).toBeInTheDocument());
  expect(screen.getByTestId("btn-takeover")).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npm run test -- DetailsSidebar flow`
Expected: FAIL — `DetailsSidebar` not found.

- [ ] **Step 3: Implement DetailsSidebar + wire App**

```tsx
// frontend/src/components/DetailsSidebar.tsx
import { useContactDetail } from "../hooks/useInbox";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="px-4 py-2">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-0.5 text-sm text-slate-700">{children}</div>
    </div>
  );
}

export function DetailsSidebar({ slug, leadId }: { slug: string | undefined; leadId: string }) {
  const { data } = useContactDetail(slug, leadId);
  if (!data) return <div className="p-4 text-xs text-slate-400">—</div>;
  return (
    <div className="divide-y divide-slate-100">
      <Row label="Contato">{data.display_name || data.whatsapp_e164 || "—"}</Row>
      <Row label="Telefone">{data.whatsapp_e164 || "—"}</Row>
      <Row label="Etapa do funil">
        {data.funnel_node ? (
          <span className="rounded px-1.5 py-0.5 text-xs font-medium text-teal" style={{ background: "color-mix(in srgb, var(--teal) 15%, transparent)" }}>
            {data.funnel_node}
          </span>
        ) : "—"}
      </Row>
      <Row label="Contexto da IA">{data.ai_reasoning || "—"}</Row>
      <Row label="Janela 24h">
        {data.window_open ? "Janela aberta" : "Janela fechada"}
        {data.window_expires_at && (
          <span className="text-slate-400"> · {new Date(data.window_expires_at).toLocaleString("pt-BR")}</span>
        )}
      </Row>
      <div className="flex flex-col gap-2 p-4">
        {["Devolver pra IA", "Resolver", "Reatribuir"].map((a) => (
          <button key={a} disabled title="em breve (3B)" className="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-400 opacity-50">
            {a}
          </button>
        ))}
      </div>
    </div>
  );
}
```

Then in `App.tsx`, replace the `sidebar` prop:

```tsx
      sidebar={leadId ? <DetailsSidebar slug={slug} leadId={leadId} /> : <div className="p-4 text-xs text-slate-400">Selecione um contato.</div>}
```

- [ ] **Step 4: Run the full suite + a production build**

Run:
```
cd frontend && npm run test && npm run build
```
Expected: all Vitest tests PASS; `npm run build` succeeds (tsc clean + Vite writes `../src/ai_sdr/web/static/inbox/`).

- [ ] **Step 5: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(chat-ui): DetailsSidebar + read-only end-to-end flow"
```

---

## Self-Review

**Spec coverage (spec §5/§11 → task):**
- AppShell (3 panes) → T5. InstanceSelector → T5. ContactList + filters + search + state badges + funnel tag + unread → T6. Contact-without-Talk (`awaiting`) renders → T6 (asserted). ConversationView header + continuous stream + Talk delimiters + AI/You bubbles + audio bubble → T8. DetailsSidebar (contact/funnel/AI-context/window/actions) → T9. apiClient + TanStack hooks → T4. Cookie auth + 401→login → T4. Served by FastAPI → T3. Bootstrap (tenant slug discovery) → T1. Talk delimiters need backend bands → T7. Avelum tokens (accent/teal/gradient/Syne) → T2/T5/T6. ✓
- **Deferred to 3B (explicitly NOT here):** composer + send, takeover/release wiring (rendered disabled), 24h-closed template picker. **Deferred to 3C:** WebSocket/live updates. **Deferred (needs 2B-ii):** ✓✓ delivery-status; **needs persisted `TurnDecision.reasoning`:** richer AI-context (sidebar shows `ai_reasoning` as the API returns it today — may be null). These are intended, documented gaps.

**Placeholder scan:** No "TBD". Three places point at live code to confirm a name: T1 (`require_console_user` 401-vs-403 convention), T7 (the `Talk` funnel/treeflow column name), and the `.gitignore` for the build dir. Each names exactly what to check and how. The Avelum logo is an intentional gradient placeholder per Global Constraints.

**Type consistency:** `frontend/src/types.ts` mirrors the pydantic schemas verbatim (ContactState union, MessageOut origin/direction literals, ContactDetailOut fields). Hook signatures (`useContacts(slug, instanceId, filters)`, `useTalks(slug, leadId)`) are consistent between T4 (definition) and T5/T6/T8 (use). `TalkBand` (frontend) matches `TalkBandOut` (T7 backend). `StateBadge`/`ContactState` vocab identical across T6/T8. The `void setLeadId;` placeholder in T5 is explicitly removed in T6.

## Open items the implementer resolves against live code
1. T1: whether `require_console_user` raises 401 or 403 on missing cookie — assert the real convention in the unauth test.
2. T7: the `Talk` model's funnel/treeflow column name (used for `funnel_node` in the band) — match what the inbox repository uses so the delimiter tag agrees with the contact-list funnel tag.
3. T2: confirm Node 20 + npm are available (`node -v`); if the registry blocks any pinned version, the nearest patch is fine — keep the majors.
4. Fonts Syne/Madefor are referenced by CSS var but not loaded in 3A (no webfont `@import`); they fall back to `system-ui`. Loading the real webfonts (and the Avelum SVG logo) is a polish follow-up, not a 3A blocker.
