// frontend/src/components/StateBadge.test.tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { StateBadge } from "./StateBadge";

test.each([
  ["ai", "IA"],
  ["requires_review", "Revisão"],
  ["human", "Humano"],
  ["awaiting", "Aguardando"],
  ["closed", "Encerrada"],
] as const)("renders %s as %s", (state, label) => {
  render(<StateBadge state={state} />);
  expect(screen.getByText(new RegExp(label))).toBeInTheDocument();
});
