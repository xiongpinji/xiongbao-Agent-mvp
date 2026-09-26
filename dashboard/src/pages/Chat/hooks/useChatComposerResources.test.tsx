/**
 * useChatComposerResources.test.tsx — per-expert knowledge base selection.
 *
 * Regression for the "remember KB selection" bug: one global selection array
 * + a touched flag reset on expert switch meant Expert B's selection leaked
 * into (or replaced) Expert A's remembered selection when switching back.
 * Selection is now persisted per expert (chatStorage) and restored on switch.
 */

import { renderHook, waitFor, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api/modules/connectors", () => ({
  connectorsApi: {
    listInstances: vi.fn().mockResolvedValue([
      {
        status: "active",
        has_credentials: true,
        mcp_server_name: "c1",
        display_name: "C1",
        owner_user_id: 7,
        kind: "mcp",
      },
      {
        status: "active",
        has_credentials: true,
        mcp_server_name: "c2",
        display_name: "C2",
        owner_user_id: 7,
        kind: "mcp",
      },
    ]),
  },
}));
vi.mock("../../../api/modules/provider", () => ({
  providerApi: { listResolvedModels: vi.fn().mockResolvedValue([]) },
}));
vi.mock("../../../api/modules/preferences", () => ({
  preferencesApi: {
    get: vi.fn().mockResolvedValue(null),
    set: vi.fn().mockResolvedValue(undefined),
  },
}));
vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: { patch: vi.fn().mockResolvedValue({}) },
}));
vi.mock("../../../api/request", () => ({
  request: vi.fn().mockResolvedValue(null),
}));
vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: {
    getCapability: vi.fn().mockResolvedValue({ usable: true }),
    list: vi.fn().mockResolvedValue([
      { id: "k1", default_open: false, owner_user_id: 7 },
      { id: "k2", default_open: false, owner_user_id: 7 },
      { id: "k3", default_open: false, owner_user_id: 7 },
      { id: "k4", default_open: false, owner_user_id: 7 },
    ]),
  },
}));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 7 }),
}));
vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    agents: [
      { agent_id: "expertA", knowledge_base_ids: ["k1"] },
      { agent_id: "expertB", knowledge_base_ids: ["k3"] },
    ],
  }),
}));

import { useChatComposerResources } from "./useChatComposerResources";
import { connectorsApi } from "../../../api/modules/connectors";
import { knowledgeBasesApi } from "../../../api/modules/knowledgeBases";
import { octopThreadsApi } from "../../../api/modules/octopThreads";

beforeEach(() => {
  localStorage.clear();
});

