import { fireEvent, render, screen } from "@testing-library/react";
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
