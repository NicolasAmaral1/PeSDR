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
