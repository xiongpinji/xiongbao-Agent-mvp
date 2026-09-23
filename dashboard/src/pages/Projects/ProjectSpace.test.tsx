import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { list, get, members, create, update } = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  members: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}));

vi.mock("../../api/modules/projects", () => ({
  PROJECTS_PAGE_SIZE: 20,
  projectsApi: { list, get, members, create, update },
}));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("../../layouts/PageShell", () => ({
  default: ({
    title,
    actions,
    children,
  }: {
    title: string;
    actions?: ReactNode;
    children: ReactNode;
  }) => (
    <main>
      <h1>{title}</h1>
      {actions}
      {children}
    </main>
  ),
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

const summary = {
  project_id: "project-1",
  name: "熊宝项目",
  description: "真实项目描述",
  my_role: "owner",
  member_count: 1,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

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

  it("renders authorized detail and marks unfinished project actions unavailable", async () => {
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
    expect(screen.getByRole("button", { name: "邀请成员" })).toBeDisabled();
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

  it("prefills from a template only after confirming the overwrite", async () => {
    const nativeGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
      nativeGetComputedStyle(element),
    );
    const user = userEvent.setup();

    render(<CreateProjectModal open onClose={vi.fn()} onSaved={vi.fn()} />);

    const name = screen.getByPlaceholderText("例如：产品需求管理");
    await user.type(name, "自定义项目");

    await user.click(screen.getByRole("button", { name: /产品需求管理/ }));
    await user.click(await screen.findByRole("button", { name: /覆\s*盖/ }));
    expect(name).toHaveValue("产品需求管理");
    expect(create).not.toHaveBeenCalled();
  });
});
