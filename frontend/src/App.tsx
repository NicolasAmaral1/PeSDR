import { useMemo, useState } from "react";
import { useMe, useInstances } from "./hooks/useInbox";
import { AppShell } from "./components/AppShell";
import { InstanceSelector } from "./components/InstanceSelector";
import { ContactList } from "./components/ContactList";
import { ConversationView } from "./components/ConversationView";
import { DetailsSidebar } from "./components/DetailsSidebar";

export default function App() {
  const me = useMe();
  const slug = me.data?.tenants[0]?.slug;
  const instances = useInstances(slug);
  const [instanceId, setInstanceId] = useState<string | undefined>(undefined);
  const [leadId, setLeadId] = useState<string | null>(null);

  const effectiveInstanceId = instanceId ?? instances.data?.[0]?.id;

  const selector = useMemo(
    () => (
      <InstanceSelector
        instances={instances.data ?? []}
        value={effectiveInstanceId}
        onChange={setInstanceId}
      />
    ),
    [instances.data, effectiveInstanceId],
  );

  if (me.isLoading || (slug && instances.isLoading)) {
    return <div className="grid h-full place-items-center text-slate-400" data-testid="boot-spinner">Carregando…</div>;
  }
  if (me.isError) {
    return <div className="grid h-full place-items-center text-red-500">Falha ao carregar a sessão.</div>;
  }

  return (
    <AppShell
      selector={selector}
      contacts={
        <ContactList
          slug={slug}
          instanceId={effectiveInstanceId}
          selectedLeadId={leadId}
          onSelect={setLeadId}
        />
      }
      conversation={
        leadId ? (
          <ConversationView slug={slug} leadId={leadId} />
        ) : (
          <div className="grid h-full place-items-center text-slate-400">Selecione um contato</div>
        )
      }
      sidebar={leadId ? <DetailsSidebar slug={slug} leadId={leadId} /> : <div className="p-4 text-xs text-slate-400">Selecione um contato.</div>}
    />
  );
}
