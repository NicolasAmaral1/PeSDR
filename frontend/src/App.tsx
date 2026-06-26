import { useMemo, useState } from "react";
import { useMe, useInstances } from "./hooks/useInbox";
import { AppShell } from "./components/AppShell";
import { InstanceSelector } from "./components/InstanceSelector";

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
      contacts={<div data-testid="contacts-pane" data-instance={effectiveInstanceId ?? ""} data-slug={slug ?? ""} />}
      conversation={
        leadId ? (
          <div data-testid="conversation-pane" data-lead={leadId} />
        ) : (
          <div className="grid h-full place-items-center text-slate-400">Selecione um contato</div>
        )
      }
      sidebar={<div data-testid="sidebar-pane" />}
    />
  );
  // setLeadId is wired to ContactList in Task 6.
  void setLeadId;
}
