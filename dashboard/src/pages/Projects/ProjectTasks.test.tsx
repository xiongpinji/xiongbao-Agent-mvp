import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../locales/en.json";
import zh from "../../locales/zh.json";

const {
  list,
  getOne,
  create,
  link,
  unlink,
  shares,
  share,
  revoke,
  grantText,
  revokeText,
  messages,
} = vi.hoisted(() => ({
  list: vi.fn(),
  getOne: vi.fn(),
  create: vi.fn(),
  link: vi.fn(),
  unlink: vi.fn(),
  shares: vi.fn(),
  share: vi.fn(),
  revoke: vi.fn(),
  grantText: vi.fn(),
  revokeText: vi.fn(),
  messages: vi.fn(),
}));

const { threadsList, threadsDelete } = vi.hoisted(() => ({
  threadsList: vi.fn(),
  threadsDelete: vi.fn(),
}));

const { getProjectExperts } = vi.hoisted(() => ({
  getProjectExperts: vi.fn(),
}));

const { agentState } = vi.hoisted(() => ({
  agentState: {
    value: {
      agents: [] as Array<{
        agent_id: string;
        name: string;
        state: string;
        kind?: "expert" | "team";
        is_shared?: boolean;
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
    projectTasksApi: {
      list,
      get: getOne,
      create,
      link,
      unlink,
      shares,
      share,
      revoke,
      grantText,
      revokeText,
      messages,
    },
  };
});

vi.mock("../../api/modules/octopThreads", () => ({
  octopThreadsApi: { list: threadsList, delete: threadsDelete },
}));

vi.mock("../../api/modules/projectExperts", () => ({
  projectExpertsApi: { list: getProjectExperts },
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
import type { ProjectMember } from "../../api/modules/projects";
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
  access: "owner",
  can_read_text: true,
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
  access: "owner",
  can_read_text: true,
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
  access: "owner",
  can_read_text: true,
};

const taskShared: ProjectTask = {
  project_id: "p1",
  thread_id: "ts1",
  owner_user_id: 3,
  agent_id: "agent-2",
  title: "被分享的周报",
  source: "manual",
  last_active: 1_700_000_400,
  created_at: 1_700_000_000,
  access: "reader",
  can_read_text: false,
};

const memberAlice: ProjectMember = {
  user_id: 2,
  username: "alice",
  role: "owner",
};
const memberBob: ProjectMember = {
  user_id: 3,
  username: "bob",
  role: "member",
};
const memberCarol: ProjectMember = {
  user_id: 4,
  username: "carol",
  role: "member",
};
const defaultMembers: ProjectMember[] = [memberAlice, memberBob, memberCarol];

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

const projectInstructions = "先阅读项目背景；回答必须标注引用来源。";
const projectInstructionsSha = "sha-current";

const onProjectReload = vi.fn();

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

interface RenderTasksOptions {
  instructions?: string;
  instructionsSha256?: string | null;
}

function tasksTree(
  projectId = "p1",
  members: ProjectMember[] | null = defaultMembers,
  options: RenderTasksOptions = {},
) {
  return (
    <MemoryRouter initialEntries={[`/projects/${projectId}`]}>
      <ProjectTasks
        projectId={projectId}
        members={members}
        instructions={options.instructions ?? projectInstructions}
        instructionsSha256={
          options.instructionsSha256 === undefined
            ? projectInstructionsSha
            : options.instructionsSha256
        }
        onProjectReload={onProjectReload}
      />
      <LocationProbe />
    </MemoryRouter>
  );
}

function renderTasks(
  projectId = "p1",
  members: ProjectMember[] | null = defaultMembers,
  options: RenderTasksOptions = {},
) {
  return render(tasksTree(projectId, members, options));
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
  create.mockResolvedValue({
    ...taskOpen,
    thread_id: "t-new",
    title: "项目新任务",
    source: "project",
  });
  getProjectExperts.mockResolvedValue({ revision: 0, items: [] });
  shares.mockResolvedValue({ items: [] });
  share.mockResolvedValue({
    user_id: 4,
    role: "reader",
    granted_at: 1_700_000_600,
    can_read_text: false,
  });
  revoke.mockResolvedValue(undefined);
  grantText.mockResolvedValue({
    user_id: 3,
    granted_at: 1_700_000_700,
  });
  revokeText.mockResolvedValue(undefined);
  messages.mockResolvedValue({
    status: "ready",
    items: [],
    has_more: false,
    next_before_seq: null,
  });
});

describe("ProjectTasks owner behavior", () => {
  it("renders the owner's own linked task with a real chat deep link", async () => {
    renderTasks("p1");

    expect(await screen.findByText("写周报")).toBeInTheDocument();
    expect(list).toHaveBeenCalledWith("p1", {
      scope: "own",
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
      screen.getByText(
        "项目任务默认私密；卡片与对话文本都需任务本人分别授权给指定成员，附件仍私密。",
      ),
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
        expect.objectContaining({ scope: "own", offset: 1, limit: 50 }),
      ),
    );
    expect(await screen.findByText("修缺陷")).toBeInTheDocument();
    expect(screen.getByText("写周报")).toBeInTheDocument();

    list.mockResolvedValueOnce(page([taskOpen], false));
    await user.type(screen.getByPlaceholderText("搜索任务标题"), "周报{enter}");
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ scope: "own", q: "周报", offset: 0 }),
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

  it("keeps the picker open when an unrelated 409 is returned", async () => {
    const user = userEvent.setup();
    link.mockRejectedValue(
      new Error(
        '409 - {"error":{"code":"SOME_OTHER_CONFLICT","message":"other write conflict"}}',
      ),
    );
    renderTasks("p1");
    await screen.findByText("写周报");
    const callsBefore = list.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "关联我的任务" }));
    await screen.findByText("周报草稿");
    await user.click(screen.getByRole("button", { name: "关联到项目" }));

    expect(await screen.findByText("other write conflict")).toBeInTheDocument();
    expect(
      screen.getByRole("dialog", { name: "关联我的任务" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        "这条任务已关联到其他项目或状态冲突。已刷新项目任务列表，请检查后重试。",
      ),
    ).toBeNull();
    expect(list.mock.calls.length).toBe(callsBefore);
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

  it("refreshes a stale unlinked card when DELETE says the link is gone", async () => {
    const user = userEvent.setup();
    list
      .mockResolvedValueOnce(page([taskOpen]))
      .mockResolvedValueOnce(page([]));
    unlink.mockRejectedValue(
      new Error(
        '404 - {"error":{"code":"NOT_FOUND","message":"project task not found"}}',
      ),
    );
    renderTasks("p1");
    await screen.findByText("写周报");

    await user.click(screen.getByRole("button", { name: "取消关联：写周报" }));
    await user.click(await screen.findByRole("button", { name: "确认移除" }));

    expect(await screen.findByText("还没有关联任务")).toBeInTheDocument();
    expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
    expect(threadsDelete).not.toHaveBeenCalled();
    expect(list).toHaveBeenCalledTimes(2);
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

  it("does not show the previous project's private rows if the new project load fails", async () => {
    list.mockResolvedValueOnce(page([taskOpen]));
    const { rerender } = renderTasks("p1");
    expect(await screen.findByText("写周报")).toBeInTheDocument();

    list.mockRejectedValueOnce(
      new Error("503 - service temporarily unavailable"),
    );
    rerender(
      <MemoryRouter initialEntries={["/projects/p2"]}>
        <ProjectTasks projectId="p2" members={defaultMembers} />
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
        <ProjectTasks projectId="p2" members={defaultMembers} />
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

  it("never repopulates rows from a late response after the project changes", async () => {
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
        <ProjectTasks projectId="p2" members={defaultMembers} />
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
});

describe("ProjectTasks create project task", () => {
  async function openCreateModal(options: RenderTasksOptions = {}) {
    const user = userEvent.setup();
    renderTasks("p1", defaultMembers, options);
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建项目任务" }));
    const dialog = await screen.findByRole("dialog", {
      name: "新建项目任务",
    });
    return { user, dialog };
  }

  function confirmBox(dialog: HTMLElement) {
    return within(dialog).getByRole("checkbox", { name: /我已阅读并确认/ });
  }

  function submitButton(dialog: HTMLElement) {
    return within(dialog).getByRole("button", {
      name: "创建任务并前往对话",
    });
  }

  it("previews the exact instructions, requires confirmation and navigates to the created chat", async () => {
    const { user, dialog } = await openCreateModal();

    expect(
      within(dialog).getByTestId("project-task-create-instructions"),
    ).toHaveTextContent(projectInstructions);
    expect(
      within(dialog).getByText(
        "项目所有者或管理员编写的指令可能调用你已启用的个人工具。任务沿用所选专家当前工作空间；没有独立的项目目录或云端执行。",
      ),
    ).toBeInTheDocument();

    expect(confirmBox(dialog)).not.toBeChecked();
    expect(submitButton(dialog)).toBeDisabled();
    expect(create).not.toHaveBeenCalled();

    await user.click(confirmBox(dialog));
    expect(submitButton(dialog)).toBeEnabled();
    await user.click(submitButton(dialog));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        "p1",
        "agent-1",
        projectInstructionsSha,
        0,
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/chat/agent-1/t-new",
      ),
    );
    expect(screen.queryByRole("dialog", { name: "新建项目任务" })).toBeNull();
    // A created task is a server-confirmed card, never a sent first turn and
    // never an optimistic row.
    expect(messages).not.toHaveBeenCalled();
    expect(screen.queryByTestId("project-task-t-new")).toBeNull();
    expect(screen.getByText("写周报")).toBeInTheDocument();
  });

  it("limits a configured project to its currently shared running single experts and sends the revision", async () => {
    getProjectExperts.mockResolvedValueOnce({
      revision: 4,
      items: [
        {
          agent_id: "agent-2",
          name: "小助手",
          description: null,
          status: "available",
        },
      ],
    });
    agentState.value.agents = [
      { agent_id: "agent-1", name: "阿熊", state: "running" },
      {
        agent_id: "agent-2",
        name: "小助手",
        state: "running",
        is_shared: true,
      },
    ];
    const { user, dialog } = await openCreateModal();
    expect(await within(dialog).findByText("小助手")).toBeInTheDocument();
    fireEvent.mouseDown(
      within(dialog).getByRole("combobox", { name: "选择专家" }),
    );
    expect(within(visibleDropdown()).queryByText("阿熊")).toBeNull();
    await user.click(confirmBox(dialog));
    await user.click(submitButton(dialog));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        "p1",
        "agent-2",
        projectInstructionsSha,
        4,
      ),
    );
  });

  it("does not offer a configured expert that has become unavailable", async () => {
    getProjectExperts.mockResolvedValueOnce({
      revision: 5,
      items: [
        {
          agent_id: "agent-2",
          name: null,
          description: null,
          status: "unavailable",
        },
      ],
    });
    const { dialog } = await openCreateModal();
    await waitFor(() =>
      expect(
        within(dialog).getByRole("combobox", { name: "选择专家" }),
      ).toBeDisabled(),
    );
    expect(submitButton(dialog)).toBeDisabled();
    expect(
      within(dialog).getByText(/项目配置的专家当前不可用/),
    ).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("refreshes project experts and clears consent after a stale selection 409", async () => {
    getProjectExperts
      .mockResolvedValueOnce({
        revision: 1,
        items: [
          {
            agent_id: "agent-2",
            name: "小助手",
            description: null,
            status: "available",
          },
        ],
      })
      .mockResolvedValueOnce({ revision: 2, items: [] });
    agentState.value.agents[1].is_shared = true;
    create.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"PROJECT_EXPERTS_CHANGED","message":"expert selection changed"}}',
      ),
    );
    const { user, dialog } = await openCreateModal();
    await waitFor(() => expect(submitButton(dialog)).toBeDisabled());
    await user.click(confirmBox(dialog));
    await user.click(submitButton(dialog));
    await waitFor(() => expect(getProjectExperts).toHaveBeenCalledTimes(2));
    expect(confirmBox(dialog)).not.toBeChecked();
    expect(onProjectReload).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("sends the selected accessible agent instead of the default", async () => {
    const { user, dialog } = await openCreateModal();
    await user.click(confirmBox(dialog));

    fireEvent.mouseDown(
      within(dialog).getByRole("combobox", { name: "选择专家" }),
    );
    fireEvent.click(within(visibleDropdown()).getByText("小助手"));

    await user.click(submitButton(dialog));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        "p1",
        "agent-2",
        projectInstructionsSha,
        0,
      ),
    );
  });

  it("explains the unavailable reason when no expert is running", async () => {
    agentState.value = {
      agents: [{ agent_id: "agent-3", name: "停用专家", state: "stopped" }],
      activeAgentId: null,
    };
    const { dialog } = await openCreateModal();

    expect(
      within(dialog).getByText(
        "没有已启用的专家，无法新建项目任务；请先在专家列表中启用或启动一个专家。",
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("combobox", { name: "选择专家" }),
    ).toBeDisabled();
    expect(submitButton(dialog)).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
  });

  it("does not offer a running team host whose delegated peers lack project context", async () => {
    agentState.value = {
      agents: [
        { agent_id: "team-1", name: "专家团", state: "running", kind: "team" },
      ],
      activeAgentId: "team-1",
    };
    const { user, dialog } = await openCreateModal();

    expect(
      within(dialog).getByRole("combobox", { name: "选择专家" }),
    ).toBeDisabled();
    await user.click(confirmBox(dialog));
    expect(submitButton(dialog)).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
  });

  it("keeps a single in-flight submit while the server creates the task", async () => {
    let resolveCreate: ((task: ProjectTask) => void) | null = null;
    create.mockImplementationOnce(
      () => new Promise((resolve) => (resolveCreate = resolve)),
    );
    const { user, dialog } = await openCreateModal();
    await user.click(confirmBox(dialog));
    await user.click(submitButton(dialog));
    expect(create).toHaveBeenCalledTimes(1);

    await user.click(submitButton(dialog));
    expect(create).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveCreate?.({
        ...taskOpen,
        thread_id: "t-new",
        title: "项目新任务",
        source: "project",
      });
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/chat/agent-1/t-new",
      ),
    );
  });

  it("keeps the creation dialog open while a private task request is in flight", async () => {
    let resolveCreate: ((task: ProjectTask) => void) | null = null;
    create.mockImplementationOnce(
      () => new Promise((resolve) => (resolveCreate = resolve)),
    );
    const { user, dialog } = await openCreateModal();
    await user.click(confirmBox(dialog));
    await user.click(submitButton(dialog));
    expect(create).toHaveBeenCalledTimes(1);
    expect(
      within(dialog).getByRole("button", { name: /取\s*消/ }),
    ).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(
      screen.getByRole("dialog", { name: "新建项目任务" }),
    ).toBeInTheDocument();
    await act(async () => {
      resolveCreate?.({ ...taskOpen, thread_id: "t-new", source: "project" });
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/chat/agent-1/t-new",
      ),
    );
  });

  it("clears consent and reloads the project after a stale digest 409", async () => {
    create.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"PROJECT_INSTRUCTIONS_CHANGED","message":"project instructions changed"}}',
      ),
    );
    const user = userEvent.setup();
    const view = renderTasks();
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建项目任务" }));
    const dialog = await screen.findByRole("dialog", {
      name: "新建项目任务",
    });
    await user.click(confirmBox(dialog));
    await user.click(submitButton(dialog));

    expect(
      await within(dialog).findByText("project instructions changed"),
    ).toBeInTheDocument();
    await waitFor(() => expect(onProjectReload).toHaveBeenCalledTimes(1));
    expect(confirmBox(dialog)).not.toBeChecked();
    expect(submitButton(dialog)).toBeDisabled();

    // Until a different server digest arrives, checking the old preview again
    // must not re-submit the stale snapshot while reload is still pending.
    await user.click(confirmBox(dialog));
    expect(submitButton(dialog)).toBeDisabled();
    expect(create).toHaveBeenCalledTimes(1);

    // Closing and reopening the modal cannot discard the rejected digest.
    await user.click(within(dialog).getByRole("button", { name: /取\s*消/ }));
    await user.click(screen.getByRole("button", { name: "新建项目任务" }));
    const reopened = await screen.findByRole("dialog", {
      name: "新建项目任务",
    });
    await user.click(confirmBox(reopened));
    expect(submitButton(reopened)).toBeDisabled();
    expect(create).toHaveBeenCalledTimes(1);

    // A detail reload may temporarily omit the digest. Returning the same
    // rejected digest must stay blocked, even after that loading gap.
    view.rerender(
      tasksTree("p1", defaultMembers, { instructionsSha256: null }),
    );
    expect(submitButton(reopened)).toBeDisabled();
    view.rerender(
      tasksTree("p1", defaultMembers, {
        instructionsSha256: projectInstructionsSha,
      }),
    );
    await user.click(confirmBox(reopened));
    expect(submitButton(reopened)).toBeDisabled();
    expect(create).toHaveBeenCalledTimes(1);

    // A refreshed digest never silently re-checks the confirmation box; the
    // member must confirm the new instructions before retrying.
    view.rerender(
      tasksTree("p1", defaultMembers, { instructionsSha256: "sha-new" }),
    );
    const refreshed = screen.getByRole("dialog", { name: "新建项目任务" });
    expect(confirmBox(refreshed)).not.toBeChecked();
    expect(submitButton(refreshed)).toBeDisabled();

    create.mockResolvedValueOnce({
      ...taskOpen,
      thread_id: "t-new",
      source: "project",
    });
    await user.click(confirmBox(refreshed));
    await user.click(submitButton(refreshed));
    await waitFor(() =>
      expect(create).toHaveBeenLastCalledWith("p1", "agent-1", "sha-new", 0),
    );
  });

  it("reloads the project and clears consent when create returns 404", async () => {
    create.mockRejectedValueOnce(
      new Error(
        '404 - {"error":{"code":"NOT_FOUND","message":"project not found"}}',
      ),
    );
    const { user, dialog } = await openCreateModal();
    await user.click(confirmBox(dialog));
    await user.click(submitButton(dialog));

    expect(
      await within(dialog).findByText("project not found"),
    ).toBeInTheDocument();
    await waitFor(() => expect(onProjectReload).toHaveBeenCalledTimes(1));
    expect(confirmBox(dialog)).not.toBeChecked();
    expect(screen.getByTestId("location")).toHaveTextContent("/projects/p1");
    expect(create).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("project-task-t-new")).toBeNull();
  });

  it("keeps a 403 archived/access failure visible and retryable", async () => {
    create.mockRejectedValueOnce(
      new Error(
        '403 - {"error":{"code":"FORBIDDEN","message":"project is archived"}}',
      ),
    );
    const { user, dialog } = await openCreateModal();
    await user.click(confirmBox(dialog));
    await user.click(submitButton(dialog));

    expect(
      await within(dialog).findByText("project is archived"),
    ).toBeInTheDocument();
    expect(onProjectReload).not.toHaveBeenCalled();
    expect(confirmBox(dialog)).toBeChecked();
    expect(submitButton(dialog)).toBeEnabled();
    expect(screen.getByTestId("location")).toHaveTextContent("/projects/p1");
  });

  it("keeps a network failure visible and retryable without a fake task", async () => {
    create
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce({
        ...taskOpen,
        thread_id: "t-new",
        source: "project",
      });
    const { user, dialog } = await openCreateModal();
    await user.click(confirmBox(dialog));
    await user.click(submitButton(dialog));

    expect(
      await within(dialog).findByText("Network error"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("project-task-t-new")).toBeNull();
    expect(screen.getByTestId("location")).toHaveTextContent("/projects/p1");

    await user.click(submitButton(dialog));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/chat/agent-1/t-new",
      ),
    );
  });

  it("closes the modal and drops a late create response after the project changes", async () => {
    let resolveCreate: ((task: ProjectTask) => void) | null = null;
    create.mockImplementationOnce(
      () => new Promise((resolve) => (resolveCreate = resolve)),
    );
    const user = userEvent.setup();
    const view = renderTasks();
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建项目任务" }));
    const dialog = await screen.findByRole("dialog", {
      name: "新建项目任务",
    });
    await user.click(confirmBox(dialog));
    await user.click(submitButton(dialog));
    expect(create).toHaveBeenCalledTimes(1);

    list.mockResolvedValueOnce(page([taskOtherProject]));
    view.rerender(tasksTree("p2"));

    expect(await screen.findByText("另一个任务")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "新建项目任务" })).toBeNull(),
    );
    await act(async () => {
      resolveCreate?.({ ...taskOpen, thread_id: "t-new", source: "project" });
      await Promise.resolve();
    });

    expect(screen.getByTestId("location").textContent).not.toContain("/chat");
    expect(screen.queryByTestId("project-task-t-new")).toBeNull();
  });
});

