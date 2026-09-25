import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { list, set } = vi.hoisted(() => ({ list: vi.fn(), set: vi.fn() }));

vi.mock("../../api/modules/projectExperts", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectExperts")
  >();
  return { ...actual, projectExpertsApi: { list, set } };
});

const { agentState } = vi.hoisted(() => ({
  agentState: {
    value: {
      agents: [] as Array<Record<string, unknown>>,
      activeAgentId: null as string | null,
    },
  },
}));

vi.mock("../../context/AgentContext", () => ({
  useAgent: () => agentState.value,
}));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../utils/antdMessage", () => ({
  message: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

import ProjectExperts from "./ProjectExperts";
import type { ProjectExpertsResponse } from "../../api/modules/projectExperts";

const a1 = {
  agent_id: "a1",
  name: "研究员",
  description: "共享研究员",
  status: "available" as const,
};

const baseResponse: ProjectExpertsResponse = {
  revision: 2,
  items: [a1],
};

function candidate(
  agentId: string,
  name: string,
  extra: Partial<{
    description: string | null;
    state: string;
    kind: string;
    is_shared: boolean;
  }> = {},
) {
  return {
    agent_id: agentId,
    name,
    description: null,
    state: "running",
    kind: "expert",
    is_shared: true,
    ...extra,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderPanel(role: "owner" | "admin" | "member" = "owner") {
  return render(<ProjectExperts projectId="p1" role={role} />);
}

function selectedList() {
  return screen.getByRole("list", { name: "已添加专家" });
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom's pseudo-element getComputedStyle is not implemented; antd's Modal
  // scroll locker calls it. Same shim as ProjectMembersPanel.test.tsx.
  const nativeGetComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    nativeGetComputedStyle(element),
  );
  list.mockResolvedValue(baseResponse);
  agentState.value = {
    agents: [
      candidate("a1", "研究员"),
      candidate("a2", "写作助手", { description: "共享写作" }),
    ],
    activeAgentId: null,
  };
});

afterEach(() => vi.restoreAllMocks());

