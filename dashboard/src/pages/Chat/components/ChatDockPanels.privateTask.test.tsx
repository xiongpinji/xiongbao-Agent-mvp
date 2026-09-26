import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatDockPanels from "./ChatDockPanels";

const { filePanelProps } = vi.hoisted(() => ({
  filePanelProps: [] as Array<Record<string, unknown>>,
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
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dock-shell">{children}</div>
  ),
}));

vi.mock("../../Agent/Workspace/components/WorkspaceDrawer", () => ({
  default: () => <div data-testid="workspace-drawer" />,
}));

vi.mock("./ChatDockFileList", () => ({
  default: () => <div data-testid="file-list" />,
}));

vi.mock("./ChatArtifactList", () => ({
  default: () => <div data-testid="artifact-list" />,
}));

vi.mock("./ChatDockOverview", () => ({
  default: () => <div data-testid="dock-overview" />,
}));

vi.mock("./FilePanelContent", () => ({
  default: (props: Record<string, unknown>) => {
    filePanelProps.push(props);
    return <div data-testid="file-panel" />;
  },
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

const fileTab = {
  id: "file:note.txt",
  kind: "file" as const,
  path: "note.txt",
};

const baseProps = {
  isMobile: false,
  dockMode: "right" as const,
  isResizing: false,
  panelSizes: { rightWidth: 560, bottomHeight: 380 },
  agentId: "RT1",
  filePaths: [] as string[],
  artifacts: [] as string[],
  openTabs: [fileTab],
  activeTabId: "file:note.txt" as string | null,
  onSelectTab: vi.fn(),
  onCloseTab: vi.fn(),
  onOpenFile: vi.fn(),
  browserEnvironment: "host" as const,
  onModeChange: vi.fn(),
  onClose: vi.fn(),
  onResizeStart: vi.fn(),
};

describe("ChatDockPanels private-task flag threading", () => {
  beforeEach(() => {
    filePanelProps.length = 0;
  });

  it("marks file tabs of an owner-private 030 route as private tasks", () => {
    render(<ChatDockPanels {...baseProps} dockOpen privateTask />);

    expect(screen.getByTestId("file-panel")).toBeTruthy();
    const last = filePanelProps[filePanelProps.length - 1];
    expect(last.agentId).toBe("RT1");
    expect(last.filePath).toBe("note.txt");
    expect(last.privateTask).toBe(true);
  });

  it("leaves ordinary agent file tabs in host mode", () => {
    render(<ChatDockPanels {...baseProps} dockOpen agentId="main" />);

    expect(screen.getByTestId("file-panel")).toBeTruthy();
    const last = filePanelProps[filePanelProps.length - 1];
    expect(Boolean(last.privateTask)).toBe(false);
  });
});
