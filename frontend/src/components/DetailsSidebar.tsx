// frontend/src/components/DetailsSidebar.tsx
import { useContactDetail } from "../hooks/useInbox";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="px-4 py-2">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-0.5 text-sm text-slate-700">{children}</div>
    </div>
  );
}

export function DetailsSidebar({ slug, leadId }: { slug: string | undefined; leadId: string }) {
  const { data } = useContactDetail(slug, leadId);
  if (!data) return <div className="p-4 text-xs text-slate-400">—</div>;
  return (
    <div className="divide-y divide-slate-100">
      <Row label="Contato">{data.display_name || data.whatsapp_e164 || "—"}</Row>
      <Row label="Telefone">{data.whatsapp_e164 || "—"}</Row>
      <Row label="Etapa do funil">
        {data.funnel_node ? (
          <span className="rounded px-1.5 py-0.5 text-xs font-medium text-teal" style={{ background: "color-mix(in srgb, var(--teal) 15%, transparent)" }}>
            {data.funnel_node}
          </span>
        ) : "—"}
      </Row>
      <Row label="Contexto da IA">{data.ai_reasoning || "—"}</Row>
      <Row label="Janela 24h">
        {data.window_open ? "Janela aberta" : "Janela fechada"}
        {data.window_expires_at && (
          <span className="text-slate-400"> · {new Date(data.window_expires_at).toLocaleString("pt-BR")}</span>
        )}
      </Row>
      <div className="flex flex-col gap-2 p-4">
        {["Devolver pra IA", "Resolver", "Reatribuir"].map((a) => (
          <button key={a} disabled title="em breve (3B)" className="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-400 opacity-50">
            {a}
          </button>
        ))}
      </div>
    </div>
  );
}
