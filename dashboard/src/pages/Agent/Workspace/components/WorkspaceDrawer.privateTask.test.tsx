import { act, render, screen, waitFor } from "@testing-library/react";
import { App } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopAgent } from "../../../../context/AgentContext";
import { clearAuthToken, setAuthToken } from "../../../../api/request";
import WorkspaceDrawer from "./WorkspaceDrawer";

const context = vi.hoisted(() => ({
  agents: [] as OctopAgent[],
  getChatAgentById: vi.fn<(id: string) => OctopAgent | null>(),
}));

vi.mock("../../../../context/AgentContext", () => ({
  useAgent: () => context,
}));

vi.mock("../../../../api/config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

vi.mock("../../../../i18n", () => ({
  default: { language: "zh" },
}));

vi.mock("../../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "Asia/Shanghai",
}));

vi.mock("./FileViewer", () => ({ default: () => null }));

function agent(overrides: Partial<OctopAgent> = {}): OctopAgent {
  return {
    id: 1,
    agent_id: "private-runtime",
    name: "Private task",
    state: "running",
    internal: true,
    description: null,
    persona_mbti: null,
    default_model: null,
    system_prompt: null,
    template_name: null,
    last_error: null,
    icon: null,
    icon_name: null,
    icon_url: null,
    color: null,
    ...overrides,
  };
}

function renderDrawer(privateTask?: boolean, agentId = "private-runtime") {
  const props = { agentId, open: true, embedded: true, privateTask };
  return render(
    <MemoryRouter>
      <App>
        <WorkspaceDrawer {...props} onClose={vi.fn()} />
      </App>
    </MemoryRouter>,
  );
}

describe("WorkspaceDrawer private-task readiness", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    context.agents = [];
    context.getChatAgentById.mockReset();
    context.getChatAgentById.mockImplementation((id) =>
      id === "private-runtime" ? agent() : null,
    );
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify([{ path: "note.txt", is_dir: false }]), {
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    localStorage.clear();
    setAuthToken("drawer-test-token");
  });

  afterEach(() => {
    clearAuthToken();
    vi.unstubAllGlobals();
  });

  it("loads the marked ready internal runtime through the authenticated root request", async () => {
    renderDrawer(true);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agents/private-runtime/workspace/tree?path=/&from_workspace=true",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer drawer-test-token",
        }),
      }),
    );
    expect(context.getChatAgentById).toHaveBeenCalledWith("private-runtime");
    expect((await screen.findAllByText("note.txt")).length).toBeGreaterThan(0);
  });

  it.each([undefined, false])(
    "never resolves an internal card without an explicit true marker (%s)",
    async (marker) => {
      renderDrawer(marker);

      await act(async () => {});
      expect(context.getChatAgentById).not.toHaveBeenCalled();
      expect(fetchMock).not.toHaveBeenCalled();
      expect(screen.getByText("chat.pickAgent")).toBeTruthy();
    },
  );

  it.each([
    ["missing", null],
    ["mismatched", agent({ agent_id: "other-runtime" })],
    ["non-internal", agent({ internal: false })],
    ["unspecified-internal", agent({ internal: undefined })],
    ...["stopped", "created", "starting", "stopping", "failed"].map(
      (state) => [state, agent({ state })] as const,
    ),
  ] as const)("does not request a %s raw card", async (_name, rawCard) => {
    context.getChatAgentById.mockReturnValue(rawCard);
    renderDrawer(true);

    await act(async () => {});
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not request a private tree without an agent id", async () => {
    renderDrawer(true, "");

    await act(async () => {});
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([undefined, true])(
    "preserves the running public-agent projection (%s marker)",
    async (marker) => {
      context.agents = [agent({ agent_id: "public-agent", internal: false })];
      renderDrawer(marker, "public-agent");

      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
      expect(fetchMock.mock.calls[0][0]).toBe(
        "/api/agents/public-agent/workspace/tree?path=/&from_workspace=true",
      );
      expect((await screen.findAllByText("note.txt")).length).toBeGreaterThan(
        0,
      );
      if (!marker) expect(context.getChatAgentById).not.toHaveBeenCalled();
    },
  );
});
