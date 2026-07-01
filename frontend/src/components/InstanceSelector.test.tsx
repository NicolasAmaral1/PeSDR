// frontend/src/components/InstanceSelector.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { InstanceSelector } from "./InstanceSelector";
import type { InstanceOut } from "../types";

const instances: InstanceOut[] = [
  { id: "a", channel_label: "main", display_name: "Main", phone_e164: "+551199" },
  { id: "b", channel_label: "vendas", display_name: "Vendas", phone_e164: null },
];

test("shows the selected instance and switches on pick", async () => {
  const onChange = vi.fn();
  render(<InstanceSelector instances={instances} value="a" onChange={onChange} />);
  expect(screen.getByTestId("instance-current")).toHaveTextContent("Main");
  await userEvent.click(screen.getByTestId("instance-trigger"));
  await userEvent.click(screen.getByText("Vendas"));
  expect(onChange).toHaveBeenCalledWith("b");
});
