import type { ContactState } from "../types";

const MAP: Record<ContactState, { emoji: string; label: string; cls: string }> = {
  ai: { emoji: "🟢", label: "IA", cls: "text-emerald-700 bg-emerald-50" },
  requires_review: { emoji: "🟠", label: "Revisão", cls: "text-amber-700 bg-amber-50" },
  human: { emoji: "🔵", label: "Humano", cls: "text-accent bg-accent/10" },
  awaiting: { emoji: "🆕", label: "Aguardando", cls: "text-slate-600 bg-slate-100" },
  closed: { emoji: "⚪", label: "Encerrada", cls: "text-slate-400 bg-slate-50" },
};

const UNKNOWN = { emoji: "•", label: "—", cls: "text-slate-400 bg-slate-50" };

export function StateBadge({ state }: { state: ContactState }) {
  // Defensive: an unexpected state must not white-screen the whole list.
  const m = MAP[state] ?? UNKNOWN;
  return (
    <span data-state={state} className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ${m.cls}`}>
      <span aria-hidden>{m.emoji}</span>
      {m.label}
    </span>
  );
}
