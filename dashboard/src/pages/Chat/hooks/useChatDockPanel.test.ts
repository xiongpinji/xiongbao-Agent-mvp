import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { dockFileTabId } from "../utils/dockFilePath";
import { dockKnowledgeTabId } from "../utils/dockKnowledgeTabId";
import { ensureNoTrajectoryTab, useChatDockPanel } from "./useChatDockPanel";

describe("useChatDockPanel tabs", () => {
  it("opens a closable overview tab on a fresh desktop chat", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.activeTabId).toBe("overview");
    expect(result.current.openTabs).toEqual([
      { id: "overview", kind: "overview" },
    ]);

    act(() => {
      result.current.handleClose();
    });
    expect(result.current.dockOpen).toBe(false);
    // Keep-alive: the overview tab stays for reopening.
    expect(result.current.openTabs.map((t) => t.id)).toEqual(["overview"]);

    act(() => {
      result.current.openOverviewTab();
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.activeTabId).toBe("overview");
    expect(result.current.openTabs.map((t) => t.id)).toEqual(["overview"]);
  });

  it("starts collapsed with no tabs on mobile", () => {
    const { result } = renderHook(() => useChatDockPanel(true));
    expect(result.current.dockOpen).toBe(false);
    expect(result.current.openTabs).toEqual([]);
    expect(result.current.activeTabId).toBeNull();
  });

  it("openFileList focuses the pinned files tab", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.openFileList();
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.activeTabId).toBe("files");
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "files",
      "overview",
    ]);
  });

  it("openFileAt dedupes by normalized path and focuses the file tab", () => {
    const { result } = renderHook(() => useChatDockPanel(false, "main"));
    act(() => {
      result.current.openFileAt(
        "/home/wally/.octop/agents/main/outbound/a.txt",
      );
    });
    act(() => {
      result.current.openFileAt("/.octop/agents/main/outbound/a.txt");
    });
    const fileId = dockFileTabId("outbound/a.txt", "main");
    expect(
      result.current.openTabs.filter((t) => t.kind === "file"),
    ).toHaveLength(1);
    expect(result.current.activeTabId).toBe(fileId);
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "overview",
      fileId,
    ]);
  });

  it("openArtifactsTab opens a distinct artifacts tab without duplicates", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.openArtifactsTab();
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.activeTabId).toBe("artifacts");
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "overview",
      "artifacts",
    ]);
    act(() => {
      result.current.openArtifactsTab();
    });
    expect(
      result.current.openTabs.filter((t) => t.kind === "artifacts"),
    ).toHaveLength(1);
  });

  it("openKnowledgeCitation opens a dock tab and dedupes by kb/doc id", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    const citation = {
      kbId: "kb1",
      docId: "doc1",
      kbName: "Personal",
      filename: "a.md",
    };
    act(() => {
      result.current.openKnowledgeCitation(citation);
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.activeTabId).toBe(dockKnowledgeTabId("kb1", "doc1"));
    act(() => {
      result.current.openKnowledgeCitation({
        ...citation,
        filename: "renamed.md",
      });
    });
    const knowledgeTabs = result.current.openTabs.filter(
      (t) => t.kind === "knowledge",
    );
    expect(knowledgeTabs).toHaveLength(1);
    expect(knowledgeTabs[0]).toMatchObject({
      kind: "knowledge",
      citation: { filename: "renamed.md" },
    });
  });

  it("toggleWorkspacePanel opens workspace tab then closes dock when active", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.toggleWorkspacePanel();
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.activeTabId).toBe("workspace");
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "overview",
      "workspace",
    ]);
    act(() => {
      result.current.toggleWorkspacePanel();
    });
    expect(result.current.dockOpen).toBe(false);
  });

  it("toggleBrowserPanel opens browser tab then closes dock when active", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.toggleBrowserPanel();
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.activeTabId).toBe("browser");
    act(() => {
      result.current.toggleBrowserPanel();
    });
    expect(result.current.dockOpen).toBe(false);
  });

  it("toggleTerminalPanel opens terminal tab then closes dock when active", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.toggleTerminalPanel();
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.activeTabId).toBe("terminal");
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "overview",
      "terminal",
    ]);
    act(() => {
      result.current.toggleTerminalPanel();
    });
    expect(result.current.dockOpen).toBe(false);
    // Closing the float button only hides the dock — tab stays for keep-alive.
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "overview",
      "terminal",
    ]);
  });

  it("ensureNoTrajectoryTab strips leftover trajectory tabs", () => {
    expect(
      ensureNoTrajectoryTab([
        { id: "files", kind: "files" },
        { id: "trajectory", kind: "trajectory" },
        { id: "browser", kind: "browser" },
      ]),
    ).toEqual([
      { id: "files", kind: "files" },
      { id: "browser", kind: "browser" },
    ]);
  });

  it("reopening terminal does not add another dock tab", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.toggleTerminalPanel();
    });
    act(() => {
      result.current.toggleTerminalPanel();
    });
    act(() => {
      result.current.toggleTerminalPanel();
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "overview",
      "terminal",
    ]);
  });

  it("openTerminalTab adds and focuses the terminal tab", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.openBrowserTab();
      result.current.openTerminalTab();
    });
    expect(result.current.activeTabId).toBe("terminal");
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "overview",
      "browser",
      "terminal",
    ]);
  });

  it("closeTab can close the files list tab", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.openFileList();
      result.current.openBrowserTab();
    });
    act(() => {
      result.current.closeTab("files");
    });
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "overview",
      "browser",
    ]);
    expect(result.current.activeTabId).toBe("browser");
    expect(result.current.dockOpen).toBe(true);
  });

  it("closeTab closes the dock when the last tab is closed", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.closeTab("overview");
    });
    expect(result.current.openTabs).toEqual([]);
    expect(result.current.dockOpen).toBe(false);
    expect(result.current.activeTabId).toBeNull();
  });

  it("openToolUiTab dedupes by callId and focuses the tool tab", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.openToolUiTab({
        callId: "call-1",
        title: "Demo card",
        toolName: "demo_card",
      });
    });
    act(() => {
      result.current.openToolUiTab({
        callId: "call-1",
        title: "Demo card",
      });
    });
    expect(result.current.dockOpen).toBe(true);
    expect(
      result.current.openTabs.filter((t) => t.kind === "toolUi"),
    ).toHaveLength(1);
    expect(result.current.activeTabId).toBe("toolUi:call-1");
    expect(
      result.current.openTabs.find((t) => t.kind === "toolUi"),
    ).toMatchObject({
      kind: "toolUi",
      callId: "call-1",
      title: "Demo card",
    });
  });

  it("focusToolUiTab reopens dock on an existing tool tab", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.openToolUiTab({ callId: "call-2", title: "Card" });
      result.current.handleClose();
    });
    expect(result.current.dockOpen).toBe(false);
    // Closing the dock drops toolUi tabs so the message stream restores.
    expect(
      result.current.openTabs.filter((t) => t.kind === "toolUi"),
    ).toHaveLength(0);
    act(() => {
      result.current.openToolUiTab({ callId: "call-2", title: "Card" });
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.activeTabId).toBe("toolUi:call-2");
  });

  it("handleClose removes toolUi tabs but keeps other tabs", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    act(() => {
      result.current.openBrowserTab();
      result.current.openToolUiTab({ callId: "call-3", title: "Card" });
    });
    act(() => {
      result.current.handleClose();
    });
    expect(result.current.dockOpen).toBe(false);
    expect(result.current.openTabs.map((t) => t.id)).toEqual([
      "overview",
      "browser",
    ]);
  });

  it("does not expose deprecated dismiss / kind aliases", () => {
    const { result } = renderHook(() => useChatDockPanel(false));
    expect(result.current).not.toHaveProperty("userDismissedRef");
    expect(result.current).not.toHaveProperty("dockKind");
    expect(result.current).not.toHaveProperty("openFilePanel");
    expect(result.current).not.toHaveProperty("openBrowserPanel");
    expect(result.current).not.toHaveProperty("resetDismissOnSessionGone");
    expect(result.current).not.toHaveProperty("toggleTrajectoryPanel");
  });

  it("resets to the new agent overview and drops stale tabs on agent switch", () => {
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string | null }) =>
        useChatDockPanel(false, agentId),
      { initialProps: { agentId: "agent-a" as string | null } },
    );
    act(() => {
      result.current.openFileAt("outbound/old-agent-a.txt");
      result.current.openArtifactsTab();
      result.current.openBrowserTab();
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.openTabs.some((t) => t.kind === "file")).toBe(true);

    rerender({ agentId: "agent-b" });

    expect(result.current.dockOpen).toBe(true);
    expect(result.current.openTabs).toEqual([
      { id: "overview", kind: "overview" },
    ]);
    expect(result.current.activeTabId).toBe("overview");
  });

  it("clears stale tabs and stays closed on agent switch in mobile", () => {
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string | null }) =>
        useChatDockPanel(true, agentId),
      { initialProps: { agentId: "agent-a" as string | null } },
    );
    act(() => {
      result.current.openFileAt("outbound/old-agent-a.txt");
      result.current.openArtifactsTab();
    });
    expect(result.current.dockOpen).toBe(true);
    expect(result.current.openTabs.length).toBeGreaterThan(0);

    rerender({ agentId: "agent-b" });

    expect(result.current.dockOpen).toBe(false);
    expect(result.current.openTabs).toEqual([]);
    expect(result.current.activeTabId).toBeNull();
  });

  it("does not clear dock on null ↔ id first paint races", () => {
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string | null }) =>
        useChatDockPanel(false, agentId),
      { initialProps: { agentId: null as string | null } },
    );
    rerender({ agentId: "agent-a" });
    act(() => {
      result.current.openFileList();
    });
    expect(result.current.dockOpen).toBe(true);
    // Same agent again after transient identity — keep open.
    rerender({ agentId: "agent-a" });
    expect(result.current.dockOpen).toBe(true);
  });
});
