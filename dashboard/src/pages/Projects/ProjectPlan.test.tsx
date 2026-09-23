import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { list, create, update, remove, bulk } = vi.hoisted(() => ({
  list: vi.fn(),
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
    projectTodosApi: { list, create, get: vi.fn(), update, remove, bulk },
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

const todoOpen = {
  todo_id: "t1",
  project_id: "p1",
  title: "写周报",
  description: "本周进展",
  status: "todo" as const,
  creator_user_id: 1,
  assignee_user_id: 2,
  version: 3,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

const todoDoing = {
  todo_id: "t2",
  project_id: "p1",
  title: "修缺陷",
  description: "",
  status: "in_progress" as const,
  creator_user_id: 2,
  assignee_user_id: null,
  version: 1,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

const todoOther = {
  todo_id: "t3",
  project_id: "p1",
  title: "alice 的待办",
  description: "",
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
  list.mockReset();
  create.mockReset();
  update.mockReset();
  remove.mockReset();
  bulk.mockReset();
  currentUserId.value = 2;
  list.mockResolvedValue(listResponse());
});

describe("ProjectPlan table/board against the PS-04 contract", () => {
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
