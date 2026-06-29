// frontend/src/hooks/useInboxSocket.ts
import { useEffect, useRef, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

export interface InboxEvent {
  seq: number;
  type: string;
  instance_id: string;
  lead_id: string | null;
  payload: Record<string, unknown>;
}

/** Pure event → query-invalidation mapping (unit-testable without a socket). */
export function applyInboxEvent(qc: QueryClient, slug: string, env: InboxEvent): void {
  const leadId = env.lead_id;
  switch (env.type) {
    case "message.created":
      if (leadId) qc.invalidateQueries({ queryKey: ["messages", slug, leadId] });
      if (leadId) qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
      qc.invalidateQueries({ queryKey: ["contacts", slug] });
      break;
    case "talk.updated":
    case "contact.updated":
      if (leadId) qc.invalidateQueries({ queryKey: ["contact", slug, leadId] });
      qc.invalidateQueries({ queryKey: ["contacts", slug] });
      break;
    default:
      // unknown / out-of-scope types (e.g. message.status_updated → 2B-ii) are ignored
      break;
  }
}

export function useInboxSocket(
  slug: string | undefined,
  instanceId: string | undefined,
): { connected: boolean } {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const lastSeqRef = useRef(0);

  useEffect(() => {
    if (!slug || !instanceId) return;
    let ws: WebSocket | null = null;
    let backoff = 1000;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let unmounted = false;
    let hasConnectedBefore = false;

    function connect() {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}/ws/instances/${instanceId}`);

      ws.onopen = () => {
        setConnected(true);
        backoff = 1000;
        // catch-up via REST refetch (v1 reconnect rule, no server replay)
        qc.invalidateQueries({ queryKey: ["contacts", slug] });
        if (hasConnectedBefore) {
          qc.invalidateQueries({ queryKey: ["messages", slug] });
          qc.invalidateQueries({ queryKey: ["contact", slug] });
        }
        hasConnectedBefore = true;
      };

      ws.onmessage = (e: MessageEvent) => {
        let env: InboxEvent;
        try {
          env = JSON.parse(e.data as string) as InboxEvent;
        } catch {
          return;
        }
        if (typeof env.seq === "number") {
          if (env.seq <= lastSeqRef.current) return; // dedup
          lastSeqRef.current = env.seq;
        }
        applyInboxEvent(qc, slug!, env);
      };

      ws.onclose = () => {
        setConnected(false);
        if (unmounted) return;
        // schedule with the current backoff, then grow it for the next attempt
        reconnectTimer = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 15000);
      };

      ws.onerror = () => {
        ws?.close();
      };
    }

    connect();

    return () => {
      unmounted = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [slug, instanceId, qc]);

  return { connected };
}
