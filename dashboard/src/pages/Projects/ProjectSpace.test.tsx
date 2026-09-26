import type { ReactNode } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal } from "antd";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { list, get, members, create, update } = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  members: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}));
const { acceptInvite } = vi.hoisted(() => ({ acceptInvite: vi.fn() }));

vi.mock("../../api/modules/projects", () => ({
  PROJECTS_PAGE_SIZE: 20,
  projectsApi: { list, get, members, create, update },
}));
vi.mock("../../api/modules/projectMembership", () => ({
  projectMembershipApi: { acceptInvite },
}));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("../../components/EmptyState", () => ({
  EmptyState: ({
    title,
    description,
    actionLabel,
    onAction,
  }: {
    title: string;
    description?: ReactNode;
    actionLabel?: string;
    onAction?: () => void;
  }) => (
    <section>
      <h2>{title}</h2>
      <p>{description}</p>
      {actionLabel && <button onClick={onAction}>{actionLabel}</button>}
    </section>
  ),
}));
vi.mock("../../utils/antdMessage", () => ({
  message: { success: vi.fn() },
}));

import ProjectsPage from "./index";
import ProjectDetail from "./ProjectDetail";
import CreateProjectModal from "./CreateProjectModal";
import type { ProjectRecord } from "../../api/modules/projects";

