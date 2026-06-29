import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { MessageStream } from "./MessageStream";
import type { MessageOut, TalkBand } from "../types";

const messages: MessageOut[] = [
  { id: "m1", direction: "in", origin: "lead", text: "oi", media_type: "text", audio_url: null, transcription: null, at: "2026-06-26T10:00:00Z" },
  { id: "m2", direction: "out", origin: "ai", text: "olá!", media_type: "text", audio_url: null, transcription: null, at: "2026-06-26T10:01:00Z" },
  { id: "m3", direction: "out", origin: "operator", text: "aqui é a Ana", media_type: "text", audio_url: null, transcription: null, at: "2026-06-27T09:00:00Z" },
];
const talks: TalkBand[] = [
  { talk_id: "t1", status: "closed", funnel_node: "boas-vindas", created_at: "2026-06-26T09:59:00Z" },
  { talk_id: "t2", status: "active", funnel_node: "humano", created_at: "2026-06-27T08:59:00Z" },
];

test("renders bubbles and a delimiter at each Talk boundary", () => {
  render(<MessageStream messages={messages} talks={talks} />);
  expect(screen.getByText("oi")).toBeInTheDocument();
  expect(screen.getByText("aqui é a Ana")).toBeInTheDocument();
  // one delimiter per talk band
  expect(screen.getAllByTestId("talk-delimiter")).toHaveLength(2);
  // operator bubble labelled "Você"
  expect(screen.getByText("Você")).toBeInTheDocument();
});
