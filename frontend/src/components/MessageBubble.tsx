import type { MessageOut } from "../types";
import { formatTime } from "../lib/format";

export function MessageBubble({ msg }: { msg: MessageOut }) {
  const out = msg.direction === "out";
  const senderLabel = msg.origin === "operator" ? "Você" : msg.origin === "ai" ? "IA" : null;
  return (
    <div className={`flex ${out ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[72%] rounded-lg px-3 py-2 text-sm shadow-sm ${out ? "bg-accent/10 text-slate-800" : "bg-white text-slate-800"}`}>
        {senderLabel && <div className="mb-0.5 text-[11px] font-semibold text-accent">{senderLabel}</div>}
        {msg.media_type === "audio" ? (
          <div className="space-y-1">
            {msg.audio_url && <audio controls src={msg.audio_url} className="h-8" />}
            {msg.transcription && <div className="text-xs italic text-slate-500">“{msg.transcription}”</div>}
          </div>
        ) : msg.media_type === "unsupported" ? (
          <div className="text-xs italic text-slate-400">mensagem não suportada (tipo: {msg.media_type})</div>
        ) : (
          <div className="whitespace-pre-wrap">{msg.text}</div>
        )}
        <div className="mt-1 text-right text-[10px] text-slate-400">{formatTime(msg.at)}</div>
      </div>
    </div>
  );
}
