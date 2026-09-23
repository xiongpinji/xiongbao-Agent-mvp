import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../locales/en.json";
import zh from "../../locales/zh.json";

const { list, getOne, link, unlink } = vi.hoisted(() => ({
  list: vi.fn(),
  getOne: vi.fn(),
  link: vi.fn(),
  unlink: vi.fn(),
}));

const { threadsList, threadsDelete } = vi.hoisted(() => ({
  threadsList: vi.fn(),
  threadsDelete: vi.fn(),
}));

const { agentState } = vi.hoisted(() => ({
  agentState: {
    value: {
      agents: [] as Array<{
        agent_id: string;
        name: string;
        state: string;
      }>,
      activeAgentId: "agent-1" as string | null,
    },
  },
}));

vi.mock("../../api/modules/projectTasks", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectTasks")
  >();
  return {
    ...actual,
    projectTasksApi: { list, get: getOne, link, unlink },
  };
});

vi.mock("../../api/modules/octopThreads", () => ({
  octopThreadsApi: { list: threadsList, delete: threadsDelete },
}));

vi.mock("../../context/AgentContext", () => ({
  useAgent: () => agentState.value,
  selectEnabledExperts: (
    agents: Array<{ state: string }>,
    _activeAgentId: string | null,
  ) => agents.filter((agent) => agent.state === "running"),
}));

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("../../utils/antdMessage", () => ({
  message: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock("../../components/EmptyState", () => ({
  EmptyState: ({
    title,
    description,
    actionLabel,
    onAction,
  }: {
    title?: string;
    description?: string;
    actionLabel?: string;
    onAction?: () => void;
  }) => (
    <section>
      <h2>{title}</h2>
      <p>{description}</p>
      {actionLabel && onAction && (
        <button onClick={onAction}>{actionLabel}</button>
      )}
    </section>
  ),
}));

import ProjectTasks, { isDashboardDmCandidate } from "./ProjectTasks";
import type { OctopThread } from "../../api/modules/octopThreads";
import type { ProjectTask } from "../../api/modules/projectTasks";
import { message } from "../../utils/antdMessage";

const taskOpen: ProjectTask = {
  project_id: "p1",
  thread_id: "t1",
  owner_user_id: 2,
  agent_id: "agent-1",
  title: "写周报",
  source: "manual",
  last_active: 1_700_000_100,
  created_at: 1_700_000_000,
};

const taskSecond: ProjectTask = {
  project_id: "p1",
  thread_id: "t2",
  owner_user_id: 2,
  agent_id: "agent-1",
  title: "修缺陷",
  source: "manual",
  last_active: 1_700_000_200,
  created_at: 1_700_000_000,
};

const taskOtherProject: ProjectTask = {
  project_id: "p2",
  thread_id: "t9",
  owner_user_id: 2,
  agent_id: "agent-2",
  title: "另一个任务",
  source: "manual",
  last_active: 1_700_000_300,
  created_at: 1_700_000_000,
};

const threadDm: OctopThread = {
  thread_id: "t9",
  title: "周报草稿",
  channel_type: "dashboard",
  session_key: "agent-1:dashboard:2:dm",
  last_active: 1_700_000_300,
  created_at: 1_700_000_000,
};

const threadGroup: OctopThread = {
  thread_id: "t10",
  title: "群会话",
  channel_type: "dashboard",
  session_key: "agent-1:dashboard:2:group",
  last_active: 1_700_000_300,
  created_at: 1_700_000_000,
};

const threadIm: OctopThread = {
  thread_id: "t11",
  title: "IM 会话",
  channel_type: "wecom",
  session_key: "agent-1:wecom:2:dm",
  last_active: 1_700_000_300,
  created_at: 1_700_000_000,
};

function page(items: ProjectTask[], hasMore = false) {
  return { items, limit: 50, offset: 0, has_more: hasMore };
}

function renderTasks(projectId = "p1") {
  return render(
    <MemoryRouter initialEntries={[`/projects/${projectId}`]}>
      <ProjectTasks projectId={projectId} />
    </MemoryRouter>,
  );
}

function visibleDropdown(): HTMLElement {
  const dropdowns = Array.from(
    document.querySelectorAll<HTMLElement>(".ant-select-dropdown"),
  ).filter((node) => !node.classList.contains("ant-select-dropdown-hidden"));
  const dropdown = dropdowns[dropdowns.length - 1];
  expect(dropdown).toBeTruthy();
  return dropdown;
}

beforeEach(() => {
  vi.clearAllMocks();
  agentState.value = {
    agents: [
      { agent_id: "agent-1", name: "阿熊", state: "running" },
      { agent_id: "agent-2", name: "小助手", state: "running" },
      { agent_id: "agent-3", name: "停用专家", state: "stopped" },
    ],
    activeAgentId: "agent-1",
  };
  list.mockResolvedValue(page([taskOpen]));
  threadsList.mockResolvedValue([threadDm, threadGroup, threadIm]);
  link.mockResolvedValue({ ...taskOpen, thread_id: "t9", title: "周报草稿" });
  unlink.mockResolvedValue(undefined);
  getOne.mockResolvedValue(taskOpen);
});

describe("ProjectTasks first-slice behavior", () => {
  it("renders the owner's own linked task with a real chat deep link", async () => {
    renderTasks("p1");

    expect(await screen.findByText("写周报")).toBeInTheDocument();
    expect(list).toHaveBeenCalledWith("p1", {
      q: "",
      limit: 50,
      offset: 0,
    });
    expect(screen.getByRole("link", { name: "写周报" })).toHaveAttribute(
      "href",
      "/chat/agent-1/t1",
    );
    expect(screen.getByText("阿熊")).toBeInTheDocument();
    expect(screen.getByText(/手动关联/)).toBeInTheDocument();
    expect(
      screen.getByText("项目任务默认私密：你只会看到自己关联的任务。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/session_key/)).toBeNull();
  });

  it("shows an honest empty personal list for a member without links", async () => {
    list.mockResolvedValue(page([]));
    renderTasks("p1");

    expect(await screen.findByText("还没有关联任务")).toBeInTheDocument();
    expect(screen.queryByTestId("project-task-t1")).toBeNull();
  });

  it("searches and pages on the server instead of filtering loaded rows", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([taskOpen], true));
    renderTasks("p1");
    await screen.findByText("写周报");

    list.mockResolvedValueOnce(page([taskSecond], false));
    await user.click(await screen.findByRole("button", { name: "加载更多" }));
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ offset: 1, limit: 50 }),
      ),
    );
    expect(await screen.findByText("修缺陷")).toBeInTheDocument();
    expect(screen.getByText("写周报")).toBeInTheDocument();

    list.mockResolvedValueOnce(page([taskOpen], false));
    await user.type(
      screen.getByPlaceholderText("搜索我的任务标题"),
      "周报{enter}",
    );
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ q: "周报", offset: 0 }),
      ),
    );
  });

  it("recovers from a list error with retry", async () => {
    const user = userEvent.setup();
    list.mockRejectedValueOnce(
      new Error("503 - service temporarily unavailable"),
    );
    renderTasks("p1");

    expect(
      await screen.findByText("service temporarily unavailable"),
    ).toBeInTheDocument();

    list.mockResolvedValueOnce(page([taskOpen]));
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("写周报")).toBeInTheDocument();
  });

  it("attaches only a bounded recent Dashboard DM candidate and requeries", async () => {
    const user = userEvent.setup();
    renderTasks("p1");
    await screen.findByText("写周报");

    await user.click(screen.getByRole("button", { name: "关联我的任务" }));

    expect(
      await screen.findByText(
        "仅显示每个专家最近 50 条 Dashboard 私聊，不是完整历史；可刷新或切换专家，后端会再次校验任务归属。",
      ),
    ).toBeInTheDocument();
    expect(await screen.findByText("周报草稿")).toBeInTheDocument();
    expect(threadsList).toHaveBeenCalledWith("agent-1", 50);
    expect(screen.queryByText("群会话")).toBeNull();
    expect(screen.queryByText("IM 会话")).toBeNull();
    expect(
      screen.getByText(
        "关联只登记归属，不会向项目成员共享对话正文、附件或运行流。",
      ),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关联到项目" }));
    await waitFor(() => expect(link).toHaveBeenCalledWith("p1", "t9"));
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1));
    expect(message.success).toHaveBeenCalledWith(
      "已关联到项目；对话内容不会共享给其他成员。",
    );
  });

  it("refreshes candidates and can switch to another enabled expert", async () => {
    const user = userEvent.setup();
    renderTasks("p1");
    await screen.findByText("写周报");

    await user.click(screen.getByRole("button", { name: "关联我的任务" }));
    await screen.findByText("周报草稿");

    await user.click(screen.getByRole("button", { name: "刷新最近任务" }));
    await waitFor(() => expect(threadsList).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "选择专家" })).toBeEnabled(),
    );

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "选择专家" }));
    fireEvent.click(within(visibleDropdown()).getByText("小助手"));
    await waitFor(() =>
      expect(threadsList).toHaveBeenLastCalledWith("agent-2", 50),
    );
  });

  it("shows an actionable conflict and reloads after a 409", async () => {
    const user = userEvent.setup();
    link.mockRejectedValue(
      new Error(
        '409 - {"error":{"code":"PROJECT_TASK_LINK_CONFLICT","message":"task already linked to another project"}}',
      ),
    );
    renderTasks("p1");
    await screen.findByText("写周报");
    const callsBefore = list.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "关联我的任务" }));
    await screen.findByText("周报草稿");
    await user.click(screen.getByRole("button", { name: "关联到项目" }));

    expect(
      await screen.findByText(
        "这条任务已关联到其他项目或状态冲突。已刷新项目任务列表，请检查后重试。",
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(list.mock.calls.length).toBeGreaterThan(callsBefore),
    );
  });

  it("detaches a task from the project without deleting the conversation", async () => {
    const user = userEvent.setup();
    renderTasks("p1");
    await screen.findByText("写周报");

    await user.click(screen.getByRole("button", { name: "取消关联：写周报" }));
    await user.click(await screen.findByRole("button", { name: "确认移除" }));

    await waitFor(() => expect(unlink).toHaveBeenCalledWith("p1", "t1"));
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1));
    expect(threadsDelete).not.toHaveBeenCalled();
    expect(message.success).toHaveBeenCalledWith(
      "已从项目移除，原对话与历史仍保留。",
    );
  });

  it("clears stale task rows when the project route turns into a 404", async () => {
    const user = userEvent.setup();
    renderTasks("p1");
    await screen.findByText("写周报");

    list.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    await user.click(screen.getByRole("button", { name: "刷新任务" }));

    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());
    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
  });

  it("never flashes previous-project rows or candidates after projectId changes", async () => {
    const user = userEvent.setup();
    let resolveP1: ((value: ReturnType<typeof page>) => void) | null = null;
    list.mockImplementationOnce(
      () => new Promise((resolve) => (resolveP1 = resolve)),
    );
    const { rerender } = renderTasks("p1");
    expect(screen.queryByText("写周报")).toBeNull();

    await user.click(screen.getByRole("button", { name: "关联我的任务" }));
    expect(await screen.findByText("周报草稿")).toBeInTheDocument();

    list.mockResolvedValueOnce(page([taskOtherProject]));
    rerender(
      <MemoryRouter initialEntries={["/projects/p2"]}>
        <ProjectTasks projectId="p2" />
      </MemoryRouter>,
    );
    expect(screen.queryByText("写周报")).toBeNull();
    expect(screen.queryByText("周报草稿")).toBeNull();

    await act(async () => {
      resolveP1?.(page([taskOpen]));
      await Promise.resolve();
    });

    expect(await screen.findByText("另一个任务")).toBeInTheDocument();
    expect(screen.queryByText("写周报")).toBeNull();
  });

  it("does not show the previous project's private rows if the new project load fails", async () => {
    list.mockResolvedValueOnce(page([taskOpen]));
    const { rerender } = renderTasks("p1");
    expect(await screen.findByText("写周报")).toBeInTheDocument();

    list.mockRejectedValueOnce(
      new Error("503 - service temporarily unavailable"),
    );
    rerender(
      <MemoryRouter initialEntries={["/projects/p2"]}>
        <ProjectTasks projectId="p2" />
      </MemoryRouter>,
    );

    expect(screen.queryByText("写周报")).toBeNull();
    expect(
      await screen.findByText("service temporarily unavailable"),
    ).toBeInTheDocument();
    expect(screen.queryByText("写周报")).toBeNull();
    expect(screen.queryByTestId("project-task-t1")).toBeNull();
  });

  it("discards a late candidate response after the project changes", async () => {
    const user = userEvent.setup();
    let resolveCandidates: ((rows: OctopThread[]) => void) | null = null;
    list.mockResolvedValueOnce(page([taskOpen]));
    threadsList.mockImplementationOnce(
      () => new Promise((resolve) => (resolveCandidates = resolve)),
    );
    const { rerender } = renderTasks("p1");
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "关联我的任务" }));
    await waitFor(() => expect(threadsList).toHaveBeenCalledTimes(1));

    list.mockResolvedValueOnce(page([taskOtherProject]));
    rerender(
      <MemoryRouter initialEntries={["/projects/p2"]}>
        <ProjectTasks projectId="p2" />
      </MemoryRouter>,
    );
    await screen.findByText("另一个任务");
    await act(async () => {
      resolveCandidates?.([threadDm]);
      await Promise.resolve();
    });

    threadsList.mockImplementationOnce(() => new Promise(() => {}));
    await user.click(screen.getByRole("button", { name: "关联我的任务" }));
    expect(screen.queryByText("周报草稿")).toBeNull();
  });

  it("keeps 全部/协同, share, local/cloud and transfer disabled with reasons", async () => {
    renderTasks("p1");
    await screen.findByText("写周报");

    const personal = screen.getByRole("radio", { name: "个人任务" });
    expect(personal).toBeChecked();
    expect(screen.getByRole("radio", { name: "全部任务" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "协同任务" })).toBeDisabled();

    for (const label of ["共享", "本地", "云端", "移交"]) {
      expect(screen.getByRole("button", { name: label })).toBeDisabled();
    }
    expect(
      screen.getByText("共享、本地/云端与移交需要后续后端权限，当前不可用。"),
    ).toBeInTheDocument();
  });
});

