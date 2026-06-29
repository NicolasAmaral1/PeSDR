import { Fragment } from "react";
import type { MessageOut, TalkBand } from "../types";
import { MessageBubble } from "./MessageBubble";
import { TalkDelimiter } from "./TalkDelimiter";

export function MessageStream({ messages, talks }: { messages: MessageOut[]; talks: TalkBand[] }) {
  const sortedMsgs = [...messages].sort((a, b) => a.at.localeCompare(b.at));
  const sortedTalks = [...talks].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const placed = new Set<string>();

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-4">
      {sortedMsgs.map((m) => {
        // any talk band whose start is <= this message and not yet placed → emit before it
        const due = sortedTalks.filter((t) => !placed.has(t.talk_id) && t.created_at <= m.at);
        due.forEach((t) => placed.add(t.talk_id));
        return (
          <Fragment key={m.id}>
            {due.map((t) => (
              <TalkDelimiter key={t.talk_id} band={t} />
            ))}
            <MessageBubble msg={m} />
          </Fragment>
        );
      })}
      {/* trailing talks that start after the last message (e.g. a freshly opened Talk) */}
      {sortedTalks
        .filter((t) => !placed.has(t.talk_id))
        .map((t) => (
          <TalkDelimiter key={t.talk_id} band={t} />
        ))}
    </div>
  );
}
