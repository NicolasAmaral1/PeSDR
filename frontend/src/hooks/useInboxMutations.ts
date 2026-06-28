// frontend/src/hooks/useInboxMutations.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPost } from "../lib/apiClient";
import type { MessageOut } from "../types";

const base = (slug: string, leadId: string) => `/api/console/tenants/${slug}/contacts/${leadId}`;

export type OptimisticMessage = MessageOut & { _pending?: boolean };

export function useTakeover(slug: string, leadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost(`${base(slug, leadId)}/takeover`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
      qc.invalidateQueries({ queryKey: ["contacts", slug] });
    },
    // a 409 (e.g. someone else took over) reconciles the UI to server state
    onError: () => {
      qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
    },
  });
}

export function useRelease(slug: string, leadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost(`${base(slug, leadId)}/release`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
      qc.invalidateQueries({ queryKey: ["contacts", slug] });
    },
    // a 409 (e.g. state changed under us) reconciles the UI to server state
    onError: () => {
      qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
    },
  });
}

export function useSend(slug: string, leadId: string) {
  const qc = useQueryClient();
  const key = ["messages", slug, leadId];
  return useMutation({
    mutationFn: ({ text }: { text: string }) => {
      const client_message_id = crypto.randomUUID();
      return apiPost(`${base(slug, leadId)}/send`, { text, client_message_id });
    },
    onMutate: async ({ text }: { text: string }) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<OptimisticMessage[]>(key) ?? [];
      const optimistic: OptimisticMessage = {
        id: crypto.randomUUID(),
        direction: "out",
        origin: "operator",
        text,
        media_type: "text",
        audio_url: null,
        transcription: null,
        at: new Date().toISOString(),
        _pending: true,
      };
      qc.setQueryData<OptimisticMessage[]>(key, [...previous, optimistic]);
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) qc.setQueryData(key, context.previous);
    },
    // On success we keep the optimistic bubble in place (no immediate refetch):
    // there is no live channel yet (3C), and the messages endpoint would not
    // echo the just-sent message synchronously. A later natural refetch (remount
    // / poll) reconciles the bubble with the server copy by id.
  });
}
