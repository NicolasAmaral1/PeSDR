import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/apiClient";
import type {
  ContactDetailOut, ContactOut, InstanceOut, MeOut, MessageOut, TalkBand, ContactState,
} from "../types";

const base = (slug: string) => `/api/console/tenants/${slug}`;

export interface ContactFilters {
  status?: ContactState | "all";
  funnel?: string;
  q?: string;
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => apiGet<MeOut>("/api/console/me") });
}

export function useInstances(slug: string | undefined) {
  return useQuery({
    queryKey: ["instances", slug],
    enabled: !!slug,
    queryFn: () => apiGet<InstanceOut[]>(`${base(slug!)}/instances`),
  });
}

export function useContacts(
  slug: string | undefined,
  instanceId: string | undefined,
  filters: ContactFilters,
) {
  const params = new URLSearchParams();
  if (filters.status && filters.status !== "all") params.set("status", filters.status);
  if (filters.funnel) params.set("funnel", filters.funnel);
  if (filters.q) params.set("q", filters.q);
  const qs = params.toString();
  return useQuery({
    queryKey: ["contacts", slug, instanceId, filters],
    enabled: !!slug && !!instanceId,
    queryFn: () =>
      apiGet<ContactOut[]>(
        `${base(slug!)}/instances/${instanceId}/contacts${qs ? `?${qs}` : ""}`,
      ),
  });
}

export function useContactDetail(slug: string | undefined, leadId: string | undefined) {
  return useQuery({
    queryKey: ["contact", slug, leadId],
    enabled: !!slug && !!leadId,
    queryFn: () => apiGet<ContactDetailOut>(`${base(slug!)}/contacts/${leadId}`),
  });
}

export function useMessages(slug: string | undefined, leadId: string | undefined) {
  return useQuery({
    queryKey: ["messages", slug, leadId],
    enabled: !!slug && !!leadId,
    queryFn: () => apiGet<MessageOut[]>(`${base(slug!)}/contacts/${leadId}/messages`),
  });
}

export function useTalks(slug: string | undefined, leadId: string | undefined) {
  return useQuery({
    queryKey: ["talks", slug, leadId],
    enabled: !!slug && !!leadId,
    queryFn: () => apiGet<TalkBand[]>(`${base(slug!)}/contacts/${leadId}/talks`),
  });
}
