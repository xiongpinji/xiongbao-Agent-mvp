import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ChatArtifactList from "./ChatArtifactList";

describe("ChatArtifactList", () => {
  it("shows an empty state when the active thread has no artifacts", () => {
    render(
      <ChatArtifactList
        agentId="agent-a"
        artifacts={[]}
        onOpenFile={vi.fn()}
      />,
    );
    expect(screen.getByText("当前任务暂无产物")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("lists duplicate artifact paths once", () => {
    render(
      <ChatArtifactList
        agentId="main"
        artifacts={[
          "/home/wally/.octop/agents/main/outbound/report.pdf",
          "/.octop/agents/main/outbound/report.pdf",
        ]}
        onOpenFile={vi.fn()}
      />,
    );
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByText("report.pdf")).toBeTruthy();
  });

  it("opens the existing file preview when an artifact row is clicked", async () => {
    const user = userEvent.setup();
    const onOpenFile = vi.fn();
    render(
      <ChatArtifactList
        agentId="main"
        artifacts={["outbound/report.pdf"]}
        onOpenFile={onOpenFile}
      />,
    );
    await user.click(screen.getByRole("button", { name: /report\.pdf/ }));
    expect(onOpenFile).toHaveBeenCalledTimes(1);
    expect(onOpenFile).toHaveBeenCalledWith("outbound/report.pdf");
  });

  it("shows only the current thread artifacts after a thread switch", () => {
    const { rerender } = render(
      <ChatArtifactList
        agentId="main"
        artifacts={["outbound/a.pdf"]}
        onOpenFile={vi.fn()}
      />,
    );
    expect(screen.getByText("a.pdf")).toBeTruthy();

    rerender(
      <ChatArtifactList agentId="main" artifacts={[]} onOpenFile={vi.fn()} />,
    );
    expect(screen.queryByText("a.pdf")).toBeNull();
    expect(screen.getByText("当前任务暂无产物")).toBeTruthy();
  });
});