describe("ProjectTasks scopes", () => {
  it("switches scopes through the correct server query with distinct rows", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([taskOpen]));
    renderTasks("p1");
    await screen.findByText("写周报");
    expect(list).toHaveBeenLastCalledWith("p1", {
      scope: "own",
      q: "",
      limit: 50,
      offset: 0,
    });

    list.mockResolvedValueOnce(page([taskShared]));
    await user.click(screen.getByText("分享给我的"));
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ scope: "shared", offset: 0 }),
      ),
    );
    expect(await screen.findByText("被分享的周报")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());
    expect(
      screen.getByText(
        "只读视图：卡片由任务本人分享；对话文本需单独授权，附件与运行流仍私密。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "关联我的任务" })).toBeNull();

    list.mockResolvedValueOnce(page([taskOpen, taskShared]));
    await user.click(screen.getByText("全部任务"));
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ scope: "all", offset: 0 }),
      ),
    );
    expect(await screen.findByText("写周报")).toBeInTheDocument();
    expect(await screen.findByText("被分享的周报")).toBeInTheDocument();
    expect(
      screen.getByText(
        "“全部任务”只包含你关联的任务和被分享给你的卡片，不代表项目内所有成员的任务可见。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "关联我的任务" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("全部任务需要明确的任务共享权限，暂未开放。"),
    ).toBeNull();
    expect(
      screen.queryByText("协同任务需要成员协作与移交能力，暂未开放。"),
    ).toBeNull();
  });

  it("discards a pending own response after switching scope", async () => {
    const user = userEvent.setup();
    let resolveOwn: ((value: ReturnType<typeof page>) => void) | null = null;
    list.mockImplementationOnce(
      () => new Promise((resolve) => (resolveOwn = resolve)),
    );
    renderTasks("p1");

    list.mockResolvedValueOnce(page([taskShared]));
    await user.click(await screen.findByText("分享给我的"));
    expect(await screen.findByText("被分享的周报")).toBeInTheDocument();

    await act(async () => {
      resolveOwn?.(page([taskOpen]));
      await Promise.resolve();
    });

    expect(screen.queryByText("写周报")).toBeNull();
  });

  it("keeps local/cloud and transfer disabled and no longer claims sharing is off", async () => {
    renderTasks("p1");
    await screen.findByText("写周报");

    for (const label of ["本地", "云端", "移交"]) {
      expect(screen.getByRole("button", { name: label })).toBeDisabled();
    }
    expect(screen.queryByRole("button", { name: "共享" })).toBeNull();
    expect(
      screen.getByText(
        "卡片分享仅开放摘要；任务本人可再单独授权指定成员只读对话文本。附件仍私密。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("本地/云端与移交需要后续后端权限，当前不可用。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("共享任务前需让所有读取入口遵守任务权限，暂未开放。"),
    ).toBeNull();
  });
});

