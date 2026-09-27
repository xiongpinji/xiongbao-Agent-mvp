import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { get, listComments, createComment, update } = vi.hoisted(() => ({
  get: vi.fn(),
  listComments: vi.fn(),
  createComment: vi.fn(),
  update: vi.fn(),
}));

vi.mock("../../api/modules/projectTodos", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectTodos")
  >();
  return {
    ...actual,
    projectTodosApi: {
      ...actual.projectTodosApi,
      get,
      listComments,
      createComment,
      update,
    },
  };
});

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

import ProjectTodoDetail from "./ProjectTodoDetail";

const todo = {
  todo_id: "t1",
  project_id: "p1",
  title: "写周报",
  description: "# 原始文本 <b>\n第二行",
  status: "todo" as const,
  creator_user_id: 1,
  assignee_user_id: 2,
  version: 3,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

const members = [
  { user_id: 1, username: "alice", role: "owner" as const },
  { user_id: 2, username: "bob", role: "member" as const },
];

const comment = {
  comment_id: "c1",
  todo_id: "t1",
  author_user_id: 2,
  author_name: "bob",
  body: "已核对数据",
  images: [],
  created_at: 1_700_000_100,
};

const changed = vi.fn();
const accessLost = vi.fn();

function detail(projectId = "p1", todoId = "t1") {
  return (
    <ProjectTodoDetail
      projectId={projectId}
      todoId={todoId}
      role="member"
      members={members}
      currentUserId={2}
      onClose={vi.fn()}
      onChanged={changed}
      onAccessLost={accessLost}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  get.mockResolvedValue(todo);
  listComments.mockResolvedValue({ items: [comment], next_cursor: null });
});

describe("ProjectTodoDetail B1", () => {
  it("shows old description literally, comments on the left and editable fields on the right", async () => {
    render(detail());

    const dialog = await screen.findByRole("dialog", { name: "待办详情" });
    expect(
      within(dialog).getByRole("heading", { name: "写周报" }),
    ).toBeVisible();
    const description = within(dialog).getByTestId("todo-description");
    expect(description.textContent).toBe("# 原始文本 <b>\n第二行");
    expect(description.querySelector("b")).toBeNull();
    expect(within(dialog).getByText("已核对数据")).toBeVisible();
    expect(within(dialog).getByText("状态")).toBeVisible();
    expect(within(dialog).getByText("处理人")).toBeVisible();
  });

  it("keeps a failed text draft and reuses its UUID v4 on an explicit retry", async () => {
    const user = userEvent.setup();
    createComment
      .mockRejectedValueOnce(new Error("网络暂时不可用"))
      .mockResolvedValueOnce(comment);
    render(detail());
    const dialog = await screen.findByRole("dialog", { name: "待办详情" });
    const input = within(dialog).getByRole("textbox", { name: "评论" });
    await user.type(input, "未发送草稿");
    await user.click(within(dialog).getByRole("button", { name: "发表评论" }));
    expect(await screen.findByText("网络暂时不可用")).toBeVisible();
    expect(input).toHaveValue("未发送草稿");

    const firstBody = createComment.mock.calls[0][2];
    expect(firstBody.body).toBe("未发送草稿");
    expect(firstBody.client_request_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    await user.click(within(dialog).getByRole("button", { name: "发表评论" }));
    await waitFor(() => expect(createComment).toHaveBeenCalledTimes(2));
    expect(createComment.mock.calls[1][2]).toEqual(firstBody);
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("refreshes comments after a successful post so concurrent comments appear", async () => {
    const user = userEvent.setup();
    const posted = { ...comment, comment_id: "c2", body: "我的评论" };
    const concurrent = {
      ...comment,
      comment_id: "c3",
      body: "另一位成员的评论",
    };
    createComment.mockResolvedValueOnce(posted);
    listComments
      .mockResolvedValueOnce({ items: [comment], next_cursor: null })
      .mockResolvedValueOnce({
        items: [concurrent, posted, comment],
        next_cursor: null,
      });

    render(detail());
    await screen.findByText("已核对数据");
    await user.type(screen.getByRole("textbox", { name: "评论" }), "我的评论");
    await user.click(screen.getByRole("button", { name: "发表评论" }));

    await waitFor(() => expect(listComments).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("另一位成员的评论")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue("");
  });

  it("removes an old project's private title and comments immediately on a route switch", async () => {
    const view = render(detail());
    expect(await screen.findByText("已核对数据")).toBeVisible();
    get.mockImplementationOnce(() => new Promise(() => {}));
    listComments.mockImplementationOnce(() => new Promise(() => {}));
    view.rerender(detail("p2", "t2"));
    expect(screen.queryByText("已核对数据")).toBeNull();
    expect(screen.queryByRole("heading", { name: "写周报" })).toBeNull();
  });

  it("clears loaded detail on a comment-list 404 after revocation", async () => {
    listComments.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    render(detail());
    expect(await screen.findByText("待办不存在或你无权访问")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "写周报" })).toBeNull();
  });

  it("keeps the comment draft across a status conflict and a manual refresh", async () => {
    const user = userEvent.setup();
    update.mockRejectedValueOnce(
      new Error('409 - {"error":{"code":"CONFLICT"}}'),
    );
    render(detail());
    const dialog = await screen.findByRole("dialog", { name: "待办详情" });
    await screen.findByText("已核对数据");
    const input = within(dialog).getByRole("textbox", { name: "评论" });
    await user.type(input, "准备发送的评论");
    fireEvent.mouseDown(within(dialog).getByRole("combobox"));
    fireEvent.click(screen.getByText("已完成"));
    expect(
      await screen.findByText("待办已被更新，请刷新后比较再保存。"),
    ).toBeVisible();
    expect(input).toHaveValue("准备发送的评论");
    await user.click(within(dialog).getByRole("button", { name: /刷\s*新/ }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue(
        "准备发送的评论",
      ),
    );
  });

  it("does not backfill the prior project's detail when its GET arrives late", async () => {
    let resolvePrior: ((value: typeof todo) => void) | undefined;
    get.mockImplementationOnce(
      () => new Promise<typeof todo>((resolve) => (resolvePrior = resolve)),
    );
    get.mockResolvedValueOnce({
      ...todo,
      project_id: "p2",
      todo_id: "t2",
      title: "项目二待办",
    });
    listComments.mockResolvedValueOnce({ items: [comment], next_cursor: null });
    listComments.mockResolvedValueOnce({ items: [], next_cursor: null });
    const view = render(detail());
    view.rerender(detail("p2", "t2"));
    expect(
      await screen.findByRole("heading", { name: "项目二待办" }),
    ).toBeVisible();
    resolvePrior?.(todo);
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "写周报" })).toBeNull(),
    );
    expect(screen.queryByText("已核对数据")).toBeNull();
  });

  it("clears previously loaded private data if posting finds membership revoked", async () => {
    const user = userEvent.setup();
    createComment.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    render(detail());
    await screen.findByText("已核对数据");
    await user.type(screen.getByRole("textbox", { name: "评论" }), "新的评论");
    await user.click(screen.getByRole("button", { name: "发表评论" }));
    expect(await screen.findByText("待办不存在或你无权访问")).toBeVisible();
    expect(screen.queryByText("已核对数据")).toBeNull();
    expect(screen.queryByRole("heading", { name: "写周报" })).toBeNull();
  });

  it("ignores an old project's late POST while a new project draft is being submitted", async () => {
    const user = userEvent.setup();
    let resolveOld: ((value: typeof comment) => void) | undefined;
    let resolveNew: ((value: typeof comment) => void) | undefined;
    createComment
      .mockImplementationOnce(
        () => new Promise<typeof comment>((resolve) => (resolveOld = resolve)),
      )
      .mockImplementationOnce(
        () => new Promise<typeof comment>((resolve) => (resolveNew = resolve)),
      );
    get.mockImplementation((projectId: string) =>
      Promise.resolve(
        projectId === "p1"
          ? todo
          : { ...todo, project_id: "p2", todo_id: "t2", title: "项目二待办" },
      ),
    );
    const view = render(detail());
    await screen.findByText("已核对数据");
    await user.type(
      screen.getByRole("textbox", { name: "评论" }),
      "项目一草稿",
    );
    await user.click(screen.getByRole("button", { name: "发表评论" }));
    await waitFor(() => expect(createComment).toHaveBeenCalledTimes(1));

    view.rerender(detail("p2", "t2"));
    await screen.findByRole("heading", { name: "项目二待办" });
    await user.type(
      screen.getByRole("textbox", { name: "评论" }),
      "项目二草稿",
    );
    await user.click(screen.getByRole("button", { name: "发表评论" }));
    await waitFor(() => expect(createComment).toHaveBeenCalledTimes(2));
    resolveOld?.(comment);
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue(
        "项目二草稿",
      ),
    );
    expect(screen.getByRole("button", { name: /发表评论/ })).toBeDisabled();
    resolveNew?.({ ...comment, todo_id: "t2", comment_id: "c2" });
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue(""),
    );
  });

  it("ignores an old project's late PATCH 404 after switching to another todo", async () => {
    let rejectOld: ((reason: Error) => void) | undefined;
    update.mockImplementationOnce(
      () => new Promise((_, reject) => (rejectOld = reject)),
    );
    get.mockImplementation((projectId: string) =>
      Promise.resolve(
        projectId === "p1"
          ? todo
          : { ...todo, project_id: "p2", todo_id: "t2", title: "项目二待办" },
      ),
    );
    const view = render(detail());
    await screen.findByText("已核对数据");
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(screen.getByText("已完成"));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));

    view.rerender(detail("p2", "t2"));
    await screen.findByRole("heading", { name: "项目二待办" });
    rejectOld?.(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "项目二待办" })).toBeVisible(),
    );
    expect(screen.queryByText("待办不存在或你无权访问")).toBeNull();
    expect(accessLost).not.toHaveBeenCalled();
    expect(changed).not.toHaveBeenCalled();
  });
});
