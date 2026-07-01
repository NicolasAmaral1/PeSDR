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
