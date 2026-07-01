export type ContactState = "ai" | "requires_review" | "human" | "awaiting" | "closed";

export interface MeOut {
  user: { id: string; username: string };
  tenants: { slug: string; display_name: string }[];
}
export interface InstanceOut {
  id: string;
  channel_label: string;
  display_name: string | null;
  phone_e164: string | null;
}
export interface ContactOut {
  lead_id: string;
  display_name: string | null;
  whatsapp_e164: string | null;
  last_message_at: string | null;
  last_message_preview: string | null;
  state: ContactState;
  funnel_node: string | null;
  unread: number;
}
export interface MessageOut {
  id: string;
  direction: "in" | "out";
  origin: "lead" | "ai" | "operator";
  text: string | null;
  media_type: string;
  audio_url: string | null;
  transcription: string | null;
  at: string;
}
export interface ContactDetailOut {
  lead_id: string;
  display_name: string | null;
  whatsapp_e164: string | null;
  state: ContactState;
  funnel_node: string | null;
  active_talk_id: string | null;
  ai_reasoning: string | null;
  window_open: boolean;
  window_expires_at: string | null;
}
export interface TalkBand {
  talk_id: string;
  status: string;
  funnel_node: string | null;
  created_at: string;
}
