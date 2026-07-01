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
