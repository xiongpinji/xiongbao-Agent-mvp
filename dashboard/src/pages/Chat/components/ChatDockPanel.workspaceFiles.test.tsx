import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatDockPanel from "./ChatDockPanel";

const captured = vi.hoisted(() => ({
  workspaceDrawer: [] as Array<{
    agentId?: string;
    open?: boolean;
    embedded?: boolean;
  }>,
  fileList: [] as Array<{ agentId?: string; filePaths?: string[] }>,
}));

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 1 }),
}));

vi.mock("../../../utils/browserProfile", () => ({
  resolveBrowserProfile: () => "profile-1",
}));

vi.mock("../../../components/BrowserWorkspace", () => ({
  default: () => <div data-testid="browser-workspace" />,
}));

vi.mock("../../../components/BrowserWorkspace/ChatDockPanelShell", () => ({
  default: ({
    title,
    children,
  }: {
    title?: React.ReactNode;
    children: React.ReactNode;
  }) => (
    <div data-testid="dock-shell">
      <div data-testid="dock-title">{title}</div>
      {children}
    </div>
  ),
}));

vi.mock("../../Agent/Workspace/components/WorkspaceDrawer", () => ({
  default: (props: {
    agentId?: string;
    open?: boolean;
    embedded?: boolean;
  }) => {
    captured.workspaceDrawer.push(props);
    return <div data-testid="workspace-drawer" />;
  },
}));

vi.mock("./ChatDockFileList", () => ({
  default: ({
    agentId,
    filePaths,
  }: {
    agentId: string;
    filePaths: string[];
  }) => {
    captured.fileList.push({ agentId, filePaths });
    return (
      <div
        data-testid="file-list"
        data-agent-id={agentId}
        data-file-paths={filePaths.join(",")}
      />
    );
  },
}));

vi.mock("./ChatArtifactList", () => ({
  default: ({ artifacts }: { artifacts: string[] }) => (
    <div data-testid="artifact-list" data-artifacts={artifacts.join(",")} />
  ),
}));

vi.mock("./ChatDockOverview", () => ({
  default: () => <div data-testid="dock-overview" />,
}));

vi.mock("./FilePanelContent", () => ({
  default: () => <div data-testid="file-panel" />,
}));

vi.mock("./KnowledgeCitationPanelContent", () => ({
  default: () => <div data-testid="knowledge-panel" />,
}));

vi.mock("./ChatDockToolUiContent", () => ({
  default: () => <div data-testid="tool-ui" />,
}));

vi.mock("../../Control/Terminal", () => ({
  default: () => <div data-testid="terminal-page" />,
}));

const baseProps = {
  mode: "right" as const,
  onModeChange: vi.fn(),
  onClose: vi.fn(),
  agentId: "agent-a",
  filePaths: [] as string[],
  artifacts: [] as string[],
  openTabs: [{ id: "terminal" as const, kind: "terminal" as const }],
  activeTabId: "terminal" as const,
  onSelectTab: vi.fn(),
  onCloseTab: vi.fn(),
  onOpenFile: vi.fn(),
};

const lastWorkspaceDrawerProps = () =>
  captured.workspaceDrawer[captured.workspaceDrawer.length - 1];

describe("ChatDockPanel workspace files vs detected files", () => {
  beforeEach(() => {
    captured.workspaceDrawer.length = 0;
    captured.fileList.length = 0;
  });

  it("names the + menu entries 产物 / 工作空间文件 / 已发现文件, not 文件变更", async () => {
    const user = userEvent.setup();
    const onOpenArtifacts = vi.fn();
    const onOpenWorkspace = vi.fn();
    const onOpenFiles = vi.fn();

    render(
      <ChatDockPanel
        {...baseProps}
        addTab={{ onOpenArtifacts, onOpenWorkspace, onOpenFiles }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "添加面板" }));
    expect(await screen.findByText("产物")).toBeTruthy();
    await user.click(await screen.findByText("工作空间文件"));
    expect(onOpenWorkspace).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "添加面板" }));
    await user.click(await screen.findByText("已发现文件"));
    expect(onOpenFiles).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "添加面板" }));
    await user.click(await screen.findByText("产物"));
    expect(onOpenArtifacts).toHaveBeenCalledTimes(1);

    expect(screen.queryByText("文件变更")).toBeNull();
  });

  it("labels open tabs 工作空间文件 / 已发现文件 / 产物 without legacy wording", () => {
    render(
      <ChatDockPanel
        {...baseProps}
        openTabs={[
          { id: "workspace", kind: "workspace" },
          { id: "files", kind: "files" },
          { id: "artifacts", kind: "artifacts" },
        ]}
        activeTabId="workspace"
      />,
    );

    const title = screen.getByTestId("dock-title");
    expect(within(title).getByText("工作空间文件")).toBeTruthy();
    expect(within(title).getByText("已发现文件")).toBeTruthy();
    expect(within(title).getByText("产物")).toBeTruthy();
    expect(within(title).queryByText("文件变更")).toBeNull();
    expect(within(title).queryByText("工作区")).toBeNull();
  });

  it("mounts the embedded authorized workspace tree for the current agent", () => {
    const { rerender } = render(
      <ChatDockPanel
        {...baseProps}
        agentId="agent-b"
        openTabs={[{ id: "workspace", kind: "workspace" }]}
        activeTabId="workspace"
      />,
    );

    expect(screen.getByTestId("workspace-drawer")).toBeTruthy();
    expect(lastWorkspaceDrawerProps()?.agentId).toBe("agent-b");
    expect(lastWorkspaceDrawerProps()?.embedded).toBe(true);
    expect(lastWorkspaceDrawerProps()?.open).toBe(true);

    rerender(
      <ChatDockPanel
        {...baseProps}
        agentId="agent-c"
        openTabs={[{ id: "workspace", kind: "workspace" }]}
        activeTabId="workspace"
      />,
    );
    expect(lastWorkspaceDrawerProps()?.agentId).toBe("agent-c");
  });

  it("keeps tool file paths in 已发现文件 and never in thread 产物", () => {
    render(
      <ChatDockPanel
        {...baseProps}
        agentId="agent-c"
        filePaths={["outbound/tool-only.txt"]}
        artifacts={["outbound/deliverable.pdf"]}
        openTabs={[
          { id: "files", kind: "files" },
          { id: "artifacts", kind: "artifacts" },
        ]}
        activeTabId="files"
      />,
    );

    const fileList = screen.getByTestId("file-list");
    expect(fileList.getAttribute("data-agent-id")).toBe("agent-c");
    expect(fileList.getAttribute("data-file-paths")).toBe(
      "outbound/tool-only.txt",
    );
    expect(
      screen.getByTestId("artifact-list").getAttribute("data-artifacts"),
    ).toBe("outbound/deliverable.pdf");
  });

  it("keeps 工作空间文件 disabled with the existing hint when the agent is not ready", async () => {
    const user = userEvent.setup();
    const onOpenWorkspace = vi.fn();

    render(
      <ChatDockPanel
        {...baseProps}
        addTab={{
          onOpenWorkspace,
          workspaceDisabled: true,
          workspaceDisabledHint: "requires running",
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "添加面板" }));
    const item = (await screen.findByText("工作空间文件")).closest("li");
    expect(item?.getAttribute("title")).toBe("requires running");
    expect(item?.className).toContain("disabled");

    await user.click(screen.getByText("工作空间文件"));
    expect(onOpenWorkspace).not.toHaveBeenCalled();
  });
});
