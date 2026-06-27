// frontend/src/components/Composer.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Composer } from "./Composer";

afterEach(() => vi.restoreAllMocks());

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

test("disabled with hint when not human", () => {
  wrap(<Composer slug="acme" leadId="l1" state="ai" windowOpen={true} />);
  expect(screen.getByText(/Assuma a conversa/)).toBeInTheDocument();
  expect(screen.getByTestId("composer-input")).toBeDisabled();
});

test("disabled with hint when 24h window closed", () => {
  wrap(<Composer slug="acme" leadId="l1" state="human" windowOpen={false} />);
  expect(screen.getByText(/Janela de 24h fechada/)).toBeInTheDocument();
  expect(screen.getByTestId("composer-input")).toBeDisabled();
});

test("sends when human + window open, then clears", async () => {
  const f = vi.fn(async () => new Response(JSON.stringify({ outbound_id: "o1", external_id: "x", status: "sent" }), { status: 200 }));
  vi.stubGlobal("fetch", f);
  wrap(<Composer slug="acme" leadId="l1" state="human" windowOpen={true} />);
  const input = screen.getByTestId("composer-input") as HTMLTextAreaElement;
  await userEvent.type(input, "olá mundo");
  await userEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(f).toHaveBeenCalled());
  expect(String((f.mock.calls as unknown[][])[0][0])).toContain("/contacts/l1/send");
  await waitFor(() => expect(input.value).toBe(""));
});

test("shows the backend error detail on failure", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "24h window closed; template required" }), { status: 422 })));
  wrap(<Composer slug="acme" leadId="l1" state="human" windowOpen={true} />);
  await userEvent.type(screen.getByTestId("composer-input"), "oi");
  await userEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(screen.getByText(/24h window closed/)).toBeInTheDocument());
});
