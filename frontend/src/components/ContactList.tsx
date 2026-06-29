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
