import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { catalogFixture } from "./todoCatalog.testFixtures";
import {
  makePlanQueryResponse,
  makePlanView,
} from "./plan/planView.testFixtures";
const { planViewsList, planViewsGet, planViewsQuery } = vi.hoisted(() => ({
  planViewsList: vi.fn(),
  planViewsGet: vi.fn(),
  planViewsQuery: vi.fn(),
}));
vi.mock("../../api/modules/projectPlanViews", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectPlanViews")
  >();
  return {
    ...actual,
    projectPlanViewsApi: {
      ...actual.projectPlanViewsApi,
      list: planViewsList,
      get: planViewsGet,
      query: planViewsQuery,
    },
  };
});
const { loadedDelivery } = vi.hoisted(() => ({
  loadedDelivery: {
    hold: false,
    latestScope: null as
      | import("./plan/ProjectPlanViews").PlanOperationScope
      | null,
    queued: [] as {
      scope: import("./plan/ProjectPlanViews").PlanOperationScope;
      deliver: () => void;
    }[],
  },
}));
vi.mock("./plan/ProjectPlanViews", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("./plan/ProjectPlanViews")
  >();
  const { forwardRef } = await import("react");
  const ActualProjectPlanViews = actual.default;
  return {
    ...actual,
    default: forwardRef<
      import("./plan/ProjectPlanViews").ProjectPlanViewsHandle,
      import("./plan/ProjectPlanViews").ProjectPlanViewsProps
    >((props, ref) => (
      <ActualProjectPlanViews
        {...props}
        ref={ref}
        onLoadedTodosChanged={(todos, scope) => {
          loadedDelivery.latestScope = scope;
          const deliver = () => props.onLoadedTodosChanged(todos, scope);
          if (loadedDelivery.hold)
            loadedDelivery.queued.push({ scope, deliver });
          else deliver();
        }}
      />
    )),
  };
});
vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  const resources = (await import("../../locales/zh.json")).default;
  const t = (
    key: string,
    fallback?: string | Record<string, unknown>,
    args?: Record<string, unknown>,
  ) => {
    let value: unknown = resources;
    for (const part of key.split(".")) {
      value =
        value && typeof value === "object"
          ? (value as Record<string, unknown>)[part]
          : undefined;
    }
    const options = typeof fallback === "object" ? fallback : args;
    const text =
      typeof value === "string"
        ? value
        : typeof fallback === "string"
        ? fallback
        : String(options?.defaultValue ?? key);
    return text.replace(/{{\s*([^}]+?)\s*}}/g, (_, name: string) =>
      String(options?.[name] ?? `{{${name}}}`),
    );
  };
  return {
    ...actual,
    useTranslation: () => ({ t, i18n: { language: "zh" } }),
  };
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
      // Each real HTTP response is decoded as a fresh catalog snapshot.
      get: async (
        ...args: Parameters<typeof actual.projectTodoCatalogApi.get>
      ) => structuredClone(await catalogGet(...args)),
      createPriority: catalogCreatePriority,
    },
  };
});

const {
  legacyList,
  planRows,
  projectRead,
  get,
  listComments,
  createComment,
  create,
  update,
  remove,
  bulk,
  listChildren,
  deleteTree,
} = vi.hoisted(() => ({
  legacyList: vi.fn(),
  planRows: vi.fn(),
  projectRead: vi.fn(),
  get: vi.fn(),
  listComments: vi.fn(),
  createComment: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  bulk: vi.fn(),
  listChildren: vi.fn(),
  deleteTree: vi.fn(),
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
      list: legacyList,
      create,
      get,
      listComments,
      createComment,
      update,
      remove,
      bulk,
      listChildren,
      deleteTree,
    },
  };
});

