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
