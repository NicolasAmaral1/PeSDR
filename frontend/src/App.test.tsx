import { render, screen } from "@testing-library/react";
import App from "./App";

test("App boots and renders the brand wordmark", () => {
  render(<App />);
  expect(screen.getByTestId("app-boot")).toHaveTextContent("Avelum Labs");
});
