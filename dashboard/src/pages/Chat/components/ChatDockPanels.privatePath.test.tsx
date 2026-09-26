import { useEffect, useRef, type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { requestMock, requestBlobMock, probeMock, messageMock } = vi.hoisted(
  () => ({
    requestMock: vi.fn(),
    requestBlobMock: vi.fn(),
    probeMock: vi.fn(),
    messageMock: {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    },
  }),
);

vi.mock("../../../api/request", () => ({
  request: requestMock,
  requestBlob: requestBlobMock,
  probeAuthResource: probeMock,
}));

vi.mock("@/utils/antdMessage", () => ({ message: messageMock }));

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 1 }),
}));

vi.mock("../../../utils/browserProfile", () => ({
  resolveBrowserProfile: () => "profile-1",
}));

vi.mock("../../../components/BrowserWorkspace", () => ({
  default: () => <div data-testid="browser-workspace" />,
}));

// The real shell places the tab bar in its title slot; render it so tests can
// assert tab identity / selection, not only panel bodies.
vi.mock("../../../components/BrowserWorkspace/ChatDockPanelShell", () => ({
  default: ({
    title,
    children,
  }: {
    title?: ReactNode;
    children?: ReactNode;
  }) => (
    <div data-testid="dock-shell">
      {title}
      {children}
    </div>
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

vi.mock("./KnowledgeCitationPanelContent", () => ({
  default: () => <div data-testid="knowledge-panel" />,
}));

vi.mock("./ChatDockToolUiContent", () => ({
  default: () => <div data-testid="tool-ui" />,
}));

vi.mock("../../Control/Terminal", () => ({
  default: () => <div data-testid="terminal-page" />,
}));

// The real FileViewer statically imports DocumentPreview → pdfjs, which needs
// browser canvas APIs jsdom lacks. Media previews stay real (they are the
// request seam under test).
vi.mock("../../Agent/Workspace/components/DocumentPreview", () => ({
  default: () => <div data-testid="document-preview" />,
}));

// The global setup's `useTranslation` returns a fresh `t` every render.
// FilePanelContent lists `t` (and callbacks derived from it) in its toolbar
// layout-effect deps, so an unstable `t` re-runs the effect, lifts new actions
// into the shell and re-renders forever. Real react-i18next keeps `t` stable.
const { stableT } = vi.hoisted(() => ({
  stableT: (
    key: string,
    fallback?: string | { defaultValue?: string },
  ): string => {
    if (typeof fallback === "string") return fallback;
    if (fallback && typeof fallback === "object") {
      return fallback.defaultValue ?? key;
    }
    return key;
  },
}));

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  return {
    ...actual,
    useTranslation: () => ({
      t: stableT,
      i18n: { language: "zh", changeLanguage: () => Promise.resolve() },
    }),
    Trans: ({ children }: { children?: ReactNode }) => children,
  };
});

import { useChatDockPanel } from "../hooks/useChatDockPanel";
import ChatDockPanels from "./ChatDockPanels";

const DELETED_COPY = "该文件可能为处理过程中的临时文件，当前已经被删除。";
const REFUSED_COPY =
  "该文件当前不可访问：任务未通过校验，或路径不在受控工作区内。";

/**
 * Drives the real ``useChatDockPanel`` → ``ChatDockPanels`` →
 * ``FilePanelContent`` (and real ``FileViewer``/``MediaPreview``) seam exactly
 * like ``Chat/index.tsx``: a private route opens tabs (in the given order)
 * and the panel renders its viewers. Unsafe raw tool shapes must never reach
 * file or media I/O, and must never reuse (or be reused by) a safe tab.
 */
function PrivateDock({ rawPaths }: { rawPaths: string[] }) {
  const dock = useChatDockPanel(false, "RT1", true);
  const openedRef = useRef(0);
  useEffect(() => {
    while (openedRef.current < rawPaths.length) {
      dock.openFileAt(rawPaths[openedRef.current]);
      openedRef.current += 1;
    }
  }, [dock, rawPaths]);

  return (
    <ChatDockPanels
      isMobile={false}
      dockOpen
      dockMode="right"
      isResizing={false}
      panelSizes={{ rightWidth: 560, bottomHeight: 380 }}
      agentId="RT1"
      privateTask
      filePaths={[]}
      artifacts={[]}
      openTabs={dock.openTabs}
      activeTabId={dock.activeTabId}
      onSelectTab={dock.setActiveTab}
      onCloseTab={dock.closeTab}
      onOpenFile={dock.openFileAt}
      browserEnvironment="host"
      onModeChange={vi.fn()}
      onClose={vi.fn()}
      onResizeStart={vi.fn()}
    />
  );
}

/**
 * Same seam, but the runtime id starts unverified (``null``) exactly like the
 * internal task route's first paint, then resolves to the server-verified id
 * through a rerender the test controls.
 */
function FirstPaintPrivateDock({
  rawPath,
  agentId,
}: {
  rawPath: string;
  agentId: string | null;
}) {
  const dock = useChatDockPanel(false, agentId, true);
  const openedRef = useRef(false);
  useEffect(() => {
    if (openedRef.current) return;
    openedRef.current = true;
    dock.openFileAt(rawPath);
  }, [dock, rawPath]);

  return (
    <ChatDockPanels
      isMobile={false}
      dockOpen
      dockMode="right"
      isResizing={false}
      panelSizes={{ rightWidth: 560, bottomHeight: 380 }}
      agentId={agentId ?? ""}
      privateTask
      filePaths={[]}
      artifacts={[]}
      openTabs={dock.openTabs}
      activeTabId={dock.activeTabId}
      onSelectTab={dock.setActiveTab}
      onCloseTab={dock.closeTab}
      onOpenFile={dock.openFileAt}
      browserEnvironment="host"
      onModeChange={vi.fn()}
      onClose={vi.fn()}
      onResizeStart={vi.fn()}
    />
  );
}

beforeEach(() => {
  requestMock.mockReset();
  requestBlobMock.mockReset();
  probeMock.mockReset();
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("private dock tab open → viewer seam", () => {
  it("reads a safe private text key from the opened tab in true mode", async () => {
    requestMock.mockResolvedValue({ content: "hello" });
    render(<PrivateDock rawPaths={["note.txt"]} />);

    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(1));
    expect(requestMock.mock.calls[0][0]).toBe(
      "/agents/RT1/workspace/file?path=note.txt&from_workspace=true",
    );
    expect(screen.getByText("hello")).toBeTruthy();
    expect(probeMock).not.toHaveBeenCalled();
  });

  it("opens a safe /workspace media key with a managed-relative true-mode preview", async () => {
    requestBlobMock.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    render(<PrivateDock rawPaths={["/workspace/note.png"]} />);

    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    expect(requestBlobMock.mock.calls[0][0]).toBe(
      "/agents/RT1/media/preview?source=note.png",
    );
    expect(requestMock).not.toHaveBeenCalled();
    expect(probeMock).not.toHaveBeenCalled();
    expect(screen.queryByText(REFUSED_COPY)).toBeNull();
    expect(screen.queryByText(DELETED_COPY)).toBeNull();
  });

  it.each([
    ["file:///workspace/note.png"],
    ["file:///etc/passwd"],
    ["/file:///workspace/note.png"],
    ["C:\\Users\\wally\\note.png"],
    ["\\\\nas\\share\\note.png"],
    ["../../etc/passwd"],
    ["notes/../../secret.txt"],
    ["~/secret/note.png"],
  ])(
    "refuses raw tab path %s with zero file or media preview requests",
    async (raw) => {
      render(<PrivateDock rawPaths={[raw]} />);

      await waitFor(() => expect(screen.getByText(REFUSED_COPY)).toBeTruthy());
      expect(requestMock).not.toHaveBeenCalled();
      expect(requestBlobMock).not.toHaveBeenCalled();
      expect(probeMock).not.toHaveBeenCalled();
      expect(screen.queryByText(DELETED_COPY)).toBeNull();
    },
  );
});

describe("private dock tab identity across safe/unsafe open orders", () => {
  it("opens a distinct refused tab when an unsafe form follows a safe key", async () => {
    requestBlobMock.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    render(
      <PrivateDock
        rawPaths={["/workspace/note.png", "file:///workspace/note.png"]}
      />,
    );

    // The safe media tab keeps its own managed-relative true-mode preview.
    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    expect(requestBlobMock.mock.calls[0][0]).toBe(
      "/agents/RT1/media/preview?source=note.png",
    );
    // The unsafe form must open (and focus) its own refused tab instead of
    // reselecting the safe viewer behind the colliding canonical key.
    await waitFor(() => expect(screen.getByText(REFUSED_COPY)).toBeTruthy());
    expect(screen.getAllByText(REFUSED_COPY)).toHaveLength(1);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3); // overview + safe + refused
    expect(tabs[2].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
    expect(requestMock).not.toHaveBeenCalled();
    expect(probeMock).not.toHaveBeenCalled();
    expect(screen.queryByText(DELETED_COPY)).toBeNull();
  });

  it("opens a working safe tab when a safe key follows an unsafe form", async () => {
    requestBlobMock.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    render(
      <PrivateDock
        rawPaths={["file:///workspace/note.png", "/workspace/note.png"]}
      />,
    );

    // The refused tab keeps the raw form and shows the truthful refusal.
    await waitFor(() => expect(screen.getByText(REFUSED_COPY)).toBeTruthy());
    expect(screen.getAllByText(REFUSED_COPY)).toHaveLength(1);
    // The safe key must open (and focus) its own working tab with a
    // managed-relative true-mode preview, not reselect the refused viewer.
    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    expect(requestBlobMock.mock.calls[0][0]).toBe(
      "/agents/RT1/media/preview?source=note.png",
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3); // overview + refused + safe
    expect(tabs[2].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
    expect(requestMock).not.toHaveBeenCalled();
    expect(probeMock).not.toHaveBeenCalled();
    expect(screen.queryByText(DELETED_COPY)).toBeNull();
  });

  it("keeps a first-paint tab and starts true-mode I/O when the runtime id verifies", async () => {
    requestMock.mockResolvedValue({ content: "hello" });
    const { rerender } = render(
      <FirstPaintPrivateDock rawPath="note.txt" agentId={null} />,
    );

    // Unverified first paint: the tab exists but performs zero file I/O.
    await waitFor(() => expect(screen.getByText(REFUSED_COPY)).toBeTruthy());
    expect(requestMock).not.toHaveBeenCalled();
    expect(requestBlobMock).not.toHaveBeenCalled();
    expect(probeMock).not.toHaveBeenCalled();

    // The first verified identity resolves the first paint; it is not an
    // agent switch, so the already-open tab must survive it.
    rerender(<FirstPaintPrivateDock rawPath="note.txt" agentId="RT1" />);

    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(1));
    expect(requestMock.mock.calls[0][0]).toBe(
      "/agents/RT1/workspace/file?path=note.txt&from_workspace=true",
    );
    await waitFor(() => expect(screen.getByText("hello")).toBeTruthy());
    expect(screen.queryByText(REFUSED_COPY)).toBeNull();
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(2); // overview + the surviving file tab
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
  });
});
