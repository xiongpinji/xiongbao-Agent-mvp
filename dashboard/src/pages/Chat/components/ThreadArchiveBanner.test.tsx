import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { expect, it, vi } from "vitest";
import ThreadArchiveBanner from "./ThreadArchiveBanner";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
function Location() {
  return <span data-testid="path">{useLocation().pathname}</span>;
}
it("restores once while retaining the existing route, composer draft and focus", async () => {
  let settle!: (value: {
    status: "saved";
    receipt: { thread_id: string; agent_id: string; archived_at: null };
  }) => void;
  const pending = new Promise<{
    status: "saved";
    receipt: { thread_id: string; agent_id: string; archived_at: null };
  }>((resolve) => {
    settle = resolve;
  });
  const restore = vi.fn(() => pending);
  render(
    <MemoryRouter initialEntries={["/chat/a/archived"]}>
      <ThreadArchiveBanner onRestore={restore} />
      <textarea defaultValue="unsent draft" aria-label="composer" />
      <Location />
    </MemoryRouter>,
  );
  const input = screen.getByRole("textbox");
  input.focus();
  const button = screen.getByRole("button", { name: "archive.restore" });
  fireEvent.click(button);
  expect(
    screen.getByRole("button", { name: "dataManagement.restoring" }),
  ).toBeDisabled();
  fireEvent.click(button);
  expect(restore).toHaveBeenCalledTimes(1);
  await act(async () =>
    settle({
      status: "saved",
      receipt: { thread_id: "archived", agent_id: "a", archived_at: null },
    }),
  );
  expect(input).toHaveValue("unsent draft");
  expect(input).toHaveFocus();
  expect(screen.getByTestId("path")).toHaveTextContent("/chat/a/archived");
});

it("keeps the restore control and draft available after a failed save", async () => {
  const restore = vi.fn().mockResolvedValue({ status: "failed" });
  render(
    <MemoryRouter initialEntries={["/chat/a/archived"]}>
      <ThreadArchiveBanner onRestore={restore} />
      <textarea defaultValue="unsent draft" aria-label="composer" />
      <Location />
    </MemoryRouter>,
  );
  const input = screen.getByRole("textbox");
  input.focus();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "archive.restore" }));
  });
  expect(screen.getByRole("button", { name: "archive.restore" })).toBeEnabled();
  expect(screen.getByRole("status")).toBeInTheDocument();
  expect(input).toHaveValue("unsent draft");
  expect(input).toHaveFocus();
  expect(screen.getByTestId("path")).toHaveTextContent("/chat/a/archived");
  expect(restore).toHaveBeenCalledOnce();
});