describe("ProjectExperts", () => {
  it("shows the ordered list and count to a member without management actions", async () => {
    renderPanel("member");

    expect(await screen.findByText("研究员")).toBeInTheDocument();
    expect(screen.getByText("专家（1/20）")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "管理专家" })).toBeNull();
    expect(list).toHaveBeenCalledWith("p1");
    expect(set).not.toHaveBeenCalled();
  });

  it("redacts an unavailable expert to a generic card without stale private details", async () => {
    list.mockResolvedValue({
      revision: 4,
      items: [
        a1,
        {
          agent_id: "gone",
          name: null,
          description: null,
          status: "unavailable",
        },
      ],
    });
    agentState.value = {
      agents: [candidate("gone", "泄露的私有专家", { is_shared: false })],
      activeAgentId: null,
    };

    renderPanel("member");

    expect(await screen.findByText("研究员")).toBeInTheDocument();
    expect(screen.getByText("专家不可用")).toBeInTheDocument();
    expect(screen.getByText("专家（2/20）")).toBeInTheDocument();
    expect(screen.queryByText("泄露的私有专家")).toBeNull();
    expect(screen.queryByText(/泄露/)).toBeNull();
  });

  it("only offers shared, enabled, single experts as candidates to an owner", async () => {
    const user = userEvent.setup();
    agentState.value = {
      agents: [
        candidate("a1", "研究员"),
        candidate("a2", "写作助手"),
        candidate("c1", "私有助手", { is_shared: false }),
        candidate("t1", "专家团", { kind: "team" }),
        candidate("e1", "停用专家", { state: "stopped" }),
      ],
      activeAgentId: null,
    };
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByText("写作助手")).toBeInTheDocument();
    expect(within(dialog).queryByText("私有助手")).toBeNull();
    expect(within(dialog).queryByText("专家团")).toBeNull();
    expect(within(dialog).queryByText("停用专家")).toBeNull();
    expect(within(dialog).getByText("已添加（1/20）")).toBeInTheDocument();
  });

  it("filters the shared candidate list by search", async () => {
    const user = userEvent.setup();
    agentState.value = {
      agents: [
        candidate("a1", "研究员"),
        candidate("a2", "写作助手"),
        candidate("a3", "翻译助手"),
      ],
      activeAgentId: null,
    };
    list.mockResolvedValue({ revision: 2, items: [] });
    renderPanel("owner");

    await screen.findByText("专家（0/20）");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");

    const search = within(dialog).getByPlaceholderText("搜索专家");
    await user.type(search, "写作");
    expect(within(dialog).getByText("写作助手")).toBeInTheDocument();
    expect(within(dialog).queryByText("翻译助手")).toBeNull();

    await user.clear(search);
    await user.type(search, "不存在");
    expect(within(dialog).getByText("没有匹配的专家。")).toBeInTheDocument();
  });

  it("adds a candidate and confirms the exact ordered list exactly once", async () => {
    const user = userEvent.setup();
    set.mockResolvedValue({
      revision: 3,
      items: [
        a1,
        {
          agent_id: "a2",
          name: "写作助手",
          description: "共享写作",
          status: "available",
        },
      ],
    });
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(
      within(dialog).getByRole("button", { name: "添加 写作助手" }),
    );
    expect(within(dialog).getByText("已添加（2/20）")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));

    expect(set).toHaveBeenCalledTimes(1);
    expect(set).toHaveBeenCalledWith("p1", 2, ["a1", "a2"]);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      within(screen.getByRole("list", { name: "项目专家名单" })).getByText(
        "写作助手",
      ),
    ).toBeInTheDocument();
  });

  it("reorders and removes draft entries before saving", async () => {
    const user = userEvent.setup();
    set.mockResolvedValue({
      revision: 3,
      items: [
        {
          agent_id: "a2",
          name: "写作助手",
          description: null,
          status: "available",
        },
      ],
    });
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(
      within(dialog).getByRole("button", { name: "添加 写作助手" }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "上移 写作助手" }),
    );

    const entries = within(selectedList()).getAllByRole("listitem");
    expect(within(entries[0]).getByText("写作助手")).toBeInTheDocument();
    expect(within(entries[1]).getByText("研究员")).toBeInTheDocument();

    await user.click(
      within(dialog).getByRole("button", { name: "移除 研究员" }),
    );
    expect(within(dialog).getByText("已添加（1/20）")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));

    expect(set).toHaveBeenCalledTimes(1);
    expect(set).toHaveBeenCalledWith("p1", 2, ["a2"]);
  });

  it("discards the draft on cancel and never sends a PUT", async () => {
    const user = userEvent.setup();
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    let dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "添加 写作助手" }),
    );
    expect(within(dialog).getByText("已添加（2/20）")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /取\s*消/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(set).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "管理专家" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("已添加（1/20）")).toBeInTheDocument();
    expect(within(selectedList()).queryByText("写作助手")).toBeNull();
  });

  it("preserves the draft and refreshes the revision after a stale conflict", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(baseResponse).mockResolvedValueOnce({
      revision: 5,
      items: [
        a1,
        {
          agent_id: "a3",
          name: "新专家",
          description: null,
          status: "available",
        },
      ],
    });
    set
      .mockRejectedValueOnce(
        new Error(
          'Request failed: 409 Conflict - {"error":{"code":"PROJECT_EXPERTS_CHANGED","message":"changed"}}',
        ),
      )
      .mockResolvedValueOnce({
        revision: 6,
        items: [
          a1,
          {
            agent_id: "a2",
            name: "写作助手",
            description: null,
            status: "available",
          },
        ],
      });
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "添加 写作助手" }),
    );
    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));

    expect(set).toHaveBeenNthCalledWith(1, "p1", 2, ["a1", "a2"]);
    expect(
      await within(dialog).findByText(
        "配置已被其他管理员修改，已刷新服务端名单；你的修改仍保留，请确认后重试。",
      ),
    ).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
    expect(within(dialog).getByText("已添加（2/20）")).toBeInTheDocument();
    expect(within(selectedList()).queryByText("新专家")).toBeNull();

    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));
    expect(set).toHaveBeenNthCalledWith(2, "p1", 5, ["a1", "a2"]);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("hides a deleted draft expert's stale local name after a conflict reload", async () => {
    const user = userEvent.setup();
    list
      .mockResolvedValueOnce(baseResponse)
      .mockResolvedValueOnce({ revision: 3, items: [] });
    set.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"PROJECT_EXPERTS_CHANGED","message":"changed"}}',
      ),
    );
    renderPanel("owner");
    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));
    await within(dialog).findByText(/配置已被其他管理员修改/);

    expect(within(selectedList()).queryByText("研究员")).toBeNull();
    expect(within(selectedList()).getByText("专家不可用")).toBeInTheDocument();
  });

  it("keeps a still-valid locally added expert's label after a conflict refresh", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(baseResponse).mockResolvedValueOnce({
      revision: 5,
      items: [a1],
    });
    set.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"PROJECT_EXPERTS_CHANGED","message":"changed"}}',
      ),
    );
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "添加 写作助手" }),
    );
    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));
    await within(dialog).findByText(/配置已被其他管理员修改/);

    // The server list no longer names a2, but it was locally added and is
    // still shared/running/single, so its candidate label stays visible.
    expect(within(selectedList()).getByText("写作助手")).toBeInTheDocument();
    expect(within(dialog).queryByText("专家不可用")).toBeNull();
    expect(within(dialog).getByText("已添加（2/20）")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));
    expect(set).toHaveBeenNthCalledWith(2, "p1", 5, ["a1", "a2"]);
  });

  it("redacts locally added experts that are no longer shared, running or single", async () => {
    const user = userEvent.setup();
    const refresh = deferred<ProjectExpertsResponse>();
    agentState.value = {
      agents: [
        candidate("a1", "研究员"),
        candidate("a2", "写作助手"),
        candidate("a3", "停用专家"),
        candidate("a4", "专家团"),
        candidate("a5", "仍可用"),
      ],
      activeAgentId: null,
    };
    list
      .mockResolvedValueOnce(baseResponse)
      .mockReturnValueOnce(refresh.promise);
    set.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"PROJECT_EXPERTS_CHANGED","message":"changed"}}',
      ),
    );
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");
    for (const name of ["写作助手", "停用专家", "专家团", "仍可用"]) {
      await user.click(
        within(dialog).getByRole("button", { name: `添加 ${name}` }),
      );
    }
    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));
    await within(dialog).findByText(/配置已被其他管理员修改/);

    // Only the still shared/running/single Agent keeps its local label.
    agentState.value = {
      agents: [
        candidate("a1", "研究员"),
        candidate("a2", "写作助手", { is_shared: false }),
        candidate("a3", "停用专家", { state: "stopped" }),
        candidate("a4", "专家团", { kind: "team" }),
        candidate("a5", "仍可用"),
      ],
      activeAgentId: null,
    };
    refresh.resolve({ revision: 5, items: [a1] });

    await waitFor(() =>
      expect(within(selectedList()).queryByText("写作助手")).toBeNull(),
    );
    expect(within(selectedList()).queryByText("停用专家")).toBeNull();
    expect(within(selectedList()).queryByText("专家团")).toBeNull();
    expect(within(selectedList()).getByText("仍可用")).toBeInTheDocument();
    expect(within(selectedList()).getAllByText("专家不可用")).toHaveLength(3);
    expect(within(dialog).getByText(/请先移除/)).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: /确\s*定/ }),
    ).toBeDisabled();
    expect(set).toHaveBeenCalledTimes(1);
  });

  it("keeps the draft open and blocks confirm when the conflict refresh fails", async () => {
    const user = userEvent.setup();
    list
      .mockResolvedValueOnce(baseResponse)
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({
        revision: 5,
        items: [
          a1,
          {
            agent_id: "a3",
            name: "新专家",
            description: null,
            status: "available",
          },
        ],
      });
    set
      .mockRejectedValueOnce(
        new Error(
          '409 - {"error":{"code":"PROJECT_EXPERTS_CHANGED","message":"changed"}}',
        ),
      )
      .mockResolvedValueOnce({ revision: 6, items: [a1] });
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "添加 写作助手" }),
    );
    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));

    // The failed refresh must not be reported as a successful one.
    expect(
      await within(dialog).findByText(/刷新服务端名单失败/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/已刷新服务端名单/)).toBeNull();
    expect(within(dialog).getByText("已添加（2/20）")).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: /确\s*定/ });
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(set).toHaveBeenCalledTimes(1);

    // Explicit reload succeeds: the draft is intact and confirm is unlocked.
    await user.click(within(dialog).getByRole("button", { name: "重新加载" }));
    await waitFor(() =>
      expect(within(dialog).queryByText(/刷新服务端名单失败/)).toBeNull(),
    );
    expect(within(dialog).getByText("已添加（2/20）")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));
    expect(set).toHaveBeenNthCalledWith(2, "p1", 5, ["a1", "a2"]);
  });

  it("ignores a late explicit reload after the dialog closes and reopens", async () => {
    const user = userEvent.setup();
    const pendingReload = deferred<ProjectExpertsResponse>();
    list
      .mockResolvedValueOnce(baseResponse)
      .mockRejectedValueOnce(new Error("network down"))
      .mockReturnValueOnce(pendingReload.promise);
    set.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"PROJECT_EXPERTS_CHANGED","message":"changed"}}',
      ),
    );
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    let dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));
    await within(dialog).findByText(/刷新服务端名单失败/);
    await user.click(within(dialog).getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(3));

    await user.click(within(dialog).getByRole("button", { name: /取\s*消/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    dialog = await screen.findByRole("dialog");

    await act(async () => {
      pendingReload.resolve({
        revision: 5,
        items: [
          {
            agent_id: "a3",
            name: "其他管理员的专家",
            description: null,
            status: "available",
          },
        ],
      });
      await pendingReload.promise;
    });
    expect(within(selectedList()).getByText("研究员")).toBeInTheDocument();
    expect(within(dialog).queryByText("专家不可用")).toBeNull();
  });

  it("blocks confirm while a server-listed unavailable expert stays selected", async () => {
    const user = userEvent.setup();
    list.mockResolvedValue({
      revision: 4,
      items: [
        a1,
        {
          agent_id: "gone",
          name: null,
          description: null,
          status: "unavailable",
        },
      ],
    });
    set.mockResolvedValue({ revision: 5, items: [a1] });
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");

    // It stays visible as a generic placeholder and removable — never dropped.
    expect(within(selectedList()).getByText("专家不可用")).toBeInTheDocument();
    expect(within(dialog).getByText(/请先移除/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: /确\s*定/ });
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(set).not.toHaveBeenCalled();

    await user.click(
      within(dialog).getByRole("button", { name: "移除 专家不可用" }),
    );
    await waitFor(() =>
      expect(within(dialog).queryByText(/请先移除/)).toBeNull(),
    );
    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));
    expect(set).toHaveBeenCalledWith("p1", 4, ["a1"]);
  });

  it("disables cancel and never submits twice while saving", async () => {
    const user = userEvent.setup();
    const pending = deferred<ProjectExpertsResponse>();
    set.mockReturnValue(pending.promise);
    renderPanel("owner");

    await screen.findByText("研究员");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /确\s*定/ }));

    expect(set).toHaveBeenCalledTimes(1);
    const confirm = within(dialog).getByRole("button", { name: /确\s*定/ });
    const cancel = within(dialog).getByRole("button", { name: /取\s*消/ });
    expect(confirm).toBeDisabled();
    expect(cancel).toBeDisabled();

    await user.click(confirm);
    await user.click(cancel);
    expect(set).toHaveBeenCalledTimes(1);

    pending.resolve({ revision: 3, items: [a1] });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("caps the selection at 20 experts", async () => {
    const user = userEvent.setup();
    list.mockResolvedValue({
      revision: 1,
      items: Array.from({ length: 20 }, (_, index) => ({
        agent_id: `a${index}`,
        name: `专家${index}`,
        description: null,
        status: "available" as const,
      })),
    });
    agentState.value = {
      agents: [candidate("b1", "新增专家")],
      activeAgentId: null,
    };
    renderPanel("owner");

    await screen.findByText("专家（20/20）");
    await user.click(screen.getByRole("button", { name: "管理专家" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByText("已添加（20/20）")).toBeInTheDocument();
    expect(
      within(dialog).getByText("最多可添加 20 位专家。"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "添加 新增专家" }),
    ).toBeDisabled();
  });

  it("never renders project A experts after switching to project B", async () => {
    let resolveA!: (value: ProjectExpertsResponse) => void;
    list
      .mockImplementationOnce(
        () =>
          new Promise<ProjectExpertsResponse>((resolve) => {
            resolveA = resolve;
          }),
      )
      .mockResolvedValueOnce({
        revision: 1,
        items: [
          {
            agent_id: "b1",
            name: "B专家",
            description: null,
            status: "available",
          },
        ],
      });

    const view = render(<ProjectExperts projectId="A" role="owner" />);
    view.rerender(<ProjectExperts projectId="B" role="owner" />);

    resolveA({
      revision: 9,
      items: [
        {
          agent_id: "a1",
          name: "A专家",
          description: null,
          status: "available",
        },
      ],
    });

    expect(await screen.findByText("B专家")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("A专家")).toBeNull());
  });
});
