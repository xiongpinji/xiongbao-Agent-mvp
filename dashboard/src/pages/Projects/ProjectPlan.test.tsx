import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { catalogFixture } from "./todoCatalog.testFixtures";
vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  const { catalogTestTranslation } = await import("./todoCatalog.testFixtures");
  return { ...actual, useTranslation: catalogTestTranslation };
});

const { catalogGet, catalogCreatePriority } = vi.hoisted(() => ({
  catalogGet: vi.fn(),
  catalogCreatePriority: vi.fn(),
}));
vi.mock("../../api/modules/projectTodoCatalog", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectTodoCatalog")
  >();
  return {
    ...actual,
    projectTodoCatalogApi: {
      ...actual.projectTodoCatalogApi,
      get: catalogGet,
      createPriority: catalogCreatePriority,
    },
  };
});

const { list, get, listComments, createComment, create, update, remove, bulk } =
  vi.hoisted(() => ({
    list: vi.fn(),
    get: vi.fn(),
    listComments: vi.fn(),
    createComment: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    bulk: vi.fn(),
  }));

const { currentUserId } = vi.hoisted(() => ({
  currentUserId: { value: 2 as number | null },
}));

vi.mock("../../api/modules/projectTodos", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectTodos")
  >();
  return {
    ...actual,
    projectTodosApi: {
      list,
      create,
      get,
      listComments,
      createComment,
      update,
      remove,
      bulk,
    },
  };
});

vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () =>
    currentUserId.value == null
      ? null
      : { id: currentUserId.value, username: "bob" },
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

import ProjectPlan from "./ProjectPlan";
import type { ProjectTodo } from "../../api/modules/projectTodos";

