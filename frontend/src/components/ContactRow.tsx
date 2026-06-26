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