vi.mock("../../api/modules/projects", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projects")
  >();
  return {
    ...actual,
    projectsApi: { ...actual.projectsApi, get: projectRead },
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
import type {
  PlanQueryRequest,
  PlanView,
} from "../../api/modules/projectPlanViews";

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
  display_revision: 3,
  parent_todo_id: null,
  children_count: 0,
  done_children_count: 0,
  children_revision: 1,
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
  display_revision: 1,
  parent_todo_id: null,
  children_count: 0,
  done_children_count: 0,
  children_revision: 1,
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
  display_revision: 1,
  parent_todo_id: null,
  children_count: 0,
  done_children_count: 0,
  children_revision: 1,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

const members = [
  { user_id: 1, username: "alice", role: "owner" as const },
  { user_id: 2, username: "bob", role: "member" as const },
];

function planRowsResponse(
  items = [todoOpen, todoDoing, todoOther],
  nextCursor: string | null = null,
) {
  return {
    items,
    next_cursor: nextCursor,
    total: items.length,
    matched_total: items.length,
  };
}
let serverViews: PlanView[];
function viewById(viewId: string) {
  return serverViews.find((view) => view.view_id === viewId)!;
}
async function queryFixture(
  projectId: string,
  body: PlanQueryRequest,
  options?: RequestInit,
) {
  const source = (await planRows(projectId, body, options)) as ReturnType<
    typeof planRowsResponse
  >;
  const definition =
    body.override_definition ?? viewById(body.view_id).definition;
  const items = source.items.map((todo) => ({
    ...todo,
    catalog_revision: body.expected_catalog_revision,
  }));
  const groups =
    definition.group_by === "status"
      ? (["todo", "in_progress", "done"] as const).map((status) => ({
          key: { kind: "status" as const, id: status },
          count: items.filter((todo) => todo.status === status).length,
        }))
      : [];
  const groupItems =
    body.group_key?.kind === "status"
      ? items.filter((todo) => todo.status === body.group_key?.id)
      : items;
  return makePlanQueryResponse({
    ...source,
    items: definition.group_by !== null && !body.group_key ? [] : groupItems,
    groups,
    view_id: body.view_id,
    view_version: body.expected_view_version,
    catalog_revision: body.expected_catalog_revision,
    server_today: catalogFixture.server_today,
    server_timezone: catalogFixture.server_timezone,
    query_fingerprint: `synthetic-${JSON.stringify([
      body.view_id,
      body.expected_view_version,
      body.expected_catalog_revision,
      definition,
      body.window ?? null,
      body.group_key ?? null,
    ])}`,
  });
}
async function changeRowStatus(title: string, value: ProjectTodo["status"]) {
  fireEvent.click(
    within(rowFor(title)).getByRole("button", { name: "修改状态" }),
  );
  const dialog = await screen.findByRole("dialog", { name: "修改状态" });
  await waitFor(() => expect(dialog).toBeVisible());
  fireEvent.change(within(dialog).getByRole("combobox", { name: "状态" }), {
    target: { value },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "保存更改" }));
}
function cardFor(title: string): HTMLElement {
  return screen.getByRole("article", { name: title });
}

interface CandidatePlanAccessLoss {
  project_id: string;
  account_id: number;
  confirmed_by: "project_get";
  status: 403 | 404;
  error: unknown;
}

function renderPlan(
  role: "owner" | "member" = "owner",
  onProjectAccessLost?: (loss: CandidatePlanAccessLoss) => void,
) {
  const props = { projectId: "p1", role, members, onProjectAccessLost };
  return render(<ProjectPlan {...props} />);
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
  const row = within(screen.getByRole("table", { name: "全部待办" }))
    .getByRole("button", { name: `查看待办：${title}`, exact: true })
    .closest("tr");
  expect(row).toBeTruthy();
  return row as HTMLElement;
}

beforeEach(() => {
  localStorage.clear();
  loadedDelivery.hold = false;
  loadedDelivery.latestScope = null;
  loadedDelivery.queued = [];
  serverViews = [
    makePlanView({ view_id: "table-p1", project_id: "p1", name: "表格" }),
    makePlanView({
      view_id: "board-p1",
      project_id: "p1",
      type: "board",
      position: 1,
      name: "看板",
    }),
  ];
  planViewsList.mockReset().mockImplementation(async () => ({
    project_id: "p1",
    revision: 1,
    default_view_id: "table-p1",
    items: serverViews,
  }));
  planViewsGet
    .mockReset()
    .mockImplementation(async (_projectId: string, viewId: string) =>
      viewById(viewId),
    );
  planRows.mockReset().mockResolvedValue(planRowsResponse());
  planViewsQuery.mockReset().mockImplementation(queryFixture);
  legacyList.mockReset();
  projectRead.mockReset().mockResolvedValue({
    project_id: "p1",
    name: "Synthetic project",
    role: "owner",
  });
  catalogGet.mockReset().mockResolvedValue(catalogFixture);
  catalogCreatePriority.mockReset();
  get.mockReset().mockResolvedValue(todoOpen);
  listComments.mockReset().mockResolvedValue({ items: [], next_cursor: null });
  createComment.mockReset();
  create.mockReset();
  update.mockReset();
  remove.mockReset();
  bulk.mockReset();
  listChildren.mockReset();
  deleteTree.mockReset();
  currentUserId.value = 2;
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      private active = true;
      constructor(private callback: IntersectionObserverCallback) {}
      observe(target: Element) {
        queueMicrotask(() => {
          if (this.active && target.isConnected)
            this.callback(
              [{ target, isIntersecting: true } as IntersectionObserverEntry],
              this as unknown as IntersectionObserver,
            );
        });
      }
      unobserve() {}
      disconnect() {
        this.active = false;
      }
      takeRecords() {
        return [];
      }
    },
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("ProjectPlan table/board against the PS-04 contract", () => {
  it("D1 accepts every atomic bulk row before publishing its newer pair catalog", async () => {
    const user = userEvent.setup();
    const accepted = [todoOpen, todoDoing].map((todo) => ({
      ...todo,
      priority_id: "pr1",
      catalog_revision: 2,
    }));
    const written = accepted.map((todo) => ({
      ...todo,
      status: "done" as const,
      version: todo.version + 1,
      display_revision: todo.display_revision + 1,
      catalog_revision: 1,
    }));
    const fresh = written.map((todo) => ({ ...todo, catalog_revision: 3 }));
    let resolveSecond!: (todo: ProjectTodo) => void;
    const pending = new Promise<ProjectTodo>((yes) => {
      resolveSecond = yes;
    });
    catalogGet
      .mockReset()
      .mockResolvedValueOnce({ ...catalogFixture, revision: 2 })
      .mockResolvedValue({ ...catalogFixture, revision: 3 });
    planRows.mockResolvedValue(planRowsResponse(accepted));
    bulk.mockResolvedValue({ items: written });
    get
      .mockReset()
      .mockResolvedValueOnce(fresh[0])
      .mockReturnValueOnce(pending);
    renderPlan();
    await screen.findByText(accepted[0].title);
    for (const todo of accepted)
      fireEvent.click(within(rowFor(todo.title)).getByRole("checkbox"));
    fireEvent.change(screen.getByRole("combobox", { name: "批量状态" }), {
      target: { value: "done" },
    });
    await user.click(screen.getByRole("button", { name: "应用批量修改" }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    planRows.mockResolvedValue(planRowsResponse(fresh));
    await act(async () => {
      resolveSecond(fresh[1]);
    });
    await waitFor(() =>
      expect(screen.queryByText("已选择 2 条待办，最多 50 条")).toBeNull(),
    );
    for (const todo of fresh)
      await waitFor(() =>
        expect(within(rowFor(todo.title)).getByText("已完成")).toBeVisible(),
      );
    expect(bulk).toHaveBeenCalledWith("p1", {
      items: [
        { todo_id: "t1", expected_version: 3 },
        { todo_id: "t2", expected_version: 1 },
      ],
      status: "done",
    });
    expect(get).toHaveBeenCalledTimes(2);
    expect(catalogGet).toHaveBeenCalledTimes(2);
  });
  it("D1 publishes a bounded pair catalog into visible rows and reuses it for mutation refresh", async () => {
    const accepted = { ...todoOpen, priority_id: "pr1", catalog_revision: 2 };
    const written = {
      ...accepted,
      status: "done" as const,
      version: 4,
      display_revision: 4,
      catalog_revision: 1,
    };
    const fresh = { ...written, catalog_revision: 3 };
    let resolve!: (todo: ProjectTodo) => void;
    const pending = new Promise<ProjectTodo>((yes) => {
      resolve = yes;
    });
    const freshCatalog = {
      ...catalogFixture,
      revision: 3,
      priorities: catalogFixture.priorities.map((priority) =>
        priority.priority_id === "pr1"
          ? { ...priority, name: "今日紧急" }
          : priority,
      ),
    };
    catalogGet
      .mockReset()
      .mockResolvedValueOnce({ ...catalogFixture, revision: 2 })
      .mockResolvedValue(freshCatalog);
    planRows.mockResolvedValue(planRowsResponse([accepted]));
    update.mockResolvedValue(written);
    get.mockReturnValue(pending);
    renderPlan();
    await screen.findByText(accepted.title);
    expect(within(rowFor(accepted.title)).getByText("紧急")).toBeVisible();
    await changeRowStatus(accepted.title, "done");
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    expect(within(rowFor(accepted.title)).getByText("紧急")).toBeVisible();
    expect(screen.getByRole("dialog", { name: "修改状态" })).toBeVisible();
    planRows.mockResolvedValue(planRowsResponse([fresh]));
    await act(async () => {
      resolve(fresh);
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "修改状态" })).toBeNull(),
    );
    await waitFor(() =>
      expect(
        within(rowFor(accepted.title)).getByText("今日紧急"),
      ).toBeVisible(),
    );
    await waitFor(() =>
      expect(
        planViewsQuery.mock.calls.some(
          ([, body]) => body.expected_catalog_revision === 3,
        ),
      ).toBe(true),
    );
    expect(update).toHaveBeenCalledWith("p1", "t1", {
      expected_version: 3,
      status: "done",
    });
    expect(get).toHaveBeenCalledTimes(1);
    expect(catalogGet).toHaveBeenCalledTimes(2);
    expect(get.mock.calls[0][2].signal).toBe(
      catalogGet.mock.calls[1][1].signal,
    );
  });
  it("loads shared plan views and queries their fixed view and catalog versions", async () => {
    renderPlan();
    await waitFor(() => expect(planViewsList).toHaveBeenCalled());
    await waitFor(() =>
      expect(planViewsQuery).toHaveBeenCalledWith(
        "p1",
        expect.objectContaining({
          view_id: "table-p1",
          expected_view_version: 1,
          expected_catalog_revision: 1,
        }),
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(legacyList).not.toHaveBeenCalled();
  });
  it("retains an open catalog manager draft through a slow explicit refresh and discards the old query", async () => {
    const user = userEvent.setup();
    let resolveRows!: (value: ReturnType<typeof planRowsResponse>) => void;
    let resolveCatalog!: (value: typeof catalogFixture) => void;
    planRows.mockReturnValueOnce(
      new Promise<ReturnType<typeof planRowsResponse>>((resolve) => {
        resolveRows = resolve;
      }),
    );
    const latest = {
      ...catalogFixture,
      revision: 2,
      priorities: catalogFixture.priorities.map((item) =>
        item.priority_id === "pr1" ? { ...item, name: "服务器当前目录" } : item,
      ),
    };
    catalogCreatePriority.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"CONFLICT","details":{"reason":"catalog_revision_conflict"}}}',
      ),
    );
    renderPlan();
    await user.click(await screen.findByRole("button", { name: "新建待办" }));
    await user.click(screen.getByRole("button", { name: "选择优先级" }));
    await user.click(screen.getByRole("button", { name: "管理目录" }));
    const name = screen.getByRole("textbox", { name: "选项名称" });
    const manager = name.closest<HTMLElement>('[role="dialog"]')!;
    fireEvent.change(name, { target: { value: "延迟刷新中的草稿" } });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "purple",
    );
    await user.click(within(manager).getByRole("button", { name: "新增选项" }));
    await screen.findByText("目录已被修改，草稿已保留，请刷新后比较再保存。");
    catalogGet
      .mockReturnValueOnce(
        new Promise<typeof catalogFixture>((resolve) => {
          resolveCatalog = resolve;
        }),
      )
      .mockResolvedValue(latest);
    await user.click(
      within(manager).getByRole("button", { name: "刷新后比较" }),
    );
    await waitFor(() => expect(catalogGet).toHaveBeenCalledTimes(2));
    await act(async () => {
      resolveRows(planRowsResponse([{ ...todoOpen, priority_id: "pr1" }]));
    });
    expect(manager).toBeVisible();
    expect(within(manager).getByRole("textbox", { name: "选项名称" })).toBe(
      name,
    );
    expect(name).toHaveValue("延迟刷新中的草稿");
    expect(within(manager).getByRole("combobox", { name: "颜色" })).toHaveValue(
      "purple",
    );
    expect(
      within(manager).getByRole("button", { name: "新增选项" }),
    ).toBeDisabled();
    await act(async () => {
      resolveCatalog(latest);
    });
    expect(await within(manager).findByText("服务器当前值")).toBeVisible();
    expect(name).toHaveValue("延迟刷新中的草稿");
    await user.click(within(manager).getByRole("button", { name: "确认比较" }));
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
    await user.click(within(manager).getByRole("button", { name: "新增选项" }));
    await waitFor(() =>
      expect(catalogCreatePriority).toHaveBeenLastCalledWith("p1", {
        expected_revision: 2,
        name: item.name,
        color: "purple",
      }),
    );
    expect(legacyList).not.toHaveBeenCalled();
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
  it("refreshes the complete catalog snapshot before displaying persisted names in table and board", async () => {
    const user = userEvent.setup();
    planRows.mockResolvedValue(
      planRowsResponse([
        { ...todoOpen, priority_id: "pr1", tag_ids: ["tag1"] },
      ]),
    );
    renderPlan();
    expect(await screen.findByText("紧急")).toBeVisible();
    catalogGet.mockResolvedValue({
      ...catalogFixture,
      revision: 2,
      priorities: [{ ...catalogFixture.priorities[0], name: "真实新名字" }],
    });
    await user.click(screen.getByRole("button", { name: /^刷\s*新$/ }));
    expect(await screen.findByText("真实新名字")).toBeVisible();
    expect(screen.queryByText("紧急")).toBeNull();
    await user.click(screen.getByRole("button", { name: "选择视图 看板" }));
    const card = await screen.findByRole("article", { name: "写周报" });
    expect(within(card).getByText("真实新名字")).toBeVisible();
    expect(planViewsQuery).toHaveBeenLastCalledWith(
      "p1",
      expect.objectContaining({ expected_catalog_revision: 2 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(legacyList).not.toHaveBeenCalled();
  });

  it("sends explicit date null to clear but omits an unchanged overdue date on other edits", async () => {
    const user = userEvent.setup();
    const overdue = { ...todoOpen, due_date: "2020-01-01" };
    const other = {
      ...todoDoing,
      todo_id: "past-two",
      title: "另一条逾期待办",
      due_date: "2020-02-01",
      version: 7,
      display_revision: 7,
    };
    planRows.mockResolvedValue(planRowsResponse([overdue, other]));
    update
      .mockResolvedValueOnce({
        ...overdue,
        due_date: null,
        version: 4,
        display_revision: 4,
      })
      .mockResolvedValue({
        ...other,
        title: "改标题保留逾期",
        version: 8,
        display_revision: 8,
      });
    renderPlan();
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑待办" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "清空截止日期" }));
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        due_date: null,
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "编辑待办" })).toBeNull(),
    );
    await user.click(
      within(rowFor("另一条逾期待办")).getByRole("button", {
        name: "编辑待办",
      }),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "标题" }), {
      target: { value: "改标题保留逾期" },
    });
    await user.click(screen.getByRole("button", { name: /^保\s*存$/ }));
    await waitFor(() =>
      expect(update).toHaveBeenLastCalledWith("p1", "past-two", {
        expected_version: 7,
        title: "改标题保留逾期",
      }),
    );
  });

  it.each([
    { label: "unavailable catalog", message: "503" },
    {
      label: "500 with not-found text",
      message:
        'Request failed: 500 Internal Server Error - {"detail":"upstream not found"}',
    },
    {
      label: "422 with a not-found error code",
      message:
        'Request failed: 422 Unprocessable Entity - {"error":{"code":"NOT_FOUND","message":"tag not found"}}',
    },
    {
      label: "503 with a 404 diagnostic",
      message:
        'Request failed: 503 Service Unavailable - {"detail":"upstream returned 404"}',
    },
  ])(
    "preserves date drafts after a catalog refresh failure: $label",
    async ({ message }) => {
      const user = userEvent.setup();
      const accessLost = vi.fn();
      renderPlan("owner", accessLost);
      await screen.findByText("写周报");
      await user.click(screen.getByRole("button", { name: "新建待办" }));
      fireEvent.change(screen.getByRole("textbox", { name: "标题" }), {
        target: { value: "日期草稿" },
      });
      fireEvent.change(screen.getByRole("textbox", { name: "描述" }), {
        target: { value: "尚未提交的工作安排" },
      });
      fireEvent.change(screen.getByLabelText("截止日期"), {
        target: { value: "2026-09-28" },
      });
      create.mockRejectedValue(
        new Error(
          '422 - {"error":{"code":"VALIDATION_ERROR","details":{"reason":"invalid_dates"}}}',
        ),
      );
      await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));
      await screen.findByRole("button", { name: "刷新后比较" });
      catalogGet.mockRejectedValue(new Error(message));
      await user.click(screen.getByRole("button", { name: "刷新后比较" }));
      expect(
        await screen.findByText("服务器日期加载失败，重试后才能编辑日期。"),
      ).toBeVisible();
      expect(screen.getByRole("dialog", { name: "新建待办" })).toBeVisible();
      expect(screen.getByRole("textbox", { name: "标题" })).toHaveValue(
        "日期草稿",
      );
      expect(screen.getByRole("textbox", { name: "描述" })).toHaveValue(
        "尚未提交的工作安排",
      );
      expect(screen.getByLabelText("截止日期")).toHaveValue("2026-09-28");
      expect(screen.getByLabelText("截止日期")).toBeDisabled();
      expect(screen.getByRole("button", { name: /^创\s*建$/ })).toBeDisabled();
      expect(create).toHaveBeenCalledTimes(1);
      expect(projectRead).not.toHaveBeenCalled();
      expect(accessLost).not.toHaveBeenCalled();
    },
  );

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
      within(rowFor("写周报")).getByRole("button", { name: "编辑待办" }),
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
  it("disables creation until the title contains non-whitespace text", async () => {
    const user = userEvent.setup();
    renderPlan();
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建待办" }));
    const dialog = screen.getByRole("dialog", { name: "新建待办" });
    const title = within(dialog).getByRole("textbox", { name: "标题" });
    const createButton = within(dialog).getByRole("button", {
      name: /^创\s*建$/,
    });

    expect(createButton).toBeDisabled();
    await user.type(title, "   ");
    expect(createButton).toBeDisabled();
    await user.type(title, "计划发布");
    expect(createButton).toBeEnabled();
    await user.clear(title);
    expect(createButton).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
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
  it("retains creation comparison after catalog conflict refresh finishes in the same view and filter", async () => {
    const user = userEvent.setup();
    const draft = {
      title: "目录冲突待办草稿",
      description: "刷新比较前保留完整草稿",
      status: "in_progress" as const,
      assignee_user_id: 1,
      start_date: "2020-01-01",
      due_date: "2026-09-30",
      priority_id: "pr1",
      tag_ids: ["tag1"],
    };
    const latest = {
      ...catalogFixture,
      revision: 2,
      priorities: catalogFixture.priorities.map((item) =>
        item.priority_id === "pr1" ? { ...item, name: "刷新后的紧急" } : item,
      ),
      tags: catalogFixture.tags.map((item) =>
        item.tag_id === "tag1" ? { ...item, name: "刷新后的设计" } : item,
      ),
    };
    create
      .mockRejectedValueOnce(
        new Error(
          '409 - {"error":{"code":"CONFLICT","details":{"reason":"catalog_revision_conflict"}}}',
        ),
      )
      .mockResolvedValueOnce({
        ...todoOpen,
        ...draft,
        todo_id: "new-catalog-conflict",
        catalog_revision: 2,
      });
    renderPlan();
    await screen.findByText("写周报");
    await user.type(
      screen.getByRole("textbox", { name: "搜索待办" }),
      "周报{enter}",
    );
    await waitFor(() =>
      expect(
        (planViewsQuery.mock.calls.at(-1)![1] as PlanQueryRequest)
          .override_definition!.filters,
      ).toContainEqual({ field: "title", op: "contains", value: "周报" }),
    );
    const originalQuery = structuredClone(
      planViewsQuery.mock.calls.at(-1)![1] as PlanQueryRequest,
    );
    await user.click(screen.getByRole("button", { name: "新建待办" }));
    const dialog = screen.getByRole("dialog", { name: "新建待办" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "标题" }), {
      target: { value: draft.title },
    });
    fireEvent.change(
      within(dialog).getByPlaceholderText("可选：补充说明（不超过 4000 字符）"),
      { target: { value: draft.description } },
    );
    chooseOption(
      within(dialog).getByRole("combobox", { name: "状态" }),
      "进行中",
    );
    chooseOption(
      within(dialog).getByRole("combobox", { name: "处理人" }),
      "alice",
    );
    fireEvent.change(within(dialog).getByLabelText("开始日期"), {
      target: { value: draft.start_date },
    });
    fireEvent.change(within(dialog).getByLabelText("截止日期"), {
      target: { value: draft.due_date },
    });
    await user.click(
      within(dialog).getByRole("button", { name: "选择优先级" }),
    );
    await user.click(screen.getByRole("button", { name: "紧急" }));
    await user.click(within(dialog).getByRole("button", { name: "选择标签" }));
    await user.click(screen.getByRole("checkbox", { name: "设计" }));
    await user.click(screen.getByRole("button", { name: "完成选择" }));
    await user.click(within(dialog).getByRole("button", { name: /^创\s*建$/ }));
    await within(dialog).findByRole("button", { name: "刷新后比较" });
    expect(create).toHaveBeenCalledExactlyOnceWith("p1", {
      ...draft,
      expected_catalog_revision: 1,
    });

    const originalScope = loadedDelivery.latestScope!;
    loadedDelivery.hold = true;
    catalogGet.mockResolvedValue(latest);
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    await waitFor(() =>
      expect(planViewsQuery).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({
          view_id: originalQuery.view_id,
          expected_view_version: originalQuery.expected_view_version,
          expected_catalog_revision: 2,
          override_definition: originalQuery.override_definition,
        }),
        expect.anything(),
      ),
    );
    expect(
      within(dialog).getByRole("button", { name: /创\s*建$/ }),
    ).toBeDisabled();
    // Keep real view/query generations and forward the real ref. Delay only
    // callback delivery to reproduce Chrome's effect arriving after compare.
    await act(async () => {
      await Promise.all(
        planViewsQuery.mock.results
          .filter(
            (_result, index) =>
              (planViewsQuery.mock.calls[index][1] as PlanQueryRequest)
                .expected_catalog_revision === 2,
          )
          .map((result) => result.value),
      );
    });
    await within(dialog).findByRole("button", { name: "确认比较" });
    await waitFor(() =>
      expect(loadedDelivery.queued.length).toBeGreaterThan(0),
    );
    const lateLoaded = loadedDelivery.queued.at(-1)!;
    expect(lateLoaded.scope).toMatchObject({
      accountId: originalScope.accountId,
      projectId: originalScope.projectId,
      viewId: originalScope.viewId,
    });
    expect(lateLoaded.scope.queryGeneration).toBeGreaterThan(
      originalScope.queryGeneration,
    );
    await act(async () => {
      loadedDelivery.hold = false;
      loadedDelivery.queued = [];
      lateLoaded.deliver();
    });
    const currentDialog = screen.getByRole("dialog", { name: "新建待办" });
    expect(
      within(currentDialog).getByRole("textbox", { name: "标题" }),
    ).toHaveValue(draft.title);
    expect(
      within(currentDialog).getByPlaceholderText(
        "可选：补充说明（不超过 4000 字符）",
      ),
    ).toHaveValue(draft.description);
    expect(within(currentDialog).getByLabelText("开始日期")).toHaveValue(
      draft.start_date,
    );
    expect(within(currentDialog).getByLabelText("截止日期")).toHaveValue(
      draft.due_date,
    );
    expect(
      within(currentDialog)
        .getByRole("combobox", { name: "状态" })
        .closest(".ant-select"),
    ).toHaveTextContent("进行中");
    expect(
      within(currentDialog)
        .getByRole("combobox", { name: "处理人" })
        .closest(".ant-select"),
    ).toHaveTextContent("alice");
    expect(
      within(currentDialog).getByRole("button", { name: "选择优先级" }),
    ).toHaveTextContent("刷新后的紧急");
    expect(
      within(currentDialog).getByRole("button", { name: "选择标签" }),
    ).toHaveTextContent("刷新后的设计");
    const createButton = within(currentDialog).getByRole("button", {
      name: /^创\s*建$/,
    });
    expect(createButton).toBeDisabled();
    await user.click(createButton);
    expect(create).toHaveBeenCalledTimes(1);
    await user.click(
      await within(currentDialog).findByRole("button", { name: "确认比较" }),
    );
    await user.click(createButton);
    await waitFor(() =>
      expect(create).toHaveBeenLastCalledWith("p1", {
        ...draft,
        expected_catalog_revision: 2,
      }),
    );
    expect(create).toHaveBeenCalledTimes(2);
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "新建待办" })).toBeNull(),
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
      .mockResolvedValue({
        ...todoOpen,
        title: "我的草稿",
        version: 5,
        display_revision: 5,
      });
    get.mockResolvedValue({
      ...todoOpen,
      title: "服务端更新",
      version: 4,
      display_revision: 4,
    });
    renderPlan();
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑待办" }),
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
    expect(
      await within(screen.getByRole("dialog", { name: "编辑待办" })).findByText(
        "服务端更新",
      ),
    ).toBeVisible();
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
    await changeRowStatus("写周报", "done");
    currentUserId.value = 9;
    planRows.mockResolvedValue(
      planRowsResponse([{ ...todoDoing, title: "新账号待办" }]),
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
    function RoutedPlan() {
      const [selectedTodoId, setSelectedTodoId] = useState<string | null>(null);
      return (
        <>
          <button role="tab" aria-selected="true">
            计划
          </button>
          <ProjectPlan
            projectId="p1"
            role="member"
            members={members}
            selectedTodoId={selectedTodoId}
            onOpenTodo={setSelectedTodoId}
            onCloseTodo={() => setSelectedTodoId(null)}
          />
        </>
      );
    }
    render(<RoutedPlan />);
    await screen.findByText("写周报");
    const tableTrigger = within(rowFor("写周报")).getByRole("button", {
      name: "查看待办：写周报",
    });
    await user.click(tableTrigger);
    expect(
      await screen.findByRole("dialog", { name: "待办详情" }),
    ).toBeVisible();
    expect(get).toHaveBeenLastCalledWith("p1", "t1");
    await user.click(
      screen.getByRole("textbox", { name: "评论", exact: true }),
    );
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "待办详情" })).toBeNull();
    await waitFor(() => expect(tableTrigger).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: "选择视图 看板" }));
    await screen.findByRole("article", { name: "写周报" });
    const boardTrigger = within(cardFor("写周报")).getByRole("button", {
      name: "查看待办：写周报",
    });
    await user.click(boardTrigger);
    expect(
      await screen.findByRole("dialog", { name: "待办详情" }),
    ).toBeVisible();
    expect(get).toHaveBeenLastCalledWith("p1", "t1");
    await user.click(
      screen.getByRole("textbox", { name: "评论", exact: true }),
    );
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "待办详情" })).toBeNull();
    await waitFor(() => expect(boardTrigger).toHaveFocus());
  });
  it("renders the same todo ids in the table and the board from one response", async () => {
    renderPlan("owner");
    expect(await screen.findByText("写周报")).toBeInTheDocument();

    const rowKeys = Array.from(
      document.querySelectorAll("[data-plan-renderer=table] tbody tr"),
    ).map(
      (row) =>
        within(row as HTMLElement)
          .getByRole("button", { name: /查看待办/ })
          .getAttribute("aria-label")
          ?.replace("查看待办：", ""),
    );
    expect(rowKeys.slice().sort()).toEqual(
      ["alice 的待办", "修缺陷", "写周报"].sort(),
    );

    fireEvent.click(screen.getByRole("button", { name: "选择视图 看板" }));
    await screen.findByRole("article", { name: "写周报" });

    expect(cardFor("写周报")).toBeInTheDocument();
    expect(cardFor("修缺陷")).toBeInTheDocument();
    expect(cardFor("alice 的待办")).toBeInTheDocument();
    expect(legacyList).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "选择视图 表格" }));
    await screen.findByText("写周报");
    expect(screen.getByText("写周报")).toBeInTheDocument();
    expect(legacyList).not.toHaveBeenCalled();
  });

  it("sends complete temporary search and filters to the server with the cursor reset", async () => {
    const user = userEvent.setup();
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.type(
      screen.getByRole("textbox", { name: "搜索待办" }),
      "周报{enter}",
    );
    const filtersOfLast = () =>
      (planViewsQuery.mock.calls.at(-1)![1] as PlanQueryRequest)
        .override_definition!.filters;
    await waitFor(() =>
      expect(filtersOfLast()).toContainEqual({
        field: "title",
        op: "contains",
        value: "周报",
      }),
    );
    chooseSelectOption("筛选状态", "进行中");
    await waitFor(() =>
      expect(filtersOfLast()).toEqual(
        expect.arrayContaining([
          { field: "title", op: "contains", value: "周报" },
          { field: "status", op: "in", values: ["in_progress"] },
        ]),
      ),
    );
    chooseSelectOption("筛选处理人", "alice");
    await waitFor(() =>
      expect(filtersOfLast()).toContainEqual({
        field: "assignee",
        op: "in",
        values: [1],
      }),
    );
    const last = planViewsQuery.mock.calls.at(-1)![1] as PlanQueryRequest;
    expect(last).not.toHaveProperty("cursor");
    expect(last).not.toHaveProperty("offset");
    expect(last).toMatchObject({
      view_id: "table-p1",
      expected_view_version: 1,
      expected_catalog_revision: 1,
      limit: 50,
    });
    expect(last.override_definition).toMatchObject({
      schema_version: 1,
      group_by: null,
      sort: [{ field: "updated_at", direction: "desc" }],
    });
    expect(legacyList).not.toHaveBeenCalled();
  });

  it("loads more with the original opaque server cursor and independently merges the next page", async () => {
    const user = userEvent.setup();
    planRows
      .mockResolvedValueOnce({
        ...planRowsResponse([todoOpen], "opaque-next"),
        total: 2,
        matched_total: 2,
      })
      .mockResolvedValue({
        ...planRowsResponse([todoDoing]),
        total: 2,
        matched_total: 2,
      });
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.click(await screen.findByRole("button", { name: "加载更多" }));
    await waitFor(() =>
      expect(planViewsQuery).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({
          view_id: "table-p1",
          expected_view_version: 1,
          expected_catalog_revision: 1,
          cursor: "opaque-next",
          limit: 50,
        }),
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(await screen.findByText("修缺陷")).toBeVisible();
    expect(screen.getByText("写周报")).toBeVisible();
    expect(screen.queryByRole("button", { name: "加载更多" })).toBeNull();
    expect(legacyList).not.toHaveBeenCalled();
  });

  it("changes status with the current version and refreshes persisted server state", async () => {
    const user = userEvent.setup();
    update.mockResolvedValue({
      ...todoOpen,
      status: "done",
      version: 4,
      display_revision: 4,
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    await changeRowStatus("写周报", "done");
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        status: "done",
      }),
    );

    planRows.mockResolvedValue(
      planRowsResponse(
        [
          {
            ...todoOpen,
            title: "写周报（服务器）",
            status: "done",
            version: 5,
            display_revision: 5,
          },
        ],
        null,
      ),
    );
    await user.click(screen.getByRole("button", { name: /^刷\s*新$/ }));

    expect(await screen.findByText("写周报（服务器）")).toBeInTheDocument();
    expect(
      planViewsQuery.mock.calls.at(-1)![1] as PlanQueryRequest,
    ).not.toHaveProperty("cursor");
  });

  it("requeries the server after a status change leaves the active filter", async () => {
    let persisted = false;
    planRows.mockImplementation((_projectId: string, body: PlanQueryRequest) =>
      Promise.resolve(
        planRowsResponse(
          body.override_definition?.filters.some(
            (filter) =>
              filter.field === "status" &&
              filter.op === "in" &&
              filter.values.includes("in_progress"),
          )
            ? persisted
              ? []
              : [todoDoing]
            : [todoOpen, todoDoing, todoOther],
        ),
      ),
    );
    update.mockImplementation(async () => {
      persisted = true;
      return { ...todoDoing, status: "done", version: 2, display_revision: 2 };
    });
    renderPlan("owner");
    await screen.findByText("修缺陷");
    chooseSelectOption("筛选状态", "进行中");
    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());
    await changeRowStatus("修缺陷", "done");
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText("修缺陷")).toBeNull());
    expect(planViewsQuery.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(
      (planViewsQuery.mock.calls.at(-1)![1] as PlanQueryRequest)
        .override_definition!.filters,
    ).toContainEqual({ field: "status", op: "in", values: ["in_progress"] });
  });

  it("edits only changed fields with the current version", async () => {
    const user = userEvent.setup();
    update.mockResolvedValue({
      ...todoOpen,
      title: "写月报",
      version: 4,
      display_revision: 4,
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑待办" }),
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
    planRows.mockResolvedValue(planRowsResponse([markdownTodo]));
    update.mockResolvedValue({
      ...markdownTodo,
      description: "# 更新版",
      version: 4,
      display_revision: 4,
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑待办" }),
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
      display_revision: 4,
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑待办" }),
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
    update.mockResolvedValue({
      ...todoOpen,
      assignee_user_id: 1,
      version: 4,
      display_revision: 4,
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑待办" }),
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

    expect(await screen.findByText("创建待办失败")).toBeInTheDocument();
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

  it("shows a retained conflict proposal and refreshes explicitly after a 409", async () => {
    const user = userEvent.setup();
    update
      .mockRejectedValueOnce(
        new Error(
          '409 - {"error":{"code":"CONFLICT","details":{"reason":"version_conflict"}}}',
        ),
      )
      .mockResolvedValue({
        ...todoOpen,
        status: "done",
        version: 5,
        display_revision: 5,
      });
    get.mockResolvedValue({ ...todoOpen, version: 4, display_revision: 4 });
    renderPlan("owner");
    await screen.findByText("写周报");
    await changeRowStatus("写周报", "done");
    const dialog = screen.getByRole("dialog", { name: "修改状态" });
    expect(
      await within(dialog).findByText(
        "待办或目录已被修改，草稿已保留，请刷新后比较。",
      ),
    ).toBeVisible();
    expect(within(dialog).getByRole("combobox", { name: "状态" })).toHaveValue(
      "done",
    );
    const beforeRefresh = planViewsQuery.mock.calls.length;
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    await within(dialog).findByText("当前服务器值");
    expect(planViewsQuery.mock.calls.length).toBeGreaterThan(beforeRefresh);
    expect(update).toHaveBeenCalledTimes(1);
    await user.click(
      within(dialog).getByRole("button", { name: "确认采用当前值继续编辑" }),
    );
    await user.click(within(dialog).getByRole("button", { name: "保存更改" }));
    await waitFor(() =>
      expect(update).toHaveBeenLastCalledWith("p1", "t1", {
        expected_version: 4,
        status: "done",
      }),
    );
  });

  it("reloads assignment after the member list changes", async () => {
    planRows.mockResolvedValueOnce(planRowsResponse()).mockResolvedValue(
      planRowsResponse([
        {
          ...todoOpen,
          assignee_user_id: null,
          version: 4,
          display_revision: 4,
        },
      ]),
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
    await waitFor(() => expect(planRows).toHaveBeenCalledTimes(2));
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

  it.each([403, 404] as const)(
    "notifies parent only after mutation 404 is confirmed by project GET %i",
    async (status) => {
      const loss = vi.fn();
      const error = new Error(
        `${status} - {"error":{"code":"${
          status === 404 ? "NOT_FOUND" : "FORBIDDEN"
        }"}}`,
      );
      update.mockRejectedValue(
        new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
      );
      projectRead.mockRejectedValue(error);
      renderPlan("owner", loss);
      await screen.findByText("写周报");
      await changeRowStatus("写周报", "done");
      await waitFor(() => expect(loss).toHaveBeenCalledTimes(1));
      expect(projectRead).toHaveBeenCalledWith("p1");
      expect(loss).toHaveBeenCalledWith({
        project_id: "p1",
        account_id: 2,
        confirmed_by: "project_get",
        status,
        error,
      });
      expect(screen.queryByText("写周报")).toBeNull();
    },
  );

  it("notifies parent after Plan query loss receives a confirmed project GET 404", async () => {
    const loss = vi.fn();
    const error = new Error('404 - {"error":{"code":"NOT_FOUND"}}');
    planRows.mockRejectedValue(error);
    projectRead.mockRejectedValue(error);
    renderPlan("owner", loss);
    expect(await screen.findByText("项目不存在或你无权访问")).toBeVisible();
    await waitFor(() => expect(loss).toHaveBeenCalledTimes(1));
    expect(projectRead).toHaveBeenCalledWith("p1");
    expect(loss).toHaveBeenCalledWith({
      project_id: "p1",
      account_id: 2,
      confirmed_by: "project_get",
      status: 404,
      error,
    });
  });

  const nonAccessProjectErrors = [
    {
      name: "500 text",
      error:
        'Request failed: 500 Internal Server Error - {"detail":"upstream not found"}',
    },
    {
      name: "500 code",
      error:
        'Request failed: 500 Internal Server Error - {"error":{"code":"NOT_FOUND"}}',
    },
    {
      name: "401 text",
      error:
        'Request failed: 401 Unauthorized - {"detail":"session not found"}',
    },
    { name: "network text", error: "NetworkError: project route not found" },
    {
      name: "422 text",
      error:
        'Request failed: 422 Unprocessable Content - {"detail":"key not found"}',
    },
  ];

  it.each(nonAccessProjectErrors)(
    "keeps non-access project GET errors from clearing the parent after catalog loss: $name",
    async ({ error }) => {
      const loss = vi.fn();
      catalogGet.mockRejectedValue(
        new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
      );
      projectRead.mockRejectedValue(new Error(error));
      renderPlan("owner", loss);
      expect(await screen.findByText("项目不存在或你无权访问")).toBeVisible();
      await waitFor(() => expect(projectRead).toHaveBeenCalledWith("p1"));
      await act(async () => {
        await Promise.resolve();
      });
      expect(loss).not.toHaveBeenCalled();
    },
  );

  it.each(nonAccessProjectErrors)(
    "keeps non-access project GET errors from clearing the parent after mutation 404: $name",
    async ({ error }) => {
      const loss = vi.fn();
      update.mockRejectedValue(
        new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
      );
      projectRead.mockRejectedValue(new Error(error));
      renderPlan("owner", loss);
      await screen.findByText("写周报");
      await changeRowStatus("写周报", "done");
      const dialog = screen.getByRole("dialog", { name: "修改状态" });
      expect(await within(dialog).findByText("待办操作失败")).toBeVisible();
      expect(
        within(dialog).getByRole("combobox", { name: "状态" }),
      ).toHaveValue("done");
      expect(screen.getByRole("table", { name: "全部待办" })).toBeVisible();
      expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
      expect(loss).not.toHaveBeenCalled();
    },
  );

  it("keeps a 403 actionable as inline feedback and retains legal project content", async () => {
    const loss = vi.fn();
    update.mockRejectedValue(
      new Error('403 - {"error":{"code":"FORBIDDEN","message":"forbidden"}}'),
    );
    renderPlan("owner", loss);
    await screen.findByText("写周报");
    await changeRowStatus("写周报", "done");
    const dialog = screen.getByRole("dialog", { name: "修改状态" });
    expect(
      await within(dialog).findByText(
        "你目前无权修改这条待办。现有内容已保留。",
      ),
    ).toBeVisible();
    expect(within(dialog).getByRole("combobox", { name: "状态" })).toHaveValue(
      "done",
    );
    expect(
      within(rowFor("写周报")).getByRole("button", {
        name: "查看待办：写周报",
      }),
    ).toBeVisible();
    expect(loss).not.toHaveBeenCalled();
  });

  it("clears stale todo data on 404 and recovers on retry", async () => {
    const user = userEvent.setup();
    planRows.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    projectRead.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    renderPlan("owner");

    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(screen.queryByText("写周报")).toBeNull();

    planRows.mockResolvedValue(planRowsResponse());
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("写周报")).toBeInTheDocument();
  });

  it("clears stale todo data when an action reports 404", async () => {
    update.mockRejectedValue(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    projectRead.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    renderPlan("owner");
    await screen.findByText("写周报");

    await changeRowStatus("写周报", "done");

    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(screen.queryByText("写周报")).toBeNull();
  });

  it("preserves the legal project after a single todo mutation 404", async () => {
    const loss = vi.fn();
    update.mockImplementation(async () => {
      planRows.mockResolvedValue(planRowsResponse([todoDoing, todoOther]));
      throw new Error('404 - {"error":{"code":"NOT_FOUND"}}');
    });
    renderPlan("owner", loss);
    await screen.findByText("写周报");
    await changeRowStatus("写周报", "done");
    await waitFor(() => expect(projectRead).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());
    expect(screen.getByRole("table", { name: "全部待办" })).toBeVisible();
    expect(screen.getByText("修缺陷")).toBeVisible();
    expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
    expect(
      screen.getAllByText("待办不存在或你无权访问").length,
    ).toBeGreaterThan(0);
    expect(loss).not.toHaveBeenCalled();
  });

  it("preserves the legal project after a todo delete 404", async () => {
    const user = userEvent.setup();
    remove.mockImplementation(async () => {
      planRows.mockResolvedValue(planRowsResponse([todoDoing, todoOther]));
      throw new Error('404 - {"error":{"code":"NOT_FOUND"}}');
    });
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "删除待办" }),
    );
    await user.click(await screen.findByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(projectRead).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());
    expect(screen.getByText("修缺陷")).toBeVisible();
    expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
  });

  it("closes only the missing todo editor after 404 in a readable project", async () => {
    const user = userEvent.setup();
    update.mockImplementation(async () => {
      planRows.mockResolvedValue(planRowsResponse([todoDoing, todoOther]));
      throw new Error('404 - {"error":{"code":"NOT_FOUND"}}');
    });
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑待办" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "编辑待办" });
    await user.type(
      within(dialog).getByRole("textbox", { name: "标题" }),
      "修订",
    );
    await user.click(within(dialog).getByRole("button", { name: /^保\s*存$/ }));
    await waitFor(() => expect(projectRead).toHaveBeenCalledWith("p1"));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "编辑待办" })).toBeNull(),
    );
    expect(screen.getByText("修缺陷")).toBeVisible();
    expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
  });

  it("closes only the missing todo detail after 404 in a readable project", async () => {
    const user = userEvent.setup();
    get.mockImplementation(async () => {
      planRows.mockResolvedValue(planRowsResponse([todoDoing, todoOther]));
      throw new Error('404 - {"error":{"code":"NOT_FOUND"}}');
    });
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", {
        name: "查看待办：写周报",
      }),
    );
    await waitFor(() => expect(projectRead).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());
    expect(screen.getByText("修缺陷")).toBeVisible();
    expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
  });

  it("retains the creation draft after POST 404 when the project is readable", async () => {
    const user = userEvent.setup();
    create.mockRejectedValue(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.click(screen.getByRole("button", { name: "新建待办" }));
    const dialog = await screen.findByRole("dialog", { name: "新建待办" });
    const title = within(dialog).getByRole("textbox", { name: "标题" });
    await user.type(title, "保留的创建草稿");
    await user.click(within(dialog).getByRole("button", { name: /^创\s*建$/ }));
    await waitFor(() => expect(projectRead).toHaveBeenCalledWith("p1"));
    expect(title).toHaveValue("保留的创建草稿");
    expect(await within(dialog).findByText("创建待办失败")).toBeVisible();
    expect(screen.getByText("修缺陷")).toBeVisible();
    expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
  });

  it("ignores delayed project recheck 404 after an account switch", async () => {
    const loss = vi.fn();
    let rejectRead!: (error: Error) => void;
    projectRead.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectRead = reject;
        }),
    );
    update.mockRejectedValue(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    const { rerender } = renderPlan("owner", loss);
    await screen.findByText("写周报");
    await changeRowStatus("写周报", "done");
    await waitFor(() => expect(projectRead).toHaveBeenCalledWith("p1"));
    planRows.mockResolvedValue(
      planRowsResponse([{ ...todoDoing, title: "新账号待办" }]),
    );
    currentUserId.value = 99;
    rerender(
      <ProjectPlan
        {...{
          projectId: "p1",
          role: "owner" as const,
          members,
          onProjectAccessLost: loss,
        }}
      />,
    );
    expect(await screen.findByText("新账号待办")).toBeVisible();
    await act(async () => {
      rejectRead(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    });
    expect(screen.getByText("新账号待办")).toBeVisible();
    expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
    expect(loss).not.toHaveBeenCalled();
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
      within(rowFor("写周报")).getByRole("button", { name: "修改状态" }),
    ).toBeInTheDocument();
    expect(within(rowFor("alice 的待办")).queryByRole("combobox")).toBeNull();
    expect(
      within(rowFor("alice 的待办")).queryByRole("button", {
        name: "编辑待办",
      }),
    ).toBeNull();
    expect(
      within(rowFor("alice 的待办")).queryByRole("button", {
        name: "删除待办",
      }),
    ).toBeNull();

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "编辑待办" }),
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
        { ...todoOpen, status: "done", version: 4, display_revision: 4 },
        { ...todoDoing, status: "done", version: 2, display_revision: 2 },
      ],
    });
    renderPlan("owner");
    await screen.findByText("写周报");

    expect(within(rowFor("写周报")).getByRole("checkbox")).toBeVisible();
    fireEvent.click(within(rowFor("写周报")).getByRole("checkbox"));
    fireEvent.click(within(rowFor("修缺陷")).getByRole("checkbox"));
    await waitFor(() =>
      expect(
        screen.getByText("已选择 2 条待办，最多 50 条"),
      ).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByRole("combobox", { name: "批量状态" }), {
      target: { value: "done" },
    });
    await user.click(screen.getByRole("button", { name: "应用批量修改" }));

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
      expect(screen.queryByText("已选择 2 条待办，最多 50 条")).toBeNull(),
    );
  });

  it("deletes with the current version only after confirmation", async () => {
    const user = userEvent.setup();
    remove.mockResolvedValue(undefined);
    planRows
      .mockResolvedValueOnce(planRowsResponse())
      .mockResolvedValue(planRowsResponse([todoDoing, todoOther]));
    renderPlan("owner");
    await screen.findByText("写周报");

    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "删除待办" }),
    );
    await user.click(await screen.findByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("p1", "t1", 3));
    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());
  });

  it("confirms a complete child version set and removes exact tree receipt ids without GET of the deleted parent", async () => {
    const user = userEvent.setup();
    const root = { ...todoOpen, children_count: 1, children_revision: 2 };
    const child = {
      ...todoDoing,
      todo_id: "child-tree",
      parent_todo_id: root.todo_id,
      children_revision: null,
      children_count: 0,
      done_children_count: 0,
    };
    planRows.mockResolvedValue(planRowsResponse([root, todoOther]));
    remove.mockRejectedValue(
      new Error(
        '409 - {"error":{"code":"CONFLICT","details":{"reason":"children_confirmation_required"}}}',
      ),
    );
    listChildren.mockResolvedValue({
      items: [child],
      limit: 100,
      has_more: false,
      next_cursor: null,
      children_revision: 2,
      parent_display_revision: 3,
      active_count: 1,
      done_count: 0,
    });
    deleteTree.mockImplementation(async () => {
      planRows.mockResolvedValue(planRowsResponse([todoOther]));
      return {
        deleted_todo_ids: [root.todo_id, child.todo_id],
        children_revision: 3,
        hierarchy_revision: 2,
      };
    });
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "删除待办" }),
    );
    await user.click(await screen.findByRole("button", { name: "确认删除" }));
    await user.click(
      await screen.findByRole("button", { name: "删除待办和子待办" }),
    );
    await waitFor(() =>
      expect(deleteTree).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        expected_children_revision: 2,
        children: [{ todo_id: "child-tree", expected_version: 1 }],
      }),
    );
    await waitFor(() => expect(screen.queryByText("写周报")).toBeNull());
    expect(get).not.toHaveBeenCalled();
    expect(screen.getByText("alice 的待办")).toBeVisible();
  });

  it("blocks tree confirmation when its child page is incomplete", async () => {
    const user = userEvent.setup();
    const root = { ...todoOpen, children_count: 1, children_revision: 2 };
    planRows.mockResolvedValue(planRowsResponse([root]));
    remove.mockRejectedValue(
      new Error(
        '409 - {"error":{"code":"CONFLICT","details":{"reason":"children_confirmation_required"}}}',
      ),
    );
    listChildren.mockResolvedValue({
      items: [],
      limit: 100,
      has_more: true,
      next_cursor: "opaque",
      children_revision: 2,
      parent_display_revision: 3,
      active_count: 1,
      done_count: 0,
    });
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "删除待办" }),
    );
    await user.click(await screen.findByRole("button", { name: "确认删除" }));
    await screen.findByText("删除待办树失败");
    expect(deleteTree).not.toHaveBeenCalled();
    expect(screen.getByText("写周报")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "删除待办和子待办" }),
    ).toBeNull();
  });

  it("keeps root rows visible when confirmed tree deletion is rejected", async () => {
    const user = userEvent.setup();
    const root = { ...todoOpen, children_count: 1, children_revision: 2 };
    const child = {
      ...todoDoing,
      todo_id: "child-tree",
      parent_todo_id: root.todo_id,
      children_revision: null,
      children_count: 0,
      done_children_count: 0,
    };
    planRows.mockResolvedValue(planRowsResponse([root]));
    remove.mockRejectedValue(
      new Error(
        '409 - {"error":{"code":"CONFLICT","details":{"reason":"children_confirmation_required"}}}',
      ),
    );
    listChildren.mockResolvedValue({
      items: [child],
      limit: 100,
      has_more: false,
      next_cursor: null,
      children_revision: 2,
      parent_display_revision: 3,
      active_count: 1,
      done_count: 0,
    });
    deleteTree.mockRejectedValue(
      new Error(
        '409 - {"error":{"code":"CONFLICT","details":{"reason":"version_conflict"}}}',
      ),
    );
    renderPlan("owner");
    await screen.findByText("写周报");
    await user.click(
      within(rowFor("写周报")).getByRole("button", { name: "删除待办" }),
    );
    await user.click(await screen.findByRole("button", { name: "确认删除" }));
    await user.click(
      await screen.findByRole("button", { name: "删除待办和子待办" }),
    );
    await screen.findByText("删除待办树失败");
    expect(screen.getByText("写周报")).toBeVisible();
    expect(get).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /^取\s*消$/ }));
  });

  it.each([false, true])(
    "closes tree confirmation on same-actor project revocation and drops late receipt (pending=%s)",
    async (pending) => {
      const user = userEvent.setup();
      const loss = vi.fn();
      const root = { ...todoOpen, children_count: 1, children_revision: 2 };
      const child = {
        ...todoDoing,
        todo_id: "child-tree",
        parent_todo_id: root.todo_id,
        children_revision: null,
        children_count: 0,
        done_children_count: 0,
      };
      let resolve!: (receipt: {
        deleted_todo_ids: string[];
        children_revision: number;
        hierarchy_revision: number;
      }) => void;
      deleteTree.mockReturnValue(
        new Promise((resolution) => {
          resolve = resolution;
        }),
      );
      planRows.mockResolvedValue(planRowsResponse([root]));
      remove.mockRejectedValue(
        new Error(
          '409 - {"error":{"code":"CONFLICT","details":{"reason":"children_confirmation_required"}}}',
        ),
      );
      listChildren.mockResolvedValue({
        items: [child],
        limit: 100,
        has_more: false,
        next_cursor: null,
        children_revision: 2,
        parent_display_revision: 3,
        active_count: 1,
        done_count: 0,
      });
      renderPlan("owner", loss);
      await screen.findByText("写周报");
      await user.click(
        within(rowFor("写周报")).getByRole("button", { name: "删除待办" }),
      );
      await user.click(await screen.findByRole("button", { name: "确认删除" }));
      const confirmation = await screen.findByRole("button", {
        name: "删除待办和子待办",
      });
      if (pending) {
        await user.click(confirmation);
        await waitFor(() => expect(deleteTree).toHaveBeenCalledTimes(1));
      }
      const denied = new Error('404 - {"error":{"code":"NOT_FOUND"}}');
      planRows.mockRejectedValue(denied);
      projectRead.mockRejectedValue(denied);
      fireEvent.click(screen.getByRole("button", { name: /^刷\s*新$/ }));
      await waitFor(() => expect(loss).toHaveBeenCalledTimes(1));
      expect(loss.mock.calls[0][0].account_id).toBe(2);
      await waitFor(() =>
        expect(
          screen.queryByRole("button", { name: "删除待办和子待办" }),
        ).toBeNull(),
      );
      if (pending)
        await act(async () =>
          resolve({
            deleted_todo_ids: [root.todo_id, child.todo_id],
            children_revision: 3,
            hierarchy_revision: 2,
          }),
        );
      else expect(deleteTree).not.toHaveBeenCalled();
      expect(screen.queryByText("待办已删除")).toBeNull();
      expect(screen.queryByText("写周报")).toBeNull();
      expect(get).not.toHaveBeenCalled();
    },
  );
});