describe("useChatComposerResources — per-expert KB selection", () => {
  it("never fetches personal connectors or knowledge for a private file task", () => {
    vi.mocked(connectorsApi.listInstances).mockClear();
    vi.mocked(knowledgeBasesApi.getCapability).mockClear();
    vi.mocked(knowledgeBasesApi.list).mockClear();

    const { result } = renderHook(() =>
      useChatComposerResources(
        "runtime-private",
        "thread-existing",
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        true,
      ),
    );

    expect(result.current.selectedConnectors).toEqual([]);
    expect(result.current.selectedKnowledgeBaseIds).toEqual([]);
    expect(connectorsApi.listInstances).not.toHaveBeenCalled();
    expect(knowledgeBasesApi.getCapability).not.toHaveBeenCalled();
    expect(knowledgeBasesApi.list).not.toHaveBeenCalled();
  });

  it("keeps private task settings local without patching the ordinary thread API", () => {
    const { result } = renderHook(() =>
      useChatComposerResources(
        "runtime-private",
        "thread-existing",
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        true,
      ),
    );
    vi.mocked(octopThreadsApi.patch).mockClear();

    act(() => {
      result.current.setSelectedModel("provider/model");
      result.current.handleReasoningChange("enabled", "high");
      result.current.handleConversationModeChange("plan");
      result.current.handleHitlPolicyChange({ mode: "ask" });
    });

    expect(octopThreadsApi.patch).not.toHaveBeenCalled();
  });

  it("still patches ordinary thread settings", () => {
    const { result } = renderHook(() =>
      useChatComposerResources("expertA", "thread-existing"),
    );
    vi.mocked(octopThreadsApi.patch).mockClear();

    act(() => {
      result.current.setSelectedModel("provider/model");
      result.current.handleReasoningChange("enabled", "high");
      result.current.handleConversationModeChange("plan");
      result.current.handleHitlPolicyChange({ mode: "ask" });
    });

    expect(octopThreadsApi.patch).toHaveBeenCalledTimes(4);
  });

  it("projects empty selections on the first private-task render", async () => {
    const threadId = "thread-existing";
    interface RenderSnapshot {
      agentId: string | null;
      privateTask: boolean;
      selectedConnectors: string[];
      selectedKnowledgeBaseIds: string[];
      chatConnectors: unknown[];
      chatKnowledgeBases: unknown;
    }
    const renderLog: RenderSnapshot[] = [];
    const { result, rerender } = renderHook(
      ({
        agentId,
        privateTask,
      }: {
        agentId: string | null;
        privateTask: boolean;
      }) => {
        const value = useChatComposerResources(
          agentId,
          threadId,
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
          privateTask,
        );
        // Record every render-phase projection: the regression window is the
        // first render after the switch, before any clearing effect runs.
        renderLog.push({
          agentId,
          privateTask,
          selectedConnectors: value.selectedConnectors,
          selectedKnowledgeBaseIds: value.selectedKnowledgeBaseIds,
          chatConnectors: value.chatConnectors,
          chatKnowledgeBases: value.chatKnowledgeBases,
        });
        return value;
      },
      { initialProps: { agentId: "expertA", privateTask: false } },
    );

    // Ordinary expert A loads personal connectors and knowledge bases.
    await waitFor(() => expect(result.current.chatConnectors.length).toBe(2));
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1"]),
    );
    expect(result.current.chatKnowledgeBases).toHaveLength(4);

    // The user picks a connector and an extra knowledge base in expert A.
    act(() => result.current.handleConnectorsChange(["c1"]));
    act(() => result.current.handleKnowledgeBaseIdsChange(["k1", "k2"]));
    expect(result.current.selectedConnectors).toEqual(["c1"]);
    expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1", "k2"]);

    // Switch to a private project file task. Mirrors Chat/index.tsx: the
    // route flips to private before verification, so the resolved agent id
    // goes null first and only becomes the runtime id once verified.
    renderLog.length = 0;
    rerender({ agentId: null, privateTask: true });

    const firstPrivateRender = renderLog.find((entry) => entry.privateTask);
    expect(firstPrivateRender).toBeDefined();
    // Synchronous projection: an immediate send consumes exactly this first
    // render's values, so it must never inherit expert A's selections.
    expect(firstPrivateRender?.selectedConnectors).toEqual([]);
    expect(firstPrivateRender?.selectedKnowledgeBaseIds).toEqual([]);
    expect(firstPrivateRender?.chatConnectors).toEqual([]);
    expect(firstPrivateRender?.chatKnowledgeBases).toBeUndefined();

    // After verification the runtime id becomes the resolved agent; this
    // send-capable first render must stay empty too.
    renderLog.length = 0;
    rerender({ agentId: "runtime-private", privateTask: true });
    expect(renderLog[0].privateTask).toBe(true);
    expect(renderLog[0].selectedConnectors).toEqual([]);
    expect(renderLog[0].selectedKnowledgeBaseIds).toEqual([]);
    expect(renderLog[0].chatConnectors).toEqual([]);
    expect(renderLog[0].chatKnowledgeBases).toBeUndefined();

    // Settled state stays empty as well.
    expect(result.current.selectedConnectors).toEqual([]);
    expect(result.current.selectedKnowledgeBaseIds).toEqual([]);
    expect(result.current.chatConnectors).toEqual([]);
    expect(result.current.chatKnowledgeBases).toBeUndefined();
  });

  it("restores expert A's manual selection after switching A -> B -> A", async () => {
    const threadId = "thread-existing"; // existing session → saved prefs apply
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string }) =>
        useChatComposerResources(agentId, threadId),
      { initialProps: { agentId: "expertA" } },
    );

    // A starts with its expert default (k1)
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1"]),
    );

    // user manually adds k2 in expert A
    act(() => result.current.handleKnowledgeBaseIdsChange(["k1", "k2"]));
    expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1", "k2"]);

    // switch to expert B: B's own default, nothing leaked from A
    rerender({ agentId: "expertB" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k3"]),
    );

    // user selects k3+k4 in expert B
    act(() => result.current.handleKnowledgeBaseIdsChange(["k3", "k4"]));

    // switch back to expert A: remembered k1+k2 (was: k3/k4 leak or reset)
    rerender({ agentId: "expertA" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1", "k2"]),
    );

    // and back to B: remembered k3+k4
    rerender({ agentId: "expertB" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k3", "k4"]),
    );
  });

  it("does not leak a cleared selection across experts", async () => {
    const threadId = "thread-existing";
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string }) =>
        useChatComposerResources(agentId, threadId),
      { initialProps: { agentId: "expertA" } },
    );
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1"]),
    );

    // user clears A's selection entirely (explicit empty is a choice)
    act(() => result.current.handleKnowledgeBaseIdsChange([]));

    rerender({ agentId: "expertB" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k3"]),
    );
    act(() => result.current.handleKnowledgeBaseIdsChange(["k4"]));

    rerender({ agentId: "expertA" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual([]),
    );
  });

  it("connectors get the same per-expert isolation (aligned with KB fix)", async () => {
    const threadId = "thread-existing";
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string }) =>
        useChatComposerResources(agentId, threadId),
      { initialProps: { agentId: "expertA" } },
    );
    await waitFor(() => expect(result.current.chatConnectors.length).toBe(2));

    // manual connector selection in A (saved per agent)
    act(() => result.current.handleConnectorsChange(["c1"]));
    expect(result.current.selectedConnectors).toEqual(["c1"]);

    // switch to B: A's c1 must NOT leak; B has no saved prefs / defaults
    rerender({ agentId: "expertB" });
    await waitFor(() => expect(result.current.selectedConnectors).toEqual([]));

    // manual selection in B, then back to A: each expert remembers its own
    act(() => result.current.handleConnectorsChange(["c2"]));
    rerender({ agentId: "expertA" });
    await waitFor(() =>
      expect(result.current.selectedConnectors).toEqual(["c1"]),
    );
    rerender({ agentId: "expertB" });
    await waitFor(() =>
      expect(result.current.selectedConnectors).toEqual(["c2"]),
    );
  });
});
