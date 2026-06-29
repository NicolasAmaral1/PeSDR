// frontend/src/components/LiveIndicator.test.tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { LiveIndicator } from "./LiveIndicator";

test("shows 'ao vivo' when connected", () => {
  render(<LiveIndicator connected={true} />);
  expect(screen.getByText(/ao vivo/i)).toBeInTheDocument();
});

test("shows 'reconectando' when disconnected", () => {
  render(<LiveIndicator connected={false} />);
  expect(screen.getByText(/reconectando/i)).toBeInTheDocument();
});
