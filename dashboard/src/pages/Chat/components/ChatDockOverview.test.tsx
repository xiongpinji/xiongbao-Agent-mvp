import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ChatDockOverview from "./ChatDockOverview";

describe("ChatDockOverview", () => {
  it("shows greeting, current task status and opens the artifacts tab", async () => {
    const user = userEvent.setup();
    const onOpenArtifacts = vi.fn();
    render(
      <ChatDockOverview
        agentId="agent-a"
        agentName="小帮手"
        threadTitle="整理季度报告"
        isStreaming={false}
        artifacts={[]}
        onOpenArtifacts={onOpenArtifacts}
        onOpenFile={vi.fn()}
      />,
    );
    expect(screen.getByText("你好，小帮手")).toBeTruthy();
    expect(screen.getByText("整理季度报告")).toBeTruthy();
    expect(screen.getByText("任务待命中")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /查看产物/ }));
    expect(onOpenArtifacts).toHaveBeenCalledTimes(1);
  });

  it("shows running status and opens recent artifacts through the preview handler", async () => {
    const user = userEvent.setup();
    const onOpenFile = vi.fn();
    render(
      <ChatDockOverview
        agentId="main"
        isStreaming
        artifacts={["outbound/report.pdf"]}
        onOpenArtifacts={vi.fn()}
        onOpenFile={onOpenFile}
      />,
    );
    expect(screen.getByText("任务进行中…")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /report\.pdf/ }));
    expect(onOpenFile).toHaveBeenCalledWith("outbound/report.pdf");
  });

  it("shows an empty recent-artifacts hint for an empty thread", () => {
    render(
      <ChatDockOverview
        agentId="main"
        artifacts={[]}
        onOpenArtifacts={vi.fn()}
        onOpenFile={vi.fn()}
      />,
    );
    expect(screen.getByText("暂无产物")).toBeTruthy();
  });
});