describe("project task candidate filtering", () => {
  it("accepts only the caller's Dashboard DM session keys", () => {
    expect(isDashboardDmCandidate(threadDm)).toBe(true);
    expect(isDashboardDmCandidate(threadGroup)).toBe(false);
    expect(isDashboardDmCandidate(threadIm)).toBe(false);
    expect(isDashboardDmCandidate({ ...threadDm, session_key: "broken" })).toBe(
      false,
    );
  });
});

describe("project task locale parity", () => {
  it("has the same projects.tasks keys in zh and en plus the new apiErrors code", () => {
    const zhTasks = Object.keys(zh.projects.tasks).sort();
    const enTasks = Object.keys(en.projects.tasks).sort();
    expect(zhTasks).toEqual(enTasks);
    expect(zhTasks.length).toBeGreaterThan(0);
    for (const key of zhTasks) {
      expect(
        (zh.projects.tasks as Record<string, string>)[key].trim().length,
      ).toBeGreaterThan(0);
      expect(
        (en.projects.tasks as Record<string, string>)[key].trim().length,
      ).toBeGreaterThan(0);
    }
    expect(zh.apiErrors.PROJECT_TASK_LINK_CONFLICT).toBeTruthy();
    expect(en.apiErrors.PROJECT_TASK_LINK_CONFLICT).toBeTruthy();
  });
});
