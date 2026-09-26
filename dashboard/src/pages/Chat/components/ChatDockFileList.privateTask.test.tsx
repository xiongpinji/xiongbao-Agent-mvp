import type { ComponentProps, ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { requestBlobMock, messageMock, clickedDownloads } = vi.hoisted(() => ({
  requestBlobMock: vi.fn(),
  messageMock: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
  clickedDownloads: [] as string[],
}));

vi.mock("../../../api/request", () => ({
  request: vi.fn(),
  requestBlob: requestBlobMock,
  probeAuthResource: vi.fn(),
}));

vi.mock("@/utils/antdMessage", () => ({ message: messageMock }));

// Sibling dock bodies are irrelevant here; only ChatDockFileList stays real
// so the row download button exercises the true path pipeline.
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
  CHAT_DOCK_POPUP_MENU_Z: 1000,
  default: ({ children }: { children?: ReactNode }) => (
    <div data-testid="dock-shell">{children}</div>
  ),
}));

vi.mock("../../Agent/Workspace/components/WorkspaceDrawer", () => ({
  default: () => <div data-testid="workspace-drawer" />,
}));

vi.mock("./ChatArtifactList", () => ({
  default: () => <div data-testid="artifact-list" />,
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

// The global setup's `useTranslation` returns a fresh `t` every render;
// keep `t` stable like the real react-i18next (FilePanelContent suite pattern).
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

import ChatDockFileList from "./ChatDockFileList";
import ChatDockPanel from "./ChatDockPanel";

const PRIVATE_DOWNLOAD_URL =
  "/agents/RT1/workspace/download?path=note.txt&from_workspace=true";

let anchorClickSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  requestBlobMock.mockReset();
  messageMock.error.mockReset();
  messageMock.success.mockReset();
  messageMock.warning.mockReset();
  clickedDownloads.length = 0;
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  anchorClickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(function (this: HTMLAnchorElement) {
      clickedDownloads.push(this.download);
    });
});

afterEach(() => {
  anchorClickSpy.mockRestore();
  vi.restoreAllMocks();
});

function renderList(
  props: Partial<ComponentProps<typeof ChatDockFileList>> = {},
) {
  return render(
    <ChatDockFileList
      agentId="RT1"
      filePaths={["note.txt"]}
      privateTask
      onOpenFile={vi.fn()}
      {...props}
    />,
  );
}

function renderPanel(props: Partial<ComponentProps<typeof ChatDockPanel>>) {
  return render(
    <ChatDockPanel
      mode="right"
      onModeChange={vi.fn()}
      onClose={vi.fn()}
      agentId="RT1"
      filePaths={["note.txt"]}
      artifacts={[]}
      openTabs={[{ id: "files", kind: "files" }]}
      activeTabId="files"
      onSelectTab={vi.fn()}
      onCloseTab={vi.fn()}
      onOpenFile={vi.fn()}
      {...props}
    />,
  );
}

const downloadButton = () => screen.getByRole("button", { name: "下载" });

describe("ChatDockFileList — verified owner-private 030 row downloads", () => {
  it.each([["note.txt"], ["/workspace/note.txt"], ["/note.txt"]])(
    "downloads private row %s with exactly one true-mode managed-relative request",
    async (filePath) => {
      requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
      const user = userEvent.setup();
      renderList({ filePaths: [filePath] });

      await user.click(downloadButton());

      await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
      expect(requestBlobMock.mock.calls[0][0]).toBe(PRIVATE_DOWNLOAD_URL);
      // Blob URL + filename behavior preserved.
      await waitFor(() => expect(clickedDownloads).toEqual(["note.txt"]));
      expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
      expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    },
  );

  it("keeps the failure toast behavior on a private download error", async () => {
    requestBlobMock.mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    renderList({ filePaths: ["note.txt"] });

    await user.click(downloadButton());

    await waitFor(() => expect(messageMock.error).toHaveBeenCalledTimes(1));
    expect(messageMock.error).toHaveBeenCalledWith("boom");
    expect(clickedDownloads).toEqual([]);
  });
});

describe("ChatDockFileList — private refusals perform zero requests", () => {
  it.each([
    ["file:///etc/passwd"],
    ["file:///workspace/note.txt"],
    ["C:\\Users\\wally\\note.txt"],
    ["\\\\host\\share"],
    ["//host/share"],
    ["~/secret/note.txt"],
    ["../etc/passwd"],
    ["note\x00.txt"],
    ["/file:///etc/passwd"],
    ["/file:///workspace/note.txt"],
    ["\\file:///etc/passwd"],
    ["\\\\file:///etc/passwd"],
    ["\\/file:///etc/passwd"],
    ["/file:\\home\\wally\\note.txt"],
    ["/C:/Users/wally/note.txt"],
    ["\\C:\\Users\\wally\\note.txt"],
    ["/workspace/C:/Users/wally/note.txt"],
    ["/~/secret/note.txt"],
    ["\\~\\secret\\note.txt"],
    ["/workspace/~/secret/note.txt"],
  ])("refuses private row %s with zero download requests", async (bad) => {
    requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
    const user = userEvent.setup();
    renderList({ filePaths: [bad] });

    await user.click(downloadButton());

    expect(requestBlobMock).not.toHaveBeenCalled();
    expect(clickedDownloads).toEqual([]);
    // Refused before setDownloading: the row never enters a busy state.
    expect(downloadButton()).toBeEnabled();
  });

  it.each([
    [["/workspace/note.txt", "file:///workspace/note.txt"]],
    [["file:///workspace/note.txt", "/workspace/note.txt"]],
  ])(
    "refuses a row reachable from both safe and unsafe raw shapes in order %j",
    async (filePaths) => {
      requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
      const user = userEvent.setup();
      renderList({ filePaths });

      await user.click(downloadButton());

      expect(requestBlobMock).not.toHaveBeenCalled();
      expect(clickedDownloads).toEqual([]);
      expect(downloadButton()).toBeEnabled();
    },
  );

  it("performs zero requests on an unverified private route (empty agentId)", async () => {
    requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
    const user = userEvent.setup();
    renderList({ agentId: "", filePaths: ["note.txt"] });

    await user.click(downloadButton());

    expect(requestBlobMock).not.toHaveBeenCalled();
    expect(clickedDownloads).toEqual([]);
    expect(downloadButton()).toBeEnabled();
  });
});

describe("ChatDockFileList — ordinary agent rows keep host-mode URLs", () => {
  it("keeps an ordinary host absolute row on its file:// host-mode URL without from_workspace", async () => {
    requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
    const user = userEvent.setup();
    renderList({
      agentId: "main",
      privateTask: false,
      filePaths: ["/home/wally/note.txt"],
    });

    await user.click(downloadButton());

    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    const url = requestBlobMock.mock.calls[0][0] as string;
    expect(url).toBe(
      "/agents/main/workspace/download?path=file%3A%2F%2F%2Fhome%2Fwally%2Fnote.txt",
    );
    expect(url).not.toContain("from_workspace");
    await waitFor(() => expect(clickedDownloads).toEqual(["note.txt"]));
  });

  it("keeps an ordinary agent-home absolute row on the uncollapsed file:// URL", async () => {
    requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
    const user = userEvent.setup();
    renderList({
      agentId: "main",
      privateTask: false,
      filePaths: ["/home/wally/.octop/agents/main/outbound/report.pdf"],
    });

    await user.click(downloadButton());

    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    const url = requestBlobMock.mock.calls[0][0] as string;
    expect(url).toBe(
      "/agents/main/workspace/download?path=file%3A%2F%2F%2Fhome%2Fwally%2F.octop%2Fagents%2Fmain%2Foutbound%2Freport.pdf",
    );
    expect(url).not.toContain("from_workspace");
    await waitFor(() => expect(clickedDownloads).toEqual(["report.pdf"]));
  });

  it("keeps an ordinary relative row on the host-mode URL without from_workspace", async () => {
    requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
    const user = userEvent.setup();
    renderList({
      agentId: "main",
      privateTask: false,
      filePaths: ["outbound/a.txt"],
    });

    await user.click(downloadButton());

    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    const url = requestBlobMock.mock.calls[0][0] as string;
    expect(url).toBe("/agents/main/workspace/download?path=outbound%2Fa.txt");
    expect(url).not.toContain("from_workspace");
    await waitFor(() => expect(clickedDownloads).toEqual(["a.txt"]));
  });
});

describe("ChatDockPanel — private flag threading into the dock file list", () => {
  it("threads privateTask so 已发现文件 row downloads are true-mode", async () => {
    requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
    const user = userEvent.setup();
    renderPanel({ privateTask: true });

    await user.click(await screen.findByRole("button", { name: "下载" }));

    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    expect(requestBlobMock.mock.calls[0][0]).toBe(PRIVATE_DOWNLOAD_URL);
  });

  it("keeps ordinary dock file list rows in host mode", async () => {
    requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
    const user = userEvent.setup();
    renderPanel({ agentId: "main", filePaths: ["/home/wally/note.txt"] });

    await user.click(await screen.findByRole("button", { name: "下载" }));

    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    const url = requestBlobMock.mock.calls[0][0] as string;
    expect(url).toBe(
      "/agents/main/workspace/download?path=file%3A%2F%2F%2Fhome%2Fwally%2Fnote.txt",
    );
    expect(url).not.toContain("from_workspace");
  });
});