describe("ProjectTasks reader cards", () => {
  async function openSharedScope(
    members: ProjectMember[] | null = defaultMembers,
  ) {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([]));
    renderTasks("p1", members);
    await screen.findByText("还没有关联任务");
    list.mockResolvedValueOnce(page([taskShared]));
    await user.click(screen.getByText("分享给我的"));
    await screen.findByText("被分享的周报");
    return { user };
  }

  it("renders a read-only local summary and never a chat link", async () => {
    const { user } = await openSharedScope();
    const card = screen.getByTestId("project-task-ts1");

    expect(within(card).queryAllByRole("link")).toHaveLength(0);
    expect(card.querySelector('a[href*="/chat"]')).toBeNull();
    expect(card.querySelector("a")).toBeNull();
    expect(within(card).getByText("分享给我的 · 只读")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /取消关联/ })).toBeNull();
    expect(within(card).queryByRole("button", { name: /分享任务/ })).toBeNull();

    getOne.mockResolvedValue(taskShared);
    await user.click(
      within(card).getByRole("button", { name: "被分享的周报" }),
    );
    await waitFor(() => expect(getOne).toHaveBeenCalledWith("p1", "ts1"));

    const dialog = await screen.findByRole("dialog", {
      name: "任务摘要（只读）",
    });
    expect(within(dialog).getByText("对话内容尚未共享")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "这里只显示任务摘要；正文、附件、工作区文件与运行流仍只对任务所有者可见。",
      ),
    ).toBeInTheDocument();
    expect(dialog.querySelector('a[href*="/chat"]')).toBeNull();
    expect(within(dialog).queryAllByRole("link")).toHaveLength(0);
  });

  it("clears a stale reader card when the detail re-check returns 404", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([]));
    renderTasks("p1");
    await screen.findByText("还没有关联任务");

    list
      .mockResolvedValueOnce(page([taskShared]))
      .mockResolvedValueOnce(page([]));
    getOne.mockRejectedValue(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    await user.click(screen.getByText("分享给我的"));
    await screen.findByText("被分享的周报");
    await user.click(screen.getByRole("button", { name: "被分享的周报" }));

    await waitFor(() => expect(screen.queryByText("被分享的周报")).toBeNull());
    expect(await screen.findByText("还没有分享给我的任务")).toBeInTheDocument();
    expect(
      screen.getByText("任务分享或文本权限已变化，列表已刷新。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: "任务摘要（只读）" }),
    ).toBeNull();
    expect(list).toHaveBeenCalledTimes(3);
  });

  it("keeps the reader dialog open with retry when the detail fails otherwise", async () => {
    const { user } = await openSharedScope();
    getOne
      .mockRejectedValueOnce(new Error("503 - service temporarily unavailable"))
      .mockResolvedValueOnce(taskShared);

    await user.click(screen.getByRole("button", { name: "被分享的周报" }));
    const dialog = await screen.findByRole("dialog", {
      name: "任务摘要（只读）",
    });
    expect(
      await within(dialog).findByText("service temporarily unavailable"),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /重\s*试/ }));
    await waitFor(() => expect(getOne).toHaveBeenCalledTimes(2));
    expect(
      await within(dialog).findByText("对话内容尚未共享"),
    ).toBeInTheDocument();
  });

  it("never requests text when the fresh card detail is card-only", async () => {
    const { user } = await openSharedScope();
    getOne.mockResolvedValue({ ...taskShared, can_read_text: false });

    await user.click(screen.getByRole("button", { name: "被分享的周报" }));
    const dialog = await screen.findByRole("dialog", {
      name: "任务摘要（只读）",
    });

    expect(within(dialog).getByText("对话内容尚未共享")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "这里只显示任务摘要；正文、附件、工作区文件与运行流仍只对任务所有者可见。",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(getOne).toHaveBeenCalledWith("p1", "ts1"));
    expect(messages).not.toHaveBeenCalled();
    expect(within(dialog).queryByTestId("project-task-reader-text")).toBeNull();
  });

  it("shows an honest pending state instead of inventing history", async () => {
    const { user } = await openSharedScope();
    getOne.mockResolvedValue({ ...taskShared, can_read_text: true });
    messages.mockResolvedValueOnce({
      status: "pending",
      items: [],
      has_more: false,
      next_before_seq: null,
    });

    await user.click(screen.getByRole("button", { name: "被分享的周报" }));
    const dialog = await screen.findByRole("dialog", {
      name: "任务摘要（只读）",
    });

    await waitFor(() =>
      expect(messages).toHaveBeenCalledWith("p1", "ts1", { limit: 50 }),
    );
    expect(
      await within(dialog).findByText("对话文本暂不可读"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        /文本尚未同步，或该任务使用了本片暂不支持的版本化历史存储/,
      ),
    ).toBeInTheDocument();
    expect(within(dialog).queryByTestId("project-task-reader-text")).toBeNull();
    expect(
      within(dialog).queryByText(
        "仅显示已支持且已完成投影的纯文本；部分非文本或超大消息未显示，这里不是完整历史。",
      ),
    ).toBeNull();
  });

  it("recovers from a text load error with retry and shows an honest empty state", async () => {
    const { user } = await openSharedScope();
    getOne.mockResolvedValue({ ...taskShared, can_read_text: true });
    messages
      .mockRejectedValueOnce(new Error("503 - service temporarily unavailable"))
      .mockResolvedValueOnce({
        status: "ready",
        items: [],
        has_more: false,
        next_before_seq: null,
      });

    await user.click(screen.getByRole("button", { name: "被分享的周报" }));
    const dialog = await screen.findByRole("dialog", {
      name: "任务摘要（只读）",
    });
    expect(
      await within(dialog).findByText("service temporarily unavailable"),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /重\s*试/ }));
    await waitFor(() => expect(messages).toHaveBeenCalledTimes(2));
    expect(
      await within(dialog).findByText("没有可显示的文本消息。"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("该任务可能只有非文本内容，或文本尚未同步。"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "仅显示已支持且已完成投影的纯文本；部分非文本或超大消息未显示，这里不是完整历史。",
      ),
    ).toBeInTheDocument();
  });

  it("renders granted plain text escaped and pages older history by cursor", async () => {
    const { user } = await openSharedScope();
    getOne.mockResolvedValue({ ...taskShared, can_read_text: true });
    messages.mockResolvedValueOnce({
      status: "ready",
      items: [
        {
          seq: 12,
          role: "assistant",
          text: "<img src=x onerror=alert(1)> 回复",
          created_at: 1_700_000_900,
          truncated: false,
        },
        {
          seq: 11,
          role: "user",
          text: "第一行\n第二行",
          created_at: 1_700_000_800,
          truncated: true,
        },
      ],
      has_more: true,
      next_before_seq: 11,
    });

    await user.click(screen.getByRole("button", { name: "被分享的周报" }));
    const dialog = await screen.findByRole("dialog", {
      name: "任务摘要（只读）",
    });

    await waitFor(() =>
      expect(messages).toHaveBeenCalledWith("p1", "ts1", { limit: 50 }),
    );
    expect(
      await within(dialog).findByText("<img src=x onerror=alert(1)> 回复"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/第一行/)).toBeInTheDocument();
    expect(
      within(dialog).getByText("内容过长，已截断显示"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "仅显示已支持且已完成投影的纯文本；部分非文本或超大消息未显示，这里不是完整历史。",
      ),
    ).toBeInTheDocument();
    expect(dialog.querySelector("img")).toBeNull();
    expect(dialog.querySelector('a[href*="/chat"]')).toBeNull();
    expect(within(dialog).queryAllByRole("link")).toHaveLength(0);

    messages.mockResolvedValueOnce({
      status: "ready",
      items: [
        {
          seq: 10,
          role: "user",
          text: "更早的消息",
          created_at: 1_700_000_700,
          truncated: false,
        },
      ],
      has_more: false,
      next_before_seq: null,
    });
    await user.click(
      within(dialog).getByRole("button", { name: "加载更早的文本" }),
    );
    await waitFor(() =>
      expect(messages).toHaveBeenLastCalledWith("p1", "ts1", {
        limit: 50,
        beforeSeq: 11,
      }),
    );
    expect(await within(dialog).findByText("更早的消息")).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "加载更早的文本" }),
    ).toBeNull();

    const nodes = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        '[data-testid^="project-task-reader-text-"]',
      ),
    );
    expect(nodes.map((node) => node.dataset.testid)).toEqual([
      "project-task-reader-text-10",
      "project-task-reader-text-11",
      "project-task-reader-text-12",
    ]);
  });

  it("drops a late text page after the reader dialog closes", async () => {
    const { user } = await openSharedScope();
    getOne.mockResolvedValue({ ...taskShared, can_read_text: true });
    let resolveMessages: ((value: unknown) => void) | null = null;
    messages.mockImplementationOnce(
      () => new Promise((resolve) => (resolveMessages = resolve)),
    );

    await user.click(screen.getByRole("button", { name: "被分享的周报" }));
    const dialog = await screen.findByRole("dialog", {
      name: "任务摘要（只读）",
    });
    await waitFor(() => expect(messages).toHaveBeenCalledTimes(1));

    await user.click(within(dialog).getByRole("button", { name: /关\s*闭/ }));
    await act(async () => {
      resolveMessages?.({
        status: "ready",
        items: [
          {
            seq: 5,
            role: "user",
            text: "迟到私密文本",
            created_at: 1_700_000_600,
            truncated: false,
          },
        ],
        has_more: false,
        next_before_seq: null,
      });
      await Promise.resolve();
    });

    expect(screen.queryByText("迟到私密文本")).toBeNull();
    expect(screen.queryByTestId("project-task-reader-text")).toBeNull();
    expect(
      screen.queryByRole("dialog", { name: "任务摘要（只读）" }),
    ).toBeNull();
  });

  it("clears cached text and the stale card when the text request 404s", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([]));
    renderTasks("p1");
    await screen.findByText("还没有关联任务");

    list
      .mockResolvedValueOnce(page([taskShared]))
      .mockResolvedValueOnce(page([]));
    getOne.mockResolvedValue({ ...taskShared, can_read_text: true });
    messages.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );

    await user.click(screen.getByText("分享给我的"));
    await screen.findByText("被分享的周报");
    await user.click(screen.getByRole("button", { name: "被分享的周报" }));

    await waitFor(() => expect(screen.queryByText("被分享的周报")).toBeNull());
    expect(await screen.findByText("还没有分享给我的任务")).toBeInTheDocument();
    expect(
      screen.getByText("任务分享或文本权限已变化，列表已刷新。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: "任务摘要（只读）" }),
    ).toBeNull();
    expect(messages).toHaveBeenCalledTimes(1);
  });

  it("keeps the card when only text access is revoked", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([]));
    renderTasks("p1");
    await screen.findByText("还没有关联任务");

    list
      .mockResolvedValueOnce(page([taskShared]))
      .mockResolvedValueOnce(page([{ ...taskShared, can_read_text: false }]));
    getOne.mockResolvedValue({ ...taskShared, can_read_text: true });
    messages.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );

    await user.click(screen.getByText("分享给我的"));
    await screen.findByText("被分享的周报");
    await user.click(screen.getByRole("button", { name: "被分享的周报" }));

    await waitFor(() => expect(list).toHaveBeenCalledTimes(3));
    expect(
      screen.getByRole("button", { name: "被分享的周报" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("project-task-reader-text")).toBeNull();
    expect(
      screen.queryByRole("dialog", { name: "任务摘要（只读）" }),
    ).toBeNull();
  });

  it("does not refetch or keep text when a reopened detail is card-only", async () => {
    const { user } = await openSharedScope();
    getOne.mockResolvedValueOnce({ ...taskShared, can_read_text: true });
    messages.mockResolvedValueOnce({
      status: "ready",
      items: [
        {
          seq: 2,
          role: "assistant",
          text: "私密回复",
          created_at: 1_700_000_800,
          truncated: false,
        },
      ],
      has_more: false,
      next_before_seq: null,
    });

    await user.click(screen.getByRole("button", { name: "被分享的周报" }));
    let dialog = await screen.findByRole("dialog", {
      name: "任务摘要（只读）",
    });
    expect(await within(dialog).findByText("私密回复")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /关\s*闭/ }));
    getOne.mockResolvedValueOnce({ ...taskShared, can_read_text: false });
    await user.click(screen.getByRole("button", { name: "被分享的周报" }));
    dialog = await screen.findByRole("dialog", { name: "任务摘要（只读）" });

    await waitFor(() =>
      expect(within(dialog).getByText("对话内容尚未共享")).toBeInTheDocument(),
    );
    expect(within(dialog).queryByText("私密回复")).toBeNull();
    expect(messages).toHaveBeenCalledTimes(1);
  });
});

