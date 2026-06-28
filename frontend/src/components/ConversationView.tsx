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
      <ConversationHeader detail={detail.data} slug={slug ?? ""} />
      <MessageStream messages={messages.data ?? []} talks={talks.data ?? []} />
      <div className="border-t border-slate-200 bg-white px-4 py-3 text-center text-xs text-slate-400">
        Composer chega no 3B (assumir + responder)
      </div>
    </div>
  );
}