const summary = {
  project_id: "project-1",
  name: "熊宝项目",
  description: "真实项目描述",
  my_role: "owner",
  member_count: 1,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

/** Localized template copy the modal must apply when a template is chosen. */
const TEMPLATE_COPY: Record<
  string,
  { description: string; instructions: string }
> = {
  requirements: {
    description: "收集与评审产品需求，跟踪状态与版本计划。",
    instructions:
      "整理需求时写明背景、目标用户、功能点、验收标准与优先级，输出需求文档草稿。",
  },
  competitor: {
    description: "持续跟踪竞品动态，沉淀功能对比与差异化结论。",
    instructions:
      "对比竞品时列出功能矩阵、定价、优劣势与可借鉴点，标注信息来源与观察时间。",
  },
  knowledge: {
    description: "集中维护团队文档、规范与常见问题解答。",
    instructions: "回答时优先引用知识库条目并注明出处；缺失内容标记为待补充。",
  },
  delivery: {
    description: "管理交付里程碑、风险与验收清单。",
    instructions:
      "按里程碑推进交付：拆解任务、明确负责人与截止时间，及时暴露风险。",
  },
  bugTracking: {
    description: "记录、分派并跟踪缺陷直至修复验证。",
    instructions:
      "每个缺陷记录复现步骤、影响范围、严重级别与修复状态，修复后回归验证。",
  },
};

/** Help element the name input must reference through aria-describedby. */
const NAME_HELP_ID = "project-name-help";

/** Silence the jsdom-only AntD modal measurement warning used by these tests. */
function stubGetComputedStyle() {
  const nativeGetComputedStyle = window.getComputedStyle.bind(window);
  return vi
    .spyOn(window, "getComputedStyle")
    .mockImplementation((element) => nativeGetComputedStyle(element));
}

function CurrentPath() {
  const location = useLocation();
  return (
    <output data-testid="current-path">
      {location.pathname + location.search}
    </output>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("project-space pages against the real API response shapes", () => {
  it("renders a list item with an epoch-second timestamp and no instructions field", async () => {
    list.mockResolvedValue({
      items: [summary],
      limit: 20,
      offset: 0,
      has_more: false,
    });

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ProjectsPage />
      </MemoryRouter>,
    );

    const link = await screen.findByRole("link", {
      name: "打开项目：熊宝项目",
    });
    expect(link).toHaveAttribute("href", "/projects/project-1");
    expect(screen.getByText(/更新于.*2023/)).toBeInTheDocument();
    expect(list).toHaveBeenCalledWith({ q: "", limit: 20, offset: 0 });
  });

  it("accepts an invite from the project list only after a user click", async () => {
    const user = userEvent.setup();
    list.mockResolvedValue({
      items: [],
      limit: 20,
      offset: 0,
      has_more: false,
    });
    acceptInvite.mockResolvedValue({
      status: "joined",
      project_id: "joined-project",
      role: "member",
    });

    render(
      <MemoryRouter initialEntries={["/projects?invite=secret-token"]}>
        <Routes>
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:projectId" element={<div>已加入项目</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("收到项目邀请")).toBeInTheDocument();
    expect(acceptInvite).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "接受邀请" }));
    expect(acceptInvite).toHaveBeenCalledWith("secret-token");
    expect(await screen.findByText("已加入项目")).toBeInTheDocument();
  });

  it("clears a terminal invite token from the URL while showing its error", async () => {
    const user = userEvent.setup();
    list.mockResolvedValue({
      items: [],
      limit: 20,
      offset: 0,
      has_more: false,
    });
    acceptInvite.mockRejectedValue(
      new Error(
        '410 - {"error":{"code":"INVITE_EXPIRED","message":"invite expired"}}',
      ),
    );

    render(
      <MemoryRouter initialEntries={["/projects?invite=expired-token"]}>
        <CurrentPath />
        <ProjectsPage />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: "接受邀请" }));
    expect(acceptInvite).toHaveBeenCalledWith("expired-token");
    await waitFor(() =>
      expect(screen.getByTestId("current-path")).toHaveTextContent(
        /^\/projects$/,
      ),
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("收到项目邀请")).toBeNull();
  });

  it("removes a declined invite and its previous error", async () => {
    const user = userEvent.setup();
    list.mockResolvedValue({
      items: [],
      limit: 20,
      offset: 0,
      has_more: false,
    });
    acceptInvite.mockRejectedValue(new Error("Temporary failure"));

    render(
      <MemoryRouter initialEntries={["/projects?invite=retry-token"]}>
        <CurrentPath />
        <ProjectsPage />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: "接受邀请" }));
    expect(await screen.findByText("Temporary failure")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /取\s*消/ }));
    expect(screen.getByTestId("current-path")).toHaveTextContent("/projects");
    expect(screen.queryByText("Temporary failure")).toBeNull();
  });

  it("keeps an approval-required invitee outside the project", async () => {
    const user = userEvent.setup();
    list.mockResolvedValue({
      items: [],
      limit: 20,
      offset: 0,
      has_more: false,
    });
    acceptInvite.mockResolvedValue({
      status: "pending_approval",
      project_id: "private-project",
      request_id: "request-1",
    });

    render(
      <MemoryRouter initialEntries={["/projects?invite=approval-token"]}>
        <Routes>
          <Route path="/projects" element={<ProjectsPage />} />
          <Route
            path="/projects/:projectId"
            element={<div>private content</div>}
          />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: "接受邀请" }));
    expect(
      await screen.findByText("加入申请已提交，等待项目管理员审批。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("private content")).toBeNull();
  });

  it("renders authorized detail and enables the owner member workflow", async () => {
    get.mockResolvedValue({ ...summary, instructions: "只在详情中返回的指令" });
    members.mockResolvedValue([
      { user_id: 1, username: "alice", role: "owner" },
    ]);

    render(
      <MemoryRouter initialEntries={["/projects/project-1"]}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("只在详情中返回的指令")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "动态" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "计划" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "任务" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "资产" })).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "邀请成员" })).toBeEnabled();
    expect(screen.getByText(/更新于.*2023/)).toBeInTheDocument();
  });

  it("lets a member read the project without showing the edit action", async () => {
    get.mockResolvedValue({
      ...summary,
      my_role: "member",
      instructions: "成员可读",
    });
    members.mockResolvedValue([
      { user_id: 1, username: "alice", role: "owner" },
      { user_id: 2, username: "bob", role: "member" },
    ]);

    render(
      <MemoryRouter initialEntries={["/projects/project-1"]}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("成员可读")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "邀请成员" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "编辑项目资料" }),
    ).not.toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();
  });

  it("does not reveal a project name when the API denies membership", async () => {
    get.mockRejectedValue(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    members.mockResolvedValue([]);

    render(
      <MemoryRouter initialEntries={["/projects/private-project"]}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(screen.queryByText("熊宝项目")).not.toBeInTheDocument();
    expect(screen.queryByText("只在详情中返回的指令")).not.toBeInTheDocument();
  });

  it("preserves the creation draft after a failed POST and saves it on retry", async () => {
    // jsdom does not implement the pseudo-element variant AntD uses to
    // measure the modal scrollbar; the real element style is sufficient here.
    const nativeGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
      nativeGetComputedStyle(element),
    );
    const user = userEvent.setup();
    const onSaved = vi.fn();
    create.mockRejectedValueOnce(
      new Error("503 - service temporarily unavailable"),
    );
    create.mockResolvedValueOnce({ ...summary, instructions: "" });

    render(<CreateProjectModal open onClose={vi.fn()} onSaved={onSaved} />);

    const name = screen.getByPlaceholderText("例如：产品需求管理");
    await user.type(name, "熊宝项目");
    await user.click(screen.getByRole("button", { name: /创\s*建/ }));

    expect(
      await screen.findByText("service temporarily unavailable"),
    ).toBeInTheDocument();
    expect(name).toHaveValue("熊宝项目");
    expect(onSaved).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /创\s*建/ }));
    expect(onSaved).toHaveBeenCalledWith({ ...summary, instructions: "" });
    expect(create).toHaveBeenCalledWith({ name: "熊宝项目" });
  });

  it("keeps a typed draft when a template switch is cancelled", async () => {
    const nativeGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
      nativeGetComputedStyle(element),
    );
    const user = userEvent.setup();

    render(<CreateProjectModal open onClose={vi.fn()} onSaved={vi.fn()} />);

    const name = screen.getByPlaceholderText("例如：产品需求管理");
    await user.type(name, "自定义项目");
    const template = screen.getByRole("button", { name: /产品需求管理/ });
    await user.click(template);

    const keepDraft = await screen.findByRole("button", {
      name: "保留当前内容",
    });
    await user.click(keepDraft);
    expect(name).toHaveValue("自定义项目");
    expect(create).not.toHaveBeenCalled();
  });

  it("clears the name and applies template copy only after confirming the overwrite", async () => {
    const nativeGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
      nativeGetComputedStyle(element),
    );
    const user = userEvent.setup();

    render(<CreateProjectModal open onClose={vi.fn()} onSaved={vi.fn()} />);

    const name = screen.getByPlaceholderText("例如：产品需求管理");
    await user.type(name, "自定义项目");
    expect(screen.getByRole("button", { name: /创\s*建/ })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: /产品需求管理/ }));
    await user.click(await screen.findByRole("button", { name: /覆\s*盖/ }));
    expect(name).toHaveValue("");
    expect(screen.getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.requirements.description,
    );
    expect(screen.getByLabelText("项目指令")).toHaveValue(
      TEMPLATE_COPY.requirements.instructions,
    );
    expect(screen.getByRole("button", { name: /创\s*建/ })).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
  });

  it("keeps the list, route and server search working from the list heading", async () => {
    const user = userEvent.setup();
    list.mockResolvedValue({
      items: [summary],
      limit: 20,
      offset: 0,
      has_more: false,
    });

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ProjectsPage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("link", { name: "打开项目：熊宝项目" }),
    ).toHaveAttribute("href", "/projects/project-1");

    await user.type(screen.getByPlaceholderText("搜索项目名称"), "熊宝{Enter}");
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith({
        q: "熊宝",
        limit: 20,
        offset: 0,
      }),
    );
    expect(
      screen.getByRole("link", { name: "打开项目：熊宝项目" }),
    ).toHaveAttribute("href", "/projects/project-1");
  });

  it("offers five home template entries that prefill copy but not the name", async () => {
    const nativeGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
      nativeGetComputedStyle(element),
    );
    const user = userEvent.setup();
    list.mockResolvedValue({
      items: [summary],
      limit: 20,
      offset: 0,
      has_more: false,
    });

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ProjectsPage />
      </MemoryRouter>,
    );

    expect(
      await screen.findAllByRole("button", { name: /^使用模板：/ }),
    ).toHaveLength(5);

    await user.click(
      screen.getByRole("button", { name: "使用模板：产品需求管理" }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("项目名称")).toHaveValue("");
    expect(within(dialog).getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.requirements.description,
    );
    expect(within(dialog).getByLabelText("项目指令")).toHaveValue(
      TEMPLATE_COPY.requirements.instructions,
    );
    expect(
      within(dialog).getByRole("button", { name: /创\s*建/ }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: /产品需求管理/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(within(dialog).queryByText("连接器")).toBeNull();
    expect(within(dialog).queryByText("技能")).toBeNull();
  });

  it("creates with a user-typed name and the selected template copy", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();
    list.mockResolvedValue({
      items: [],
      limit: 20,
      offset: 0,
      has_more: false,
    });
    create.mockResolvedValue({
      ...summary,
      instructions: TEMPLATE_COPY.requirements.instructions,
    });

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ProjectsPage />
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "使用模板：产品需求管理" }),
    );
    const dialog = await screen.findByRole("dialog");
    const name = within(dialog).getByLabelText("项目名称");
    const submit = within(dialog).getByRole("button", { name: /创\s*建/ });
    expect(submit).toBeDisabled();

    await user.type(name, "我的需求项目");
    expect(submit).toBeEnabled();
    await user.click(submit);

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        name: "我的需求项目",
        description: TEMPLATE_COPY.requirements.description,
        instructions: TEMPLATE_COPY.requirements.instructions,
      }),
    );
  });

  it("opens a blank create form from the generic new-project action", async () => {
    const nativeGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
      nativeGetComputedStyle(element),
    );
    const user = userEvent.setup();
    list.mockResolvedValue({
      items: [],
      limit: 20,
      offset: 0,
      has_more: false,
    });

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ProjectsPage />
      </MemoryRouter>,
    );

    await user.click(
      (await screen.findAllByRole("button", { name: /新建项目/ }))[0],
    );
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("项目名称")).toHaveValue("");

    await user.click(within(dialog).getByRole("button", { name: /取\s*消/ }));

    await user.click(
      await screen.findByRole("button", {
        name: "使用模板：Bug 跟踪",
      }),
    );
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("项目名称")).toHaveValue("");
    expect(within(dialog).getByLabelText("项目指令")).toHaveValue(
      TEMPLATE_COPY.bugTracking.instructions,
    );
    await user.click(within(dialog).getByRole("button", { name: /取\s*消/ }));

    await user.click(
      (await screen.findAllByRole("button", { name: /新建项目/ }))[0],
    );
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("项目名称")).toHaveValue("");
  });

  it("asks before replacing edits in a modal opened from a home template", async () => {
    const nativeGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
      nativeGetComputedStyle(element),
    );
    const user = userEvent.setup();
    list.mockResolvedValue({
      items: [],
      limit: 20,
      offset: 0,
      has_more: false,
    });

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ProjectsPage />
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "使用模板：竞品分析" }),
    );
    const dialog = await screen.findByRole("dialog");
    const name = within(dialog).getByLabelText("项目名称");
    expect(name).toHaveValue("");
    expect(within(dialog).getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.competitor.description,
    );

    await user.type(name, "自定义项目");
    await user.click(within(dialog).getByRole("button", { name: /Bug 跟踪/ }));

    const keepDraft = await screen.findByRole("button", {
      name: "保留当前内容",
    });
    await user.click(keepDraft);
    expect(name).toHaveValue("自定义项目");
    expect(within(dialog).getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.competitor.description,
    );
    expect(create).not.toHaveBeenCalled();
  });

  it("keeps the edited draft and selected template when a switch is cancelled", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();

    render(
      <CreateProjectModal
        open
        initialTemplateId="requirements"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    const name = screen.getByLabelText("项目名称");
    const description = screen.getByLabelText("描述");
    await user.type(name, "我的需求项目");
    await user.clear(description);
    await user.type(description, "用户补充的描述");
    const requirements = screen.getByRole("button", { name: /产品需求管理/ });
    expect(requirements).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: /竞品分析/ }));
    await user.click(
      await screen.findByRole("button", { name: "保留当前内容" }),
    );

    expect(name).toHaveValue("我的需求项目");
    expect(description).toHaveValue("用户补充的描述");
    expect(screen.getByLabelText("项目指令")).toHaveValue(
      TEMPLATE_COPY.requirements.instructions,
    );
    expect(requirements).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /竞品分析/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(create).not.toHaveBeenCalled();
  });

  it("keeps the template selection and draft after a failed create, then retries", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();
    const onSaved = vi.fn();
    create.mockRejectedValueOnce(
      new Error("503 - service temporarily unavailable"),
    );
    create.mockResolvedValueOnce({
      ...summary,
      instructions: TEMPLATE_COPY.delivery.instructions,
    });

    render(
      <CreateProjectModal
        open
        initialTemplateId="delivery"
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );

    const name = screen.getByLabelText("项目名称");
    await user.type(name, "交付项目");
    await user.click(screen.getByRole("button", { name: /创\s*建/ }));

    expect(
      await screen.findByText("service temporarily unavailable"),
    ).toBeInTheDocument();
    expect(name).toHaveValue("交付项目");
    expect(screen.getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.delivery.description,
    );
    expect(screen.getByLabelText("项目指令")).toHaveValue(
      TEMPLATE_COPY.delivery.instructions,
    );
    expect(screen.getByRole("button", { name: /项目交付/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(onSaved).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /创\s*建/ }));
    await waitFor(() =>
      expect(create).toHaveBeenLastCalledWith({
        name: "交付项目",
        description: TEMPLATE_COPY.delivery.description,
        instructions: TEMPLATE_COPY.delivery.instructions,
      }),
    );
    expect(onSaved).toHaveBeenCalledWith({
      ...summary,
      instructions: TEMPLATE_COPY.delivery.instructions,
    });
  });

  it("keeps an open draft when the parent changes initialTemplateId", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSaved = vi.fn();
    const { rerender } = render(
      <CreateProjectModal
        open
        initialTemplateId="requirements"
        onClose={onClose}
        onSaved={onSaved}
      />,
    );

    const name = screen.getByLabelText("项目名称");
    const description = screen.getByLabelText("描述");
    await user.type(name, "我的需求项目");
    await user.clear(description);
    await user.type(description, "用户补充的描述");

    rerender(
      <CreateProjectModal
        open
        initialTemplateId="bugTracking"
        onClose={onClose}
        onSaved={onSaved}
      />,
    );

    expect(name).toHaveValue("我的需求项目");
    expect(description).toHaveValue("用户补充的描述");
    expect(screen.getByLabelText("项目指令")).toHaveValue(
      TEMPLATE_COPY.requirements.instructions,
    );
    expect(
      screen.getByRole("button", { name: /产品需求管理/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Bug 跟踪/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.queryByRole("button", { name: "保留当前内容" })).toBeNull();
  });

  it("applies a seed only on each close-to-open edge", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSaved = vi.fn();
    const { rerender } = render(
      <CreateProjectModal
        open
        initialTemplateId="requirements"
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    await user.type(screen.getByLabelText("项目名称"), "旧草稿");

    rerender(
      <CreateProjectModal
        open={false}
        initialTemplateId="requirements"
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    rerender(
      <CreateProjectModal
        open
        initialTemplateId={null}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );

    const reopenedForCreate = await screen.findByRole("dialog");
    expect(within(reopenedForCreate).getByLabelText("项目名称")).toHaveValue(
      "",
    );
    expect(within(reopenedForCreate).getByLabelText("描述")).toHaveValue("");
    expect(within(reopenedForCreate).getByLabelText("项目指令")).toHaveValue(
      "",
    );
    for (const templateName of [
      "产品需求管理",
      "竞品分析",
      "团队知识库",
      "项目交付",
      "Bug 跟踪",
    ]) {
      expect(
        within(reopenedForCreate).getByRole("button", {
          name: new RegExp(templateName),
        }),
      ).toHaveAttribute("aria-pressed", "false");
    }

    rerender(
      <CreateProjectModal
        open={false}
        initialTemplateId={null}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    rerender(
      <CreateProjectModal
        open
        initialTemplateId="knowledge"
        onClose={onClose}
        onSaved={onSaved}
      />,
    );

    const reopenedFromTemplate = await screen.findByRole("dialog");
    expect(within(reopenedFromTemplate).getByLabelText("项目名称")).toHaveValue(
      "",
    );
    expect(within(reopenedFromTemplate).getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.knowledge.description,
    );
    expect(within(reopenedFromTemplate).getByLabelText("项目指令")).toHaveValue(
      TEMPLATE_COPY.knowledge.instructions,
    );
    expect(
      within(reopenedFromTemplate).getByRole("button", { name: /团队知识库/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("loads an existing project into edit mode without template controls", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();
    const onSaved = vi.fn();
    const target: ProjectRecord = {
      ...summary,
      my_role: "owner",
      instructions: "既有项目指令",
      instructions_sha256: "sha256-placeholder",
    };
    update.mockResolvedValue({ ...target, name: "更名项目" });

    render(
      <CreateProjectModal
        open
        editTarget={target}
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );

    expect(screen.queryByText("从模板开始")).toBeNull();
    expect(screen.queryByRole("button", { name: /产品需求管理/ })).toBeNull();
    const name = screen.getByLabelText("项目名称");
    expect(name).toHaveValue("熊宝项目");
    expect(screen.getByLabelText("描述")).toHaveValue("真实项目描述");
    expect(screen.getByLabelText("项目指令")).toHaveValue("既有项目指令");

    await user.clear(name);
    await user.type(name, "更名项目");
    await user.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("project-1", {
        name: "更名项目",
        description: "真实项目描述",
        instructions: "既有项目指令",
      }),
    );
    expect(onSaved).toHaveBeenCalledWith({ ...target, name: "更名项目" });
  });

  it("ties the name rule and error text to the input and exposes invalid typing", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();

    render(<CreateProjectModal open onClose={vi.fn()} onSaved={vi.fn()} />);

    const name = screen.getByLabelText("项目名称");
    expect(name).toBeRequired();
    expect(name).toHaveAttribute("aria-describedby", NAME_HELP_ID);
    const help = document.getElementById(NAME_HELP_ID);
    expect(help).not.toBeNull();
    expect(help).toHaveTextContent(/名称 1–15 个字符/);
    expect(name).toHaveAccessibleDescription(/名称 1–15 个字符/);
    expect(name).toHaveAttribute("aria-invalid", "false");

    await user.type(name, "   ");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription("请输入项目名称");
    expect(screen.getByRole("button", { name: /创\s*建/ })).toBeDisabled();

    await user.clear(name);
    await user.type(name, "有效名称");
    expect(name).toHaveAttribute("aria-invalid", "false");
    expect(name).toHaveAccessibleDescription(/名称 1–15 个字符/);
    expect(screen.getByRole("button", { name: /创\s*建/ })).toBeEnabled();

    await user.clear(name);
    await user.type(name, "1234567890123456");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription("名称最多 15 个字符");
    expect(screen.getByRole("button", { name: /创\s*建/ })).toBeDisabled();
  });

  it("closes instead of submitting a draft to a different edit target", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSaved = vi.fn();
    const targetA: ProjectRecord = {
      ...summary,
      project_id: "project-a",
      name: "项目 A",
      description: "A 描述",
      instructions: "A 指令",
      instructions_sha256: "sha-a",
    };
    const targetB: ProjectRecord = {
      ...summary,
      project_id: "project-b",
      name: "项目 B",
      description: "B 描述",
      instructions: "B 指令",
      instructions_sha256: "sha-b",
    };
    update.mockResolvedValue({ ...targetB, name: "B 改名" });

    const { rerender } = render(
      <CreateProjectModal
        open
        editTarget={targetA}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    const name = screen.getByLabelText("项目名称");
    await user.clear(name);
    await user.type(name, "A 改名草稿");

    rerender(
      <CreateProjectModal
        open
        editTarget={targetB}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );

    expect(onClose).toHaveBeenCalled();
    expect(update).not.toHaveBeenCalled();

    // Even if the parent ignored onClose, save must not target B.
    await user.click(screen.getByRole("button", { name: /保\s*存/ }));
    expect(update).not.toHaveBeenCalled();

    rerender(
      <CreateProjectModal
        open={false}
        editTarget={targetB}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    rerender(
      <CreateProjectModal
        open
        editTarget={targetB}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );

    const freshName = screen.getByLabelText("项目名称");
    expect(freshName).toHaveValue("项目 B");
    expect(screen.getByLabelText("描述")).toHaveValue("B 描述");
    expect(screen.getByLabelText("项目指令")).toHaveValue("B 指令");

    await user.clear(freshName);
    await user.type(freshName, "B 改名");
    await user.click(screen.getByRole("button", { name: /保\s*存/ }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("project-b", {
        name: "B 改名",
        description: "B 描述",
        instructions: "B 指令",
      }),
    );
    expect(onSaved).toHaveBeenCalledWith({ ...targetB, name: "B 改名" });
  });

  it("keeps an open edit draft when the target is refreshed with the same id", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSaved = vi.fn();
    const targetA: ProjectRecord = {
      ...summary,
      project_id: "project-a",
      name: "项目 A",
      description: "A 描述",
      instructions: "A 指令",
      instructions_sha256: "sha-a",
    };
    const refreshedA: ProjectRecord = {
      ...targetA,
      name: "项目 A（服务端刷新）",
      description: "A 描述（服务端刷新）",
    };
    update.mockResolvedValue({ ...refreshedA, name: "A 草稿" });

    const { rerender } = render(
      <CreateProjectModal
        open
        editTarget={targetA}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
    const name = screen.getByLabelText("项目名称");
    await user.clear(name);
    await user.type(name, "A 草稿");

    rerender(
      <CreateProjectModal
        open
        editTarget={refreshedA}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );

    expect(onClose).not.toHaveBeenCalled();
    expect(name).toHaveValue("A 草稿");
    expect(screen.getByLabelText("描述")).toHaveValue("A 描述");

    await user.click(screen.getByRole("button", { name: /保\s*存/ }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("project-a", {
        name: "A 草稿",
        description: "A 描述",
        instructions: "A 指令",
      }),
    );
  });

  it("ignores a stale overwrite confirmation from a previous modal session", async () => {
    stubGetComputedStyle();
    let staleOnOk: (() => void) | undefined;
    const confirmSpy = vi
      .spyOn(Modal, "confirm")
      .mockImplementation((config) => {
        staleOnOk = config.onOk;
        return {
          destroy: vi.fn(),
          update: vi.fn(),
        } as unknown as ReturnType<typeof Modal.confirm>;
      });
    const user = userEvent.setup();
    const onClose = vi.fn();

    const { rerender } = render(
      <CreateProjectModal
        open
        initialTemplateId="requirements"
        onClose={onClose}
        onSaved={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("项目名称"), "自定义项目");
    await user.click(screen.getByRole("button", { name: /竞品分析/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(staleOnOk).toBeTypeOf("function");

    rerender(
      <CreateProjectModal
        open={false}
        initialTemplateId={null}
        onClose={onClose}
        onSaved={vi.fn()}
      />,
    );
    rerender(
      <CreateProjectModal
        open
        initialTemplateId="knowledge"
        onClose={onClose}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.knowledge.description,
    );

    act(() => {
      staleOnOk?.();
    });

    expect(screen.getByLabelText("项目名称")).toHaveValue("");
    expect(screen.getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.knowledge.description,
    );
    expect(screen.getByLabelText("项目指令")).toHaveValue(
      TEMPLATE_COPY.knowledge.instructions,
    );
    expect(screen.getByRole("button", { name: /团队知识库/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /竞品分析/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("keeps a remounted session's draft when an old confirm callback fires", async () => {
    stubGetComputedStyle();
    let staleOnOk: (() => void) | undefined;
    vi.spyOn(Modal, "confirm").mockImplementation((config) => {
      staleOnOk = config.onOk;
      return {
        destroy: vi.fn(),
        update: vi.fn(),
      } as unknown as ReturnType<typeof Modal.confirm>;
    });
    const user = userEvent.setup();
    const onClose = vi.fn();

    const first = render(
      <CreateProjectModal
        open
        initialTemplateId="requirements"
        onClose={onClose}
        onSaved={vi.fn()}
      />,
    );
    await user.type(screen.getByLabelText("项目名称"), "自定义项目");
    await user.click(screen.getByRole("button", { name: /竞品分析/ }));
    expect(staleOnOk).toBeTypeOf("function");

    first.unmount();
    render(
      <CreateProjectModal
        open
        initialTemplateId="knowledge"
        onClose={onClose}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.knowledge.description,
    );

    act(() => {
      staleOnOk?.();
    });

    expect(screen.getByLabelText("项目名称")).toHaveValue("");
    expect(screen.getByLabelText("描述")).toHaveValue(
      TEMPLATE_COPY.knowledge.description,
    );
    expect(screen.getByLabelText("项目指令")).toHaveValue(
      TEMPLATE_COPY.knowledge.instructions,
    );
    expect(screen.getByRole("button", { name: /团队知识库/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /竞品分析/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("removes a pending overwrite confirmation when its parent session closes", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();
    const onClose = vi.fn();

    const { rerender } = render(
      <CreateProjectModal open onClose={onClose} onSaved={vi.fn()} />,
    );
    await user.type(screen.getByLabelText("项目名称"), "自定义项目");
    await user.click(screen.getByRole("button", { name: /竞品分析/ }));
    expect(
      await screen.findByRole("button", { name: /覆\s*盖/ }),
    ).toBeInTheDocument();

    rerender(
      <CreateProjectModal open={false} onClose={onClose} onSaved={vi.fn()} />,
    );

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /覆\s*盖/ })).toBeNull(),
    );
    expect(screen.queryByRole("button", { name: "保留当前内容" })).toBeNull();
  });

  it("destroys a pending overwrite confirmation on a target switch or unmount", async () => {
    stubGetComputedStyle();
    const destroy = vi.fn();
    let staleOnOk: (() => void) | undefined;
    vi.spyOn(Modal, "confirm").mockImplementation((config) => {
      staleOnOk = config.onOk;
      return {
        destroy,
        update: vi.fn(),
      } as unknown as ReturnType<typeof Modal.confirm>;
    });
    const user = userEvent.setup();
    const onClose = vi.fn();
    const targetA: ProjectRecord = {
      ...summary,
      project_id: "project-a",
      name: "项目 A",
      description: "A 描述",
      instructions: "A 指令",
      instructions_sha256: "sha-a",
    };

    const first = render(
      <CreateProjectModal open onClose={onClose} onSaved={vi.fn()} />,
    );
    await user.type(screen.getByLabelText("项目名称"), "自定义项目");
    await user.click(screen.getByRole("button", { name: /竞品分析/ }));
    expect(destroy).not.toHaveBeenCalled();

    first.rerender(
      <CreateProjectModal
        open
        editTarget={targetA}
        onClose={onClose}
        onSaved={vi.fn()}
      />,
    );
    expect(onClose).toHaveBeenCalled();
    expect(destroy).toHaveBeenCalledTimes(1);

    act(() => {
      staleOnOk?.();
    });
    expect(screen.getByLabelText("项目名称")).toHaveValue("自定义项目");
    expect(screen.getByLabelText("描述")).toHaveValue("");

    first.unmount();

    const second = render(
      <CreateProjectModal open onClose={onClose} onSaved={vi.fn()} />,
    );
    await user.type(screen.getByLabelText("项目名称"), "另一个草稿");
    await user.click(screen.getByRole("button", { name: /竞品分析/ }));
    expect(destroy).toHaveBeenCalledTimes(1);

    second.unmount();
    expect(destroy).toHaveBeenCalledTimes(2);
  });

  it("announces name errors while the neutral rule stays a non-live reference", async () => {
    stubGetComputedStyle();
    const user = userEvent.setup();

    render(<CreateProjectModal open onClose={vi.fn()} onSaved={vi.fn()} />);

    const name = screen.getByLabelText("项目名称");
    expect(name).toHaveAttribute("aria-describedby", NAME_HELP_ID);
    const neutralHelp = document.getElementById(NAME_HELP_ID);
    expect(neutralHelp).not.toBeNull();
    expect(neutralHelp).not.toHaveAttribute("role");
    expect(neutralHelp).not.toHaveAttribute("aria-live");

    await user.type(name, "有效名称");
    expect(document.getElementById(NAME_HELP_ID)).toBe(neutralHelp);
    expect(neutralHelp).not.toHaveAttribute("role");
    expect(screen.queryByRole("alert")).toBeNull();

    await user.clear(name);
    await user.type(name, "   ");
    expect(name).toHaveAttribute("aria-invalid", "true");
    const blankError = screen.getByRole("alert");
    expect(blankError).toHaveAttribute("id", NAME_HELP_ID);
    expect(blankError).toHaveTextContent("请输入项目名称");
    expect(name).toHaveAccessibleDescription("请输入项目名称");

    await user.clear(name);
    await user.type(name, "1234567890123456");
    expect(name).toHaveAttribute("aria-invalid", "true");
    const longError = screen.getByRole("alert");
    expect(longError).toHaveAttribute("id", NAME_HELP_ID);
    expect(longError).toHaveTextContent("名称最多 15 个字符");
    expect(screen.getAllByRole("alert")).toHaveLength(1);

    await user.clear(name);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.getElementById(NAME_HELP_ID)).toBe(neutralHelp);
    expect(neutralHelp).not.toHaveAttribute("role");
  });

  it("frames the home introduction as one labelled region with a single h1", async () => {
    list.mockResolvedValue({
      items: [],
      limit: 20,
      offset: 0,
      has_more: false,
    });

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ProjectsPage />
      </MemoryRouter>,
    );

    const intro = await screen.findByRole("region", { name: "项目" });
    expect(within(intro).getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(
      within(intro).getByText(
        "人的长期协作空间：成员、计划、任务与资产按项目沉淀。",
      ),
    ).toBeInTheDocument();
    expect(within(intro).getByRole("button", { name: "刷新" })).toBeEnabled();
    expect(
      within(intro).getByRole("button", { name: /新建项目/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("shows compact cards with only server-derived summary fields", async () => {
    list.mockResolvedValue({
      items: [summary],
      limit: 20,
      offset: 0,
      has_more: false,
    });

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ProjectsPage />
      </MemoryRouter>,
    );

    const link = await screen.findByRole("link", {
      name: "打开项目：熊宝项目",
    });
    expect(link).toHaveAttribute("href", "/projects/project-1");
    expect(within(link).getByText("熊宝项目")).toBeInTheDocument();
    expect(within(link).getByText("所有者")).toBeInTheDocument();
    expect(within(link).getByText(/更新于.*2023/)).toBeInTheDocument();
    expect(within(link).queryByText("真实项目描述")).toBeNull();
    expect(within(link).queryByText(/名成员/)).toBeNull();
    expect(within(link).queryByRole("button")).toBeNull();
  });
});