describe("ProjectTasks owner sharing", () => {
  async function openShareModal(
    members: ProjectMember[] | null = defaultMembers,
  ) {
    const user = userEvent.setup();
    renderTasks("p1", members);
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "分享任务：写周报" }));
    const dialog = await screen.findByRole("dialog", {
      name: "分享任务卡片",
    });
    return { user, dialog };
  }

  it("grants and revokes reader access to selected members with confirmation", async () => {
    shares.mockResolvedValueOnce({
      items: [
        {
          user_id: 3,
          role: "reader",
          granted_at: 1_700_000_500,
          can_read_text: false,
        },
      ],
    });
    const { user, dialog } = await openShareModal();

    await waitFor(() => expect(shares).toHaveBeenCalledWith("p1", "t1"));
    expect(
      within(dialog).getByText(
        "卡片分享仅开放摘要；对话文本需对每位接收者另行确认。附件仍私密。",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("alice")).toBeNull();
    expect(within(dialog).getByText("bob")).toBeInTheDocument();
    expect(within(dialog).getByText("已分享")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "撤回 bob 的分享" }),
    ).toBeInTheDocument();

    shares.mockResolvedValueOnce({
      items: [
        {
          user_id: 3,
          role: "reader",
          granted_at: 1_700_000_500,
          can_read_text: false,
        },
        {
          user_id: 4,
          role: "reader",
          granted_at: 1_700_000_600,
          can_read_text: false,
        },
      ],
    });
    await user.click(
      within(dialog).getByRole("button", { name: "分享给 carol" }),
    );
    expect(
      await screen.findByText(
        "确认分享给 carol？目前只共享任务标题与卡片信息，正文和附件仍私密。",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认分享" }));

    await waitFor(() => expect(share).toHaveBeenCalledWith("p1", "t1", 4));
    expect(message.success).toHaveBeenCalledWith(
      "已分享给 carol；正文和附件仍私密。",
    );
    // Sharing the card never grants conversation text on its own.
    expect(grantText).not.toHaveBeenCalled();
    await waitFor(() => expect(shares).toHaveBeenCalledTimes(2));

    await user.click(screen.getByRole("button", { name: "撤回 bob 的分享" }));
    expect(
      await screen.findByText("撤回后 bob 将不再看到这张任务卡片。"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认撤回" }));

    await waitFor(() => expect(revoke).toHaveBeenCalledWith("p1", "t1", 3));
    expect(message.success).toHaveBeenCalledWith("已撤回 bob 的分享。");
  });

  it("disables granting with an honest state when the member roster failed", async () => {
    const { dialog } = await openShareModal(null);

    expect(
      within(dialog).getByText(
        "成员名单加载失败，暂时无法选择分享对象；请刷新页面后重试。",
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "分享给 bob" }),
    ).toBeNull();
  });

  it("offers a retry when the active grant list fails to load", async () => {
    shares
      .mockRejectedValueOnce(new Error("503 - service temporarily unavailable"))
      .mockResolvedValueOnce({ items: [] });
    const { user, dialog } = await openShareModal();

    expect(
      await within(dialog).findByText("service temporarily unavailable"),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /重\s*试/ }));
    await waitFor(() => expect(shares).toHaveBeenCalledTimes(2));
    expect(await within(dialog).findByText("bob")).toBeInTheDocument();
  });

  it("drops a stale shares response after the share dialog is closed", async () => {
    const user = userEvent.setup();
    let resolveShares: ((value: { items: unknown[] }) => void) | null = null;
    shares.mockImplementationOnce(
      () => new Promise((resolve) => (resolveShares = resolve)),
    );
    renderTasks("p1");
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "分享任务：写周报" }));
    const dialog = await screen.findByRole("dialog", {
      name: "分享任务卡片",
    });

    await user.click(within(dialog).getByRole("button", { name: /关\s*闭/ }));
    await act(async () => {
      resolveShares?.({
        items: [
          {
            user_id: 3,
            role: "reader",
            granted_at: 1_700_000_500,
            can_read_text: false,
          },
        ],
      });
      await Promise.resolve();
    });

    expect(screen.queryByRole("dialog", { name: "分享任务卡片" })).toBeNull();
    expect(screen.queryByText("撤回 bob 的分享")).toBeNull();
  });

  it("requires a separate confirmation before granting text to a shared member", async () => {
    shares.mockResolvedValueOnce({
      items: [
        {
          user_id: 3,
          role: "reader",
          granted_at: 1_700_000_500,
          can_read_text: false,
        },
      ],
    });
    const { user, dialog } = await openShareModal();
    await waitFor(() => expect(shares).toHaveBeenCalledWith("p1", "t1"));

    expect(within(dialog).queryByText("可读文本")).toBeNull();
    await user.click(
      await within(dialog).findByRole("button", { name: "授予 bob 文本权限" }),
    );
    expect(
      await screen.findByText(/允许 bob 查看本任务的历史与后续同步文本/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/文本可能包含用户粘贴的路径、链接或敏感内容/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^取\s*消$/ }));
    expect(grantText).not.toHaveBeenCalled();
    expect(within(dialog).queryByText("可读文本")).toBeNull();
  });

  it("grants text only after the separate confirmation is accepted", async () => {
    shares
      .mockResolvedValueOnce({
        items: [
          {
            user_id: 3,
            role: "reader",
            granted_at: 1_700_000_500,
            can_read_text: false,
          },
        ],
      })
      .mockResolvedValueOnce({
        items: [
          {
            user_id: 3,
            role: "reader",
            granted_at: 1_700_000_500,
            can_read_text: true,
          },
        ],
      });
    const { user, dialog } = await openShareModal();
    await waitFor(() => expect(shares).toHaveBeenCalledWith("p1", "t1"));

    await user.click(
      await within(dialog).findByRole("button", { name: "授予 bob 文本权限" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "确认授予文本" }),
    );

    await waitFor(() => expect(grantText).toHaveBeenCalledWith("p1", "t1", 3));
    await waitFor(() => expect(shares).toHaveBeenCalledTimes(2));
    expect(await within(dialog).findByText("可读文本")).toBeInTheDocument();
    expect(message.success).toHaveBeenCalledWith(
      "已授予 bob 对话文本；附件、工具与思考过程仍私密。",
    );
  });

  it("revokes text separately while the card share stays active", async () => {
    shares.mockResolvedValueOnce({
      items: [
        {
          user_id: 3,
          role: "reader",
          granted_at: 1_700_000_500,
          can_read_text: true,
        },
      ],
    });
    const { user, dialog } = await openShareModal();
    expect(await within(dialog).findByText("可读文本")).toBeInTheDocument();

    shares.mockResolvedValueOnce({
      items: [
        {
          user_id: 3,
          role: "reader",
          granted_at: 1_700_000_500,
          can_read_text: false,
        },
      ],
    });
    await user.click(
      within(dialog).getByRole("button", { name: "撤回 bob 的文本权限" }),
    );
    expect(
      await screen.findByText(
        "撤回后 bob 将不再看到对话文本，但任务卡片分享仍保留。",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认撤回文本" }));

    await waitFor(() => expect(revokeText).toHaveBeenCalledWith("p1", "t1", 3));
    expect(revoke).not.toHaveBeenCalled();
    await waitFor(() => expect(shares).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(within(dialog).queryByText("可读文本")).toBeNull(),
    );
    expect(within(dialog).getByText("已分享")).toBeInTheDocument();
    expect(message.success).toHaveBeenCalledWith(
      "已撤回 bob 的文本权限；卡片分享仍保留。",
    );
  });

  it("clears text controls as soon as the card share is revoked", async () => {
    shares.mockResolvedValueOnce({
      items: [
        {
          user_id: 3,
          role: "reader",
          granted_at: 1_700_000_500,
          can_read_text: true,
        },
      ],
    });
    const { user, dialog } = await openShareModal();
    expect(await within(dialog).findByText("可读文本")).toBeInTheDocument();

    shares.mockResolvedValueOnce({ items: [] });
    await user.click(
      within(dialog).getByRole("button", { name: "撤回 bob 的分享" }),
    );
    await user.click(await screen.findByRole("button", { name: "确认撤回" }));

    await waitFor(() => expect(revoke).toHaveBeenCalledWith("p1", "t1", 3));
    await waitFor(() => expect(shares).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(within(dialog).queryByText("可读文本")).toBeNull(),
    );
    expect(
      within(dialog).queryByRole("button", { name: "撤回 bob 的文本权限" }),
    ).toBeNull();
    expect(grantText).not.toHaveBeenCalled();
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
  it("has matching zh/en task and composer keys without stale unavailable copy", () => {
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
    for (const stale of [
      "shareReason",
      "scopeAllReason",
      "scopeCollabReason",
      "privacyHint",
    ]) {
      expect(zhTasks).not.toContain(stale);
    }
    expect(zh.apiErrors.PROJECT_TASK_LINK_CONFLICT).toBeTruthy();
    expect(en.apiErrors.PROJECT_TASK_LINK_CONFLICT).toBeTruthy();
    expect(Object.keys(zh.projects.taskComposer).sort()).toEqual(
      Object.keys(en.projects.taskComposer).sort(),
    );
  });
});
