import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const { listAgents } = vi.hoisted(() => ({ listAgents: vi.fn() }));
vi.mock("../api/modules/agent", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/modules/agent")>();
  return { ...actual, agentApi: { ...actual.agentApi, list: listAgents } };
});

import { AgentProvider, useAgent, type OctopAgent } from "./AgentContext";

function expert(isShared: boolean): OctopAgent {
  return {
    id: 1,
    agent_id: "researcher",
    name: "研究员",
    description: null,
    persona_mbti: null,
    default_model: null,
    system_prompt: null,
    template_name: null,
    state: "running",
    last_error: null,
    icon: null,
    icon_name: null,
    icon_url: null,
    color: null,
    config: {},
    is_shared: isShared,
  };
}

function SharingStatus() {
  const { agents, refresh } = useAgent();
  return (
    <>
      <span>{agents[0]?.is_shared ? "共享中" : "未共享"}</span>
      <button onClick={() => void refresh()}>刷新专家</button>
    </>
  );
}

function internalAgent(overrides: Partial<OctopAgent> = {}): OctopAgent {
  return {
    ...expert(false),
    id: 9,
    agent_id: "internal-1",
    name: "私有文件任务",
    internal: true,
    config: undefined,
    ...overrides,
  };
}

function AgentProbe() {
  const { agents, activeAgentId, setActiveAgent, getChatAgentById } =
    useAgent();
  return (
    <>
      <span data-testid="ordinary-agents">
        {agents.map((agent) => agent.agent_id).join(",")}
      </span>
      <span data-testid="active-agent">{activeAgentId ?? "none"}</span>
      <span data-testid="raw-lookup">
        {getChatAgentById("internal-1")?.name ?? "none"}
      </span>
      <button onClick={() => setActiveAgent("internal-1")}>设为内部</button>
    </>
  );
}

beforeEach(() => {
  listAgents.mockReset();
  localStorage.clear();
});

it("updates a live expert when only its sharing state changes", async () => {
  listAgents
    .mockResolvedValueOnce([expert(true)])
    .mockResolvedValueOnce([expert(false)]);

  render(
    <AgentProvider>
      <SharingStatus />
    </AgentProvider>,
  );
  await screen.findByText("共享中");

  fireEvent.click(screen.getByRole("button", { name: "刷新专家" }));
  await screen.findByText("未共享");
  expect(listAgents).toHaveBeenCalledTimes(2);
});

it("keeps an internal runtime out of ordinary agents, defaults and localStorage", async () => {
  listAgents.mockResolvedValueOnce([internalAgent(), expert(false)]);

  render(
    <AgentProvider>
      <AgentProbe />
    </AgentProvider>,
  );

  await waitFor(() =>
    expect(screen.getByTestId("ordinary-agents")).toHaveTextContent(
      "researcher",
    ),
  );
  expect(screen.getByTestId("ordinary-agents")).not.toHaveTextContent(
    "internal-1",
  );
  expect(screen.getByTestId("active-agent")).toHaveTextContent("researcher");
  expect(localStorage.getItem("octop:active-agent")).toBe("researcher");
  // The raw lookup is still available for an explicit owner thread.
  expect(screen.getByTestId("raw-lookup")).toHaveTextContent("私有文件任务");
});

it("ignores a stored internal active id instead of restoring it", async () => {
  localStorage.setItem("octop:active-agent", "internal-1");
  listAgents.mockResolvedValueOnce([internalAgent(), expert(false)]);

  render(
    <AgentProvider>
      <AgentProbe />
    </AgentProvider>,
  );

  await waitFor(() =>
    expect(screen.getByTestId("active-agent")).toHaveTextContent("researcher"),
  );
  expect(localStorage.getItem("octop:active-agent")).toBe("researcher");
});

it("refuses to persist an internal runtime through setActiveAgent", async () => {
  listAgents.mockResolvedValueOnce([expert(false), internalAgent()]);

  render(
    <AgentProvider>
      <AgentProbe />
    </AgentProvider>,
  );
  await waitFor(() =>
    expect(screen.getByTestId("active-agent")).toHaveTextContent("researcher"),
  );

  fireEvent.click(screen.getByRole("button", { name: "设为内部" }));
  await waitFor(() =>
    expect(screen.getByTestId("active-agent")).toHaveTextContent("researcher"),
  );
  expect(localStorage.getItem("octop:active-agent")).toBe("researcher");
});
