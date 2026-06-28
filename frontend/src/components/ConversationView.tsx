import { useContactDetail, useMessages, useTalks } from "../hooks/useInbox";
import { ConversationHeader } from "./ConversationHeader";
import { MessageStream } from "./MessageStream";
import { Composer } from "./Composer";

export function ConversationView({ slug, leadId }: { slug: string | undefined; leadId: string }) {
  const detail = useContactDetail(slug, leadId);
  const messages = useMessages(slug, leadId);
  const talks = useTalks(slug, leadId);

  if (!slug) {
    return <div className="grid h-full place-items-center text-slate-400">Sem instância.</div>;
  }
  if (detail.isLoading) {
    return <div className="grid h-full place-items-center text-slate-400">Carregando conversa…</div>;
  }
  if (!detail.data) {
    return <div className="grid h-full place-items-center text-slate-400">Conversa indisponível.</div>;
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <ConversationHeader detail={detail.data} slug={slug} />
      <MessageStream messages={messages.data ?? []} talks={talks.data ?? []} />
      <Composer
        slug={slug}
        leadId={leadId}
        state={detail.data.state}
        windowOpen={detail.data.window_open}
      />
    </div>
  );
}