const todoOpen: ProjectTodo = {
  todo_id: "t1",
  project_id: "p1",
  title: "写周报",
  description: "本周进展",
  description_format: "plain" as const,
  start_date: null,
  due_date: null,
  priority_id: null,
  tag_ids: [] as string[],
  catalog_revision: 1,
  status: "todo" as const,
  creator_user_id: 1,
  assignee_user_id: 2,
  version: 3,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

const todoDoing: ProjectTodo = {
  todo_id: "t2",
  project_id: "p1",
  title: "修缺陷",
  description: "",
  description_format: "plain" as const,
  start_date: null,
  due_date: null,
  priority_id: null,
  tag_ids: [] as string[],
  catalog_revision: 1,
  status: "in_progress" as const,
  creator_user_id: 2,
  assignee_user_id: null,
  version: 1,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

const todoOther: ProjectTodo = {
  todo_id: "t3",
  project_id: "p1",
  title: "alice 的待办",
  description: "",
  description_format: "plain" as const,
  start_date: null,
  due_date: null,
  priority_id: null,
  tag_ids: [] as string[],
  catalog_revision: 1,
  status: "todo" as const,
  creator_user_id: 1,
  assignee_user_id: null,
  version: 1,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

const members = [
  { user_id: 1, username: "alice", role: "owner" as const },
  { user_id: 2, username: "bob", role: "member" as const },
];

function listResponse(
  items = [todoOpen, todoDoing, todoOther],
  hasMore = false,
) {
  return {
    items,
    limit: 50,
    offset: 0,
    has_more: hasMore,
  };
}

function renderPlan(role: "owner" | "member" = "owner") {
  return render(<ProjectPlan projectId="p1" role={role} members={members} />);
}

function visibleDropdown(): HTMLElement {
  const dropdowns = Array.from(
    document.querySelectorAll<HTMLElement>(".ant-select-dropdown"),
  ).filter((node) => !node.classList.contains("ant-select-dropdown-hidden"));
  const dropdown = dropdowns[dropdowns.length - 1];
  expect(dropdown).toBeTruthy();
  return dropdown;
}

function chooseOption(combobox: HTMLElement, optionText: string): void {
  fireEvent.mouseDown(combobox);
  fireEvent.click(within(visibleDropdown()).getByText(optionText));
}

function chooseSelectOption(name: string, optionText: string): void {
  chooseOption(screen.getByRole("combobox", { name }), optionText);
}

function rowFor(title: string): HTMLElement {
  const row = screen.getByText(title).closest("tr");
  expect(row).toBeTruthy();
  return row as HTMLElement;
}

beforeEach(() => {
  catalogGet.mockReset().mockResolvedValue(catalogFixture);
  catalogCreatePriority.mockReset();
  list.mockReset();
  get.mockReset();
  listComments.mockReset();
  createComment.mockReset();
  create.mockReset();
  update.mockReset();
  remove.mockReset();
  bulk.mockReset();
  currentUserId.value = 2;
  list.mockResolvedValue(listResponse());
  get.mockResolvedValue(todoOpen);
  listComments.mockResolvedValue({ items: [], next_cursor: null });
});

describe("ProjectPlan table/board against the PS-04 contract", () => {
  it("retains an open catalog manager draft while a slow initial todo list requires a newer catalog snapshot", async () => {
    const user = userEvent.setup();
    let resolveList!: (value: ReturnType<typeof listResponse>) => void;
    let resolveCatalog!: (value: typeof catalogFixture) => void;
    const latest = {
      ...catalogFixture,
      revision: 2,
      priorities: catalogFixture.priorities.map((item) =>
        item.priority_id === "pr1" ? { ...item, name: "服务器当前目录" } : item,
      ),
    };
    list.mockReturnValueOnce(
      new Promise<ReturnType<typeof listResponse>>((resolve) => {
        resolveList = resolve;
      }),
    );
    catalogGet
      .mockReset()
      .mockResolvedValueOnce(catalogFixture)
      .mockReturnValueOnce(
        new Promise<typeof catalogFixture>((resolve) => {
          resolveCatalog = resolve;
        }),
      )
      .mockResolvedValue(latest);
    renderPlan();
    await user.click(screen.getByRole("button", { name: "新建待办" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "选择优先级" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "选择优先级" }));
    await user.click(screen.getByRole("button", { name: "管理目录" }));
    const name = screen.getByRole("textbox", { name: "选项名称" });
    const manager = name.closest<HTMLElement>('[role="dialog"]')!;
    fireEvent.change(name, { target: { value: "延迟刷新中的草稿" } });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "purple",
    );
    await act(async () => {
      resolveList(
        listResponse([
          { ...todoOpen, priority_id: "pr1", catalog_revision: 2 },
        ]),
      );
    });
    await waitFor(() => expect(catalogGet).toHaveBeenCalledTimes(2));
    expect(manager).toBeInTheDocument();
    expect(manager).toBeVisible();
    expect(within(manager).getByRole("status")).toHaveTextContent(/加载/);
    expect(within(manager).getByRole("textbox", { name: "选项名称" })).toBe(
      name,
    );
    expect(name).toHaveValue("延迟刷新中的草稿");
    expect(within(manager).getByRole("combobox", { name: "颜色" })).toHaveValue(
      "purple",
    );
    const add = within(manager).getByRole("button", { name: "新增选项" });
    expect(add).toBeDisabled();
    fireEvent.click(add);
    expect(catalogCreatePriority).not.toHaveBeenCalled();
    expect(within(rowFor("写周报")).queryByText("紧急")).toBeNull();
    await act(async () => {
      resolveCatalog(latest);
    });
    await waitFor(() =>
      expect(
        within(manager).getByRole("button", { name: "新增选项" }),
      ).toBeEnabled(),
    );
    expect(screen.getByRole("textbox", { name: "选项名称" })).toBe(name);
    expect(name).toHaveValue("延迟刷新中的草稿");
    expect(screen.getByRole("combobox", { name: "颜色" })).toHaveValue(
      "purple",
    );
    expect(within(rowFor("写周报")).getByText("服务器当前目录")).toBeVisible();
    const item = {
      ...latest.priorities[0],
      priority_id: "pr-new",
      name: "延迟刷新中的草稿",
      color: "purple" as const,
    };
    catalogCreatePriority.mockResolvedValue({ revision: 3, item });
    catalogGet.mockResolvedValue({
      ...latest,
      revision: 3,
      priorities: [...latest.priorities, item],
    });
    await user.click(add);
    await waitFor(() =>
      expect(catalogCreatePriority).toHaveBeenCalledWith("p1", {
        expected_revision: 2,
        name: item.name,
        color: "purple",
      }),
    );
  }, 15000);
  it("returns from catalog management to the same field draft and selects a newly persisted option", async () => {
    const user = userEvent.setup();
    const item = {
      ...catalogFixture.priorities[0],
      priority_id: "new-priority",
      name: "研发优先",
      color: "green" as const,
      position: 1,
    };
    catalogCreatePriority.mockImplementation(async () => {
      catalogGet.mockResolvedValue({
        ...catalogFixture,
        revision: 2,
        priorities: [...catalogFixture.priorities, item],
      });
      return { revision: 2, item };
    });
    create.mockResolvedValue({
      ...todoOpen,
      priority_id: item.priority_id,
      catalog_revision: 2,
    });
    renderPlan();
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建待办" }));
    fireEvent.change(screen.getByRole("textbox", { name: "标题" }), {
      target: { value: "保留任务草稿" },
    });
    fireEvent.change(screen.getByLabelText("开始日期"), {
      target: { value: "2020-01-01" },
    });
    await user.click(screen.getByRole("button", { name: "选择优先级" }));
    await user.click(screen.getByRole("button", { name: "管理目录" }));
    fireEvent.change(screen.getByRole("textbox", { name: "选项名称" }), {
      target: { value: item.name },
    });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "green",
    );
    await user.click(screen.getByRole("button", { name: "新增选项" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "管理目录" })).toBeNull(),
    );
    await user.click(screen.getByRole("button", { name: "完成选择" }));
    expect(
      screen.getByRole("button", { name: "选择优先级" }),
    ).toHaveTextContent(item.name);
    expect(screen.getByRole("textbox", { name: "标题" })).toHaveValue(
      "保留任务草稿",
    );
    expect(screen.getByLabelText("开始日期")).toHaveValue("2020-01-01");
    await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("p1", {
        title: "保留任务草稿",
        description: "",
        status: "todo",
        start_date: "2020-01-01",
        priority_id: "new-priority",
        expected_catalog_revision: 2,
      }),
    );
  }, 15000);
  it("retains catalog name/color drafts during explicit conflict refresh when the directory revision advances", async () => {
    const user = userEvent.setup();
    catalogCreatePriority.mockRejectedValue(
      new Error(
        '409 - {"error":{"code":"CONFLICT","details":{"reason":"catalog_revision_conflict"}}}',
      ),
    );
    renderPlan();
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建待办" }));
    await user.click(screen.getByRole("button", { name: "选择优先级" }));
    await user.click(screen.getByRole("button", { name: "管理目录" }));
    fireEvent.change(screen.getByRole("textbox", { name: "选项名称" }), {
      target: { value: "目录草稿保持" },
    });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "purple",
    );
    await user.click(screen.getByRole("button", { name: "新增选项" }));
    await screen.findByText("目录已被修改，草稿已保留，请刷新后比较再保存。");
    catalogGet.mockResolvedValue({ ...catalogFixture, revision: 2 });
    await user.click(screen.getByRole("button", { name: "刷新后比较" }));
    expect(
      await screen.findByRole("textbox", { name: "选项名称" }),
    ).toHaveValue("目录草稿保持");
    expect(screen.getByRole("combobox", { name: "颜色" })).toHaveValue(
      "purple",
    );
    expect(await screen.findByText("服务器当前值")).toBeVisible();
  }, 15000);
  it("refreshes mismatched catalog snapshots before displaying persisted names in table and board", async () => {
    list.mockResolvedValue(
      listResponse([
        {
          ...todoOpen,
          priority_id: "pr1",
          tag_ids: ["tag1"],
          catalog_revision: 2,
        },
      ]),
    );
    catalogGet.mockResolvedValueOnce(catalogFixture).mockResolvedValue({
      ...catalogFixture,
      revision: 2,
      priorities: [{ ...catalogFixture.priorities[0], name: "真实新名字" }],
    });
    renderPlan();
    expect(await screen.findByText("真实新名字")).toBeVisible();
    expect(screen.queryByText("紧急")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "看板" }));
    expect(
      within(screen.getByTestId("todo-card-t1")).getByText("真实新名字"),
    ).toBeVisible();
    expect(catalogGet.mock.calls.length).toBeGreaterThan(1);
  });
  it("sends explicit date null to clear but omits an unchanged overdue date on other edits", async () => {
    const user = userEvent.setup();
    const overdue = { ...todoOpen, due_date: "2020-01-01" };
    list.mockResolvedValue(listResponse([overdue]));
    update.mockResolvedValue({ ...overdue, due_date: null, version: 4 });
    renderPlan();
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "清空截止日期" }));
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        due_date: null,
      }),
    );
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑" }),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "标题" }), {
      target: { value: "改标题保留逾期" },
    });
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));
    await waitFor(() =>
      expect(update).toHaveBeenLastCalledWith("p1", "t1", {
        expected_version: 3,
        title: "改标题保留逾期",
      }),
    );
  });
  it("blocks date submission without server metadata and preserves entered dates across failed metadata refresh", async () => {
    const user = userEvent.setup();
    renderPlan();
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建待办" }));
    fireEvent.change(screen.getByRole("textbox", { name: "标题" }), {
      target: { value: "日期草稿" },
    });
    fireEvent.change(screen.getByLabelText("截止日期"), {
      target: { value: "2026-09-28" },
    });
    create.mockRejectedValue(
      new Error(
        '422 - {"error":{"code":"VALIDATION_ERROR","details":{"reason":"invalid_dates"}}}',
      ),
    );
    catalogGet.mockRejectedValue(new Error("503"));
    await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));
    expect(
      await screen.findByText("服务器日期加载失败，重试后才能编辑日期。"),
    ).toBeVisible();
    expect(screen.getByLabelText("截止日期")).toHaveValue("2026-09-28");
    expect(screen.getByLabelText("截止日期")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));
    expect(create).toHaveBeenCalledTimes(1);
  });
  it("preserves the editor and legal project content when an option 422 says not found", async () => {
    const user = userEvent.setup();
    update.mockRejectedValue(
      new Error(
        '422 - {"error":{"code":"VALIDATION_ERROR","message":"tag not found","details":{"reason":"invalid_tags"}}}',
      ),
    );
    renderPlan();
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑" }),
    );
    await user.click(screen.getByRole("button", { name: "选择标签" }));
    await user.click(screen.getByRole("checkbox", { name: "设计" }));
    await user.click(screen.getByRole("button", { name: "完成选择" }));
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));
    expect(
      await screen.findByText("标签选项已变化，请刷新目录并确认选择。"),
    ).toBeVisible();
    expect(screen.getByRole("dialog", { name: "编辑待办" })).toBeVisible();
    expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
  });
  it("creates with actual status and date/priority/tag fields against the catalog revision", async () => {
    const user = userEvent.setup();
    create.mockResolvedValue({ ...todoOpen, todo_id: "new" });
    renderPlan();
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建待办" }));
    const dialog = screen.getByRole("dialog", { name: "新建待办" });
    await user.type(
      within(dialog).getByRole("textbox", { name: "标题" }),
      "计划发布",
    );
    chooseOption(
      within(dialog).getByRole("combobox", { name: "状态" }),
      "进行中",
    );
    fireEvent.change(within(dialog).getByLabelText("开始日期"), {
      target: { value: "2020-01-01" },
    });
    fireEvent.change(within(dialog).getByLabelText("截止日期"), {
      target: { value: "2026-09-30" },
    });
    await user.click(
      within(dialog).getByRole("button", { name: "选择优先级" }),
    );
    await user.click(screen.getByRole("button", { name: "紧急" }));
    await user.click(within(dialog).getByRole("button", { name: "选择标签" }));
    await user.click(screen.getByRole("checkbox", { name: "设计" }));
    await user.click(screen.getByRole("button", { name: "完成选择" }));
    await user.click(within(dialog).getByRole("button", { name: /^创\s*建$/ }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("p1", {
        title: "计划发布",
        description: "",
        status: "in_progress",
        start_date: "2020-01-01",
        due_date: "2026-09-30",
        priority_id: "pr1",
        tag_ids: ["tag1"],
        expected_catalog_revision: 1,
      }),
    );
  }, 15000);
  it("retains editor draft on 409, displays refreshed server comparison and uses its new version after confirmation", async () => {
    const user = userEvent.setup();
    update
      .mockRejectedValueOnce(
        new Error(
          '409 - {"error":{"code":"CONFLICT","details":{"reason":"version_conflict"}}}',
        ),
      )
      .mockResolvedValue({ ...todoOpen, title: "我的草稿", version: 5 });
    get.mockResolvedValue({ ...todoOpen, title: "服务端更新", version: 4 });
    renderPlan();
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑" }),
    );
    const title = screen.getByRole("textbox", { name: "标题" });
    await user.clear(title);
    await user.type(title, "我的草稿");
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));
    expect(
      await screen.findByRole("dialog", { name: "编辑待办" }),
    ).toBeVisible();
    expect(title).toHaveValue("我的草稿");
    await user.click(screen.getByRole("button", { name: "刷新后比较" }));
    expect(await screen.findByText("服务端更新")).toBeVisible();
    expect(title).toHaveValue("我的草稿");
    await user.click(screen.getByRole("button", { name: "确认比较" }));
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));
    await waitFor(() =>
      expect(update).toHaveBeenLastCalledWith("p1", "t1", {
        expected_version: 4,
        title: "我的草稿",
      }),
    );
  });
  it("ignores a prior account's late mutation error and reloads the same project for the new account", async () => {
    let rejectOld!: (error: Error) => void;
    update.mockReturnValue(
      new Promise((_, reject) => {
        rejectOld = reject;
      }),
    );
    const { rerender } = renderPlan();
    await screen.findByText("写周报");
    chooseOption(
      within(rowFor("写周报")).getByRole("combobox", {
        name: "更改状态：写周报",
      }),
      "已完成",
    );
    currentUserId.value = 9;
    list.mockResolvedValue(
      listResponse([{ ...todoDoing, title: "新账号待办" }]),
    );
    rerender(<ProjectPlan projectId="p1" role="member" members={members} />);
    expect(await screen.findByText("新账号待办")).toBeVisible();
    rejectOld(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    await waitFor(() =>
      expect(screen.queryByText("项目不存在或你无权访问")).toBeNull(),
    );
    expect(screen.getByText("新账号待办")).toBeVisible();
  });
  it("opens the same detail id from table and board and restores trigger focus on Escape", async () => {
    const user = userEvent.setup();
    renderPlan("member");
    await screen.findByText("写周报");
    const tableTrigger = within(rowFor("写周报")).getByRole("button", {
      name: "查看待办：写周报",
    });
    await user.click(tableTrigger);
    expect(
      await screen.findByRole("dialog", { name: "待办详情" }),
    ).toBeVisible();
    expect(get).toHaveBeenLastCalledWith("p1", "t1");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "待办详情" })).toBeNull();
    expect(tableTrigger).toHaveFocus();

    fireEvent.click(screen.getByRole("radio", { name: "看板" }));
    await user.click(
      within(screen.getByTestId("todo-card-t1")).getByRole("button", {
        name: "查看待办：写周报",
      }),
    );
    expect(
      await screen.findByRole("dialog", { name: "待办详情" }),
    ).toBeVisible();
    expect(get).toHaveBeenLastCalledWith("p1", "t1");
  });
  it("renders the same todo ids in the table and the board from one response", async () => {
    renderPlan("owner");
    expect(await screen.findByText("写周报")).toBeInTheDocument();

    const rowKeys = Array.from(
      document.querySelectorAll("tr[data-row-key]"),
    ).map((row) => row.getAttribute("data-row-key"));
    expect(rowKeys.slice().sort()).toEqual(["t1", "t2", "t3"]);

    fireEvent.click(screen.getByRole("radio", { name: "看板" }));

    expect(screen.getByTestId("todo-card-t1")).toBeInTheDocument();
    expect(screen.getByTestId("todo-card-t2")).toBeInTheDocument();
    expect(screen.getByTestId("todo-card-t3")).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("radio", { name: "表格" }));
    expect(screen.getByText("写周报")).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(1);
  });

  it("sends search and filters to the server with offset reset to zero", async () => {
    const user = userEvent.setup();
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.type(screen.getByPlaceholderText("搜索待办标题"), "周报{enter}");
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ q: "周报", offset: 0, limit: 50 }),
      ),
    );

    chooseSelectOption("状态筛选", "进行中");
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({
          q: "周报",
          status: "in_progress",
          offset: 0,
        }),
      ),
    );

    chooseSelectOption("处理人筛选", "alice");
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({
          status: "in_progress",
          assigneeUserId: 1,
          offset: 0,
        }),
      ),
    );
  });

  it("loads more from the server offset only when has_more is true", async () => {
    const user = userEvent.setup();
    list.mockResolvedValue(listResponse([todoOpen], true));
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(await screen.findByRole("button", { name: "加载更多" }));
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ offset: 1, limit: 50 }),
      ),
    );
  });

  it("changes status with the current version and refreshes persisted server state", async () => {
    const user = userEvent.setup();
    update.mockResolvedValue({ ...todoOpen, status: "done", version: 4 });
    renderPlan("owner");
    await screen.findByText("写周报");

    chooseOption(
      within(rowFor("写周报")).getByRole("combobox", {
        name: "更改状态：写周报",
      }),
      "已完成",
    );
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        status: "done",
      }),
    );

    list.mockResolvedValue(
      listResponse(
        [
          {
            ...todoOpen,
            title: "写周报（服务器）",
            status: "done",
            version: 5,
          },
        ],
        false,
      ),
    );
    await user.click(screen.getByRole("button", { name: "刷新待办" }));

    expect(await screen.findByText("写周报（服务器）")).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith(
      "p1",
      expect.objectContaining({ offset: 0 }),
    );
  });

  it("requeries the server after a status change leaves the active filter", async () => {
    let persisted = false;
    list.mockImplementation((_projectId: string, params: { status?: string }) =>
      Promise.resolve(
        listResponse(
          params.status === "in_progress"
            ? persisted
              ? []
              : [todoDoing]
            : [todoOpen, todoDoing, todoOther],
        ),
      ),
    );
    update.mockImplementation(async () => {
      persisted = true;
      return { ...todoDoing, status: "done", version: 2 };
    });
    renderPlan("owner");
    await screen.findByText("修缺陷");
    chooseSelectOption("状态筛选", "进行中");
    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());

    chooseOption(
      within(rowFor("修缺陷")).getByRole("combobox", {
        name: "更改状态：修缺陷",
      }),
      "已完成",
    );
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(3));
    expect(screen.queryByText("修缺陷")).toBeNull();
  });

  it("edits only changed fields with the current version", async () => {
    const user = userEvent.setup();
    update.mockResolvedValue({ ...todoOpen, title: "写月报", version: 4 });
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑" }),
    );
    const titleInput = await screen.findByDisplayValue("写周报");
    await user.clear(titleInput);
    await user.type(titleInput, "写月报");
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        title: "写月报",
      }),
    );
  });

  it("retains Markdown format when the legacy editor changes a Markdown todo's description", async () => {
    const user = userEvent.setup();
    const markdownTodo = {
      ...todoOpen,
      description: "# 原版",
      description_format: "markdown" as const,
    };
    list.mockResolvedValue(listResponse([markdownTodo]));
    update.mockResolvedValue({
      ...markdownTodo,
      description: "# 更新版",
      version: 4,
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑" }),
    );
    const description = await screen.findByDisplayValue("# 原版");
    await user.clear(description);
    await user.type(description, "# 更新版");
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        description: "# 更新版",
        description_format: "markdown",
      }),
    );
  });

  it("explicitly keeps plain format when the legacy editor changes plain text", async () => {
    const user = userEvent.setup();
    update.mockResolvedValue({
      ...todoOpen,
      description: "新进展",
      version: 4,
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑" }),
    );
    const description = await screen.findByDisplayValue("本周进展");
    await user.clear(description);
    await user.type(description, "新进展");
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        description: "新进展",
        description_format: "plain",
      }),
    );
  });

  it("lets a manager reassign through the edit form", async () => {
    const user = userEvent.setup();
    update.mockResolvedValue({ ...todoOpen, assignee_user_id: 1, version: 4 });
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑" }),
    );
    await screen.findByDisplayValue("写周报");
    chooseSelectOption("处理人", "alice");
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        assignee_user_id: 1,
      }),
    );
  });

  it("preserves the creation draft after a failed POST and saves it on retry", async () => {
    const user = userEvent.setup();
    create.mockRejectedValueOnce(
      new Error("503 - service temporarily unavailable"),
    );
    create.mockResolvedValueOnce({
      ...todoOpen,
      todo_id: "t9",
      title: "新待办",
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(screen.getByRole("button", { name: "新建待办" }));
    const titleInput = await screen.findByPlaceholderText(
      "输入待办标题（1–200 字符）",
    );
    await user.type(titleInput, "新待办");
    await user.type(
      screen.getByPlaceholderText("可选：补充说明（不超过 4000 字符）"),
      "说明",
    );
    await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));

    expect(
      await screen.findByText("service temporarily unavailable"),
    ).toBeInTheDocument();
    expect(titleInput).toHaveValue("新待办");
    expect(create).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("p1", {
        title: "新待办",
        description: "说明",
        status: "todo",
      }),
    );
  });

  it("shows the conflict notice and reloads after a 409", async () => {
    update.mockRejectedValue(
      new Error(
        '409 - {"error":{"code":"INVITE_INVALID","details":{"reason":"version_conflict"}}}',
      ),
    );
    renderPlan("owner");
    await screen.findByText("写周报");

    chooseOption(
      within(rowFor("写周报")).getByRole("combobox", {
        name: "更改状态：写周报",
      }),
      "已完成",
    );

    expect(
      await screen.findByText("待办已被他人更新，已为你刷新最新内容，请重试。"),
    ).toBeInTheDocument();
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1));
    expect(screen.getByText("写周报")).toBeInTheDocument();
  });

  it("reloads assignment after the member list changes", async () => {
    list
      .mockResolvedValueOnce(listResponse())
      .mockResolvedValue(
        listResponse([{ ...todoOpen, assignee_user_id: null }]),
      );
    const { rerender } = renderPlan("owner");
    await screen.findByText("写周报");
    expect(within(rowFor("写周报")).getByText("bob")).toBeInTheDocument();

    rerender(
      <ProjectPlan
        projectId="p1"
        role="owner"
        members={members.filter((member) => member.user_id !== 2)}
      />,
    );
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(within(rowFor("写周报")).getByText("未指派")).toBeInTheDocument();
  });

  it("explains an invalid assignee without invite-link wording", async () => {
    const user = userEvent.setup();
    create.mockRejectedValue(
      new Error(
        '400 - {"error":{"code":"INVITE_INVALID","details":{"reason":"invalid_assignee"}}}',
      ),
    );
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建待办" }));
    await user.type(screen.getByRole("textbox", { name: "标题" }), "安排评审");
    await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));
    expect(
      await screen.findByText("处理人必须是当前项目成员。"),
    ).toBeInTheDocument();
  });

  it("keeps a 403 actionable as inline feedback", async () => {
    update.mockRejectedValue(
      new Error('403 - {"error":{"code":"FORBIDDEN","message":"forbidden"}}'),
    );
    renderPlan("owner");
    await screen.findByText("写周报");

    chooseOption(
      within(rowFor("写周报")).getByRole("combobox", {
        name: "更改状态：写周报",
      }),
      "已完成",
    );

    expect(await screen.findByText("forbidden")).toBeInTheDocument();
    expect(screen.getByText("写周报")).toBeInTheDocument();
  });

  it("clears stale todo data on 404 and recovers on retry", async () => {
    const user = userEvent.setup();
    list.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    renderPlan("owner");

    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(screen.queryByText("写周报")).toBeNull();

    list.mockResolvedValue(listResponse());
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("写周报")).toBeInTheDocument();
  });

  it("clears stale todo data when an action reports 404", async () => {
    update.mockRejectedValue(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    renderPlan("owner");
    await screen.findByText("写周报");

    chooseOption(
      within(rowFor("写周报")).getByRole("combobox", {
        name: "更改状态：写周报",
      }),
      "已完成",
    );

    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(screen.queryByText("写周报")).toBeNull();
  });

  it("hides manager-only bulk and assignment controls from a regular member", async () => {
    const user = userEvent.setup();
    renderPlan("member");
    await screen.findByText("写周报");

    expect(screen.queryByTestId("plan-bulk-toolbar")).toBeNull();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByRole("combobox", { name: "批量状态" })).toBeNull();

    // bob (id 2) is the assignee of t1, so only that row is editable.
    expect(
      within(rowFor("写周报")).getByRole("combobox", {
        name: "更改状态：写周报",
      }),
    ).toBeInTheDocument();
    expect(within(rowFor("alice 的待办")).queryByRole("combobox")).toBeNull();
    expect(
      within(rowFor("alice 的待办")).queryByRole("button", { name: "编辑" }),
    ).toBeNull();
    expect(
      within(rowFor("alice 的待办")).queryByRole("button", { name: "删除" }),
    ).toBeNull();

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑" }),
    );
    await screen.findByDisplayValue("写周报");
    expect(screen.queryByRole("combobox", { name: "处理人" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /^取\s*消$/ }));

    await user.click(screen.getByRole("button", { name: "新建待办" }));
    const assignee = await screen.findByRole("combobox", { name: "处理人" });
    fireEvent.mouseDown(assignee);
    expect(within(visibleDropdown()).getByText("bob")).toBeInTheDocument();
    expect(within(visibleDropdown()).queryByText("alice")).toBeNull();
  });

  it("bulk-updates unique selected ids and versions through the atomic endpoint", async () => {
    const user = userEvent.setup();
    bulk.mockResolvedValue({
      items: [
        { ...todoOpen, status: "done", version: 4 },
        { ...todoDoing, status: "done", version: 2 },
      ],
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    expect(screen.getByTestId("plan-bulk-toolbar")).toBeInTheDocument();
    fireEvent.click(within(rowFor("写周报")).getByRole("checkbox"));
    fireEvent.click(within(rowFor("修缺陷")).getByRole("checkbox"));
    await waitFor(() =>
      expect(screen.getByText("已选 2 项")).toBeInTheDocument(),
    );

    chooseSelectOption("批量状态", "已完成");
    await user.click(screen.getByRole("button", { name: "批量更新" }));

    await waitFor(() => expect(bulk).toHaveBeenCalledTimes(1));
    const call = bulk.mock.calls[0] as unknown as [
      string,
      {
        status: string;
        items: { todo_id: string; expected_version: number }[];
      },
    ];
    const projectArg = call[0];
    const body = call[1];
    expect(projectArg).toBe("p1");
    expect(body.status).toBe("done");
    const ids = body.items.map((item) => item.todo_id);
    const versions = body.items.map((item) => item.expected_version);
    expect(ids.slice().sort()).toEqual(["t1", "t2"]);
    expect(versions.slice().sort()).toEqual([1, 3]);
    expect(new Set(ids).size).toBe(ids.length);
    await waitFor(() =>
      expect(screen.getByText("已选 0 项")).toBeInTheDocument(),
    );
  });

  it("deletes with the current version only after confirmation", async () => {
    const user = userEvent.setup();
    remove.mockResolvedValue(undefined);
    list
      .mockResolvedValueOnce(listResponse())
      .mockResolvedValue(listResponse([todoDoing, todoOther]));
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "删除" }),
    );
    await user.click(await screen.findByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("p1", "t1", 3));
    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());
  });
});
