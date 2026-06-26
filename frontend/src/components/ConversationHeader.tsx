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
