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

const { catalogGet, get, listComments, accessLost } = vi.hoisted(() => ({
  catalogGet: vi.fn(),
  get: vi.fn(),
  listComments: vi.fn(),
  accessLost: vi.fn(),
}));

vi.mock("../../api/modules/projectTodoCatalog", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectTodoCatalog")
  >();
  return {
    ...actual,
    projectTodoCatalogApi: { ...actual.projectTodoCatalogApi, get: catalogGet },
  };
});

vi.mock("../../api/modules/projectTodos", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectTodos")
  >();
  return {
    ...actual,
    projectTodosApi: { ...actual.projectTodosApi, get, listComments },
  };
});

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

import ProjectTodoDetail from "./ProjectTodoDetail";
import type {
  ProjectTodo,
  ProjectTodoComment,
  ProjectTodoCommentPage,
} from "../../api/modules/projectTodos";

const todo: ProjectTodo = {
  todo_id: "t1",
  project_id: "p1",
  title: "项目 A 待办",
  description: "已有待办正文",
  description_format: "plain",
  start_date: null,
  due_date: null,
  priority_id: null,
  tag_ids: [],
  catalog_revision: 1,
  status: "todo",
  creator_user_id: 1,
  assignee_user_id: 2,
  version: 3,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

function comment(
  id: string,
  body: string,
  createdAt = 1_700_000_100,
  todoId = "t1",
) {
  return {
    comment_id: id,
    todo_id: todoId,
    author_user_id: 2,
    author_name: "bob",
    body,
    images: [],
    created_at: createdAt,
  } satisfies ProjectTodoComment;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function detail(projectId = "p1", todoId = "t1", accountId = 2) {
  return (
    <ProjectTodoDetail
      projectId={projectId}
      todoId={todoId}
      role="member"
      members={[
        { user_id: 1, username: "alice", role: "owner" },
        { user_id: accountId, username: "bob", role: "member" },
      ]}
      currentUserId={accountId}
      onClose={vi.fn()}
      onChanged={vi.fn()}
      onAccessLost={accessLost}
    />
  );
}

function moreButton() {
  return screen.getByRole("button", { name: /加载更多评论$/ });
}

beforeEach(() => {
  vi.clearAllMocks();
  catalogGet.mockReset().mockResolvedValue(catalogFixture);
  get.mockReset().mockResolvedValue(todo);
  listComments.mockReset();
});

describe("ProjectTodoDetail comment pagination", () => {
  it("merges and deduplicates clicked pages chronologically and follows each next cursor", async () => {
    const user = userEvent.setup();
    listComments
      .mockResolvedValueOnce({
        items: [
          comment("c-late", "较晚的初始评论", 300),
          comment("c-a", "同时间较小 ID", 200),
        ],
        next_cursor: "cursor-page-2",
      })
      .mockResolvedValueOnce({
        items: [
          comment("c-z", "同时间较大 ID", 200),
          comment("c-late", "重复 ID 的最新正文", 300),
          comment("c-early", "较早的第二页评论", 100),
        ],
        next_cursor: "cursor-page-3",
      })
      .mockResolvedValueOnce({
        items: [comment("c-last", "第三页评论", 400)],
        next_cursor: null,
      });

    render(detail());
    await screen.findByText("较晚的初始评论");
    expect(listComments).toHaveBeenNthCalledWith(1, "p1", "t1", {
      limit: 20,
    });
    await user.click(moreButton());
    await screen.findByText("重复 ID 的最新正文");
    expect(listComments).toHaveBeenNthCalledWith(2, "p1", "t1", {
      limit: 20,
      cursor: "cursor-page-2",
    });
    expect(
      screen.getAllByRole("article").map((article) => article.textContent),
    ).toEqual([
      expect.stringContaining("较早的第二页评论"),
      expect.stringContaining("同时间较小 ID"),
      expect.stringContaining("同时间较大 ID"),
      expect.stringContaining("重复 ID 的最新正文"),
    ]);
    expect(screen.queryByText("较晚的初始评论")).toBeNull();
    expect(screen.getAllByText("重复 ID 的最新正文")).toHaveLength(1);
    await user.click(moreButton());
    expect(await screen.findByText("第三页评论")).toBeVisible();
    expect(listComments).toHaveBeenNthCalledWith(3, "p1", "t1", {
      limit: 20,
      cursor: "cursor-page-3",
    });
    expect(screen.queryByRole("button", { name: "加载更多评论" })).toBeNull();
  });

  it("keeps existing content and drafts after a page failure and retries the same cursor", async () => {
    const user = userEvent.setup();
    const failedPage = deferred<ProjectTodoCommentPage>();
    listComments
      .mockResolvedValueOnce({
        items: [comment("c-existing", "已加载评论正文")],
        next_cursor: "retry-cursor",
      })
      .mockReturnValueOnce(failedPage.promise)
      .mockResolvedValueOnce({
        items: [comment("c-retry", "重试成功的评论")],
        next_cursor: null,
      });
    render(detail());
    await screen.findByText("已加载评论正文");
    await user.click(screen.getByRole("button", { name: "编辑描述" }));
    fireEvent.change(screen.getByRole("textbox", { name: "待办描述" }), {
      target: { value: "未保存描述草稿" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "评论" }), {
      target: { value: "未发送评论草稿" },
    });
    await user.click(moreButton());
    expect(moreButton()).toHaveClass("ant-btn-loading");
    await act(async () => {
      failedPage.reject(
        new Error(
          '503 - {"error":{"code":"SERVICE_UNAVAILABLE","message":"分页暂时不可用"}}',
        ),
      );
    });
    expect(await screen.findByText("分页暂时不可用")).toBeVisible();
    expect(screen.getByText("已加载评论正文")).toBeVisible();
    expect(screen.getByRole("heading", { name: todo.title })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "待办描述" })).toHaveValue(
      "未保存描述草稿",
    );
    expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue(
      "未发送评论草稿",
    );
    expect(moreButton()).not.toHaveClass("ant-btn-loading");
    expect(accessLost).not.toHaveBeenCalled();
    await user.click(moreButton());
    expect(await screen.findByText("重试成功的评论")).toBeVisible();
    expect(listComments).toHaveBeenNthCalledWith(2, "p1", "t1", {
      limit: 20,
      cursor: "retry-cursor",
    });
    expect(listComments).toHaveBeenNthCalledWith(3, "p1", "t1", {
      limit: 20,
      cursor: "retry-cursor",
    });
    expect(screen.queryByText("分页暂时不可用")).toBeNull();
    expect(screen.getByText("已加载评论正文")).toBeVisible();
  });

  it.each([
    ["A → B", "success"],
    ["A → B", "404"],
    ["A → B → new A", "success"],
    ["A → B → new A", "404"],
    ["same project, new account", "success"],
    ["same project, new account", "404"],
  ] as const)(
    "isolates an old page %s / %s from current content, drafts, error and loading",
    async (transition, outcome) => {
      const user = userEvent.setup();
      const oldPage = deferred<ProjectTodoCommentPage>();
      const currentPage = deferred<ProjectTodoCommentPage>();
      const accountSwitch = transition === "same project, new account";
      const returnsToA = transition === "A → B → new A";
      const currentProject = accountSwitch || returnsToA ? "p1" : "p2";
      const currentTodoId = accountSwitch || returnsToA ? "t1" : "t2";
      const currentAccount = accountSwitch ? 9 : 2;
      const currentTodo = {
        ...todo,
        project_id: currentProject,
        todo_id: currentTodoId,
        title: "当前会话待办",
        description: "当前会话原始正文",
        assignee_user_id: currentAccount,
      };
      const currentFirstPage = {
        items: [
          comment("c-current", "当前会话第一页评论", undefined, currentTodoId),
        ],
        next_cursor: "current-page-2",
      };
      listComments
        .mockResolvedValueOnce({
          items: [comment("c-old", "旧会话第一页评论")],
          next_cursor: "old-page-2",
        })
        .mockReturnValueOnce(oldPage.promise);
      get.mockResolvedValueOnce(todo);
      if (returnsToA) {
        get.mockResolvedValueOnce({
          ...todo,
          project_id: "p2",
          todo_id: "t2",
          title: "过渡项目 B 待办",
        });
        listComments.mockResolvedValueOnce({ items: [], next_cursor: null });
      }
      get.mockResolvedValueOnce(currentTodo);
      listComments
        .mockResolvedValueOnce(currentFirstPage)
        .mockReturnValueOnce(currentPage.promise)
        .mockResolvedValueOnce({
          items: [
            comment(
              "c-current-final",
              "当前会话第三页评论",
              undefined,
              currentTodoId,
            ),
          ],
          next_cursor: null,
        });

      const view = render(detail());
      await screen.findByText("旧会话第一页评论");
      await user.click(moreButton());
      expect(listComments).toHaveBeenNthCalledWith(2, "p1", "t1", {
        limit: 20,
        cursor: "old-page-2",
      });
      if (returnsToA) {
        view.rerender(detail("p2", "t2"));
        await screen.findByRole("heading", { name: "过渡项目 B 待办" });
      }
      view.rerender(detail(currentProject, currentTodoId, currentAccount));
      await screen.findByRole("heading", { name: currentTodo.title });
      expect(screen.getByTestId("todo-description")).toHaveTextContent(
        currentTodo.description,
      );
      expect(screen.queryByText("旧会话第一页评论")).toBeNull();
      await user.click(screen.getByRole("button", { name: "编辑描述" }));
      fireEvent.change(screen.getByRole("textbox", { name: "待办描述" }), {
        target: { value: "当前会话描述草稿" },
      });
      fireEvent.change(screen.getByRole("textbox", { name: "评论" }), {
        target: { value: "当前会话评论草稿" },
      });
      await user.click(moreButton());
      expect(listComments).toHaveBeenLastCalledWith(
        currentProject,
        currentTodoId,
        { limit: 20, cursor: "current-page-2" },
      );
      fireEvent.paste(screen.getByRole("textbox", { name: "评论" }), {
        clipboardData: {
          files: [new File(["text"], "draft.txt", { type: "text/plain" })],
        },
      });
      expect(screen.getByText("仅支持 PNG、JPEG 或 WebP 图片")).toBeVisible();
      expect(moreButton()).toHaveClass("ant-btn-loading");

      await act(async () => {
        if (outcome === "success") {
          oldPage.resolve({
            items: [comment("c-old-page-2", "旧分页私密正文")],
            next_cursor: "old-page-3",
          });
        } else {
          oldPage.reject(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
        }
      });
      expect(
        screen.getByRole("heading", { name: currentTodo.title }),
      ).toBeVisible();
      expect(screen.getByText("当前会话第一页评论")).toBeVisible();
      expect(screen.queryByText("旧分页私密正文")).toBeNull();
      expect(screen.queryByText("待办不存在或你无权访问")).toBeNull();
      expect(screen.getByRole("textbox", { name: "待办描述" })).toHaveValue(
        "当前会话描述草稿",
      );
      expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue(
        "当前会话评论草稿",
      );
      expect(screen.getByText("仅支持 PNG、JPEG 或 WebP 图片")).toBeVisible();
      expect(moreButton()).toHaveClass("ant-btn-loading");
      expect(accessLost).not.toHaveBeenCalled();

      await act(async () => {
        currentPage.resolve({
          items: [
            comment(
              "c-current-page-2",
              "当前会话第二页评论",
              undefined,
              currentTodoId,
            ),
          ],
          next_cursor: "current-page-3",
        });
      });
      expect(await screen.findByText("当前会话第二页评论")).toBeVisible();
      expect(moreButton()).not.toHaveClass("ant-btn-loading");
      await user.click(moreButton());
      await waitFor(() =>
        expect(listComments).toHaveBeenLastCalledWith(
          currentProject,
          currentTodoId,
          { limit: 20, cursor: "current-page-3" },
        ),
      );
      expect(await screen.findByText("当前会话第三页评论")).toBeVisible();
      expect(screen.queryByRole("button", { name: "加载更多评论" })).toBeNull();
      expect(screen.getAllByRole("article")).toHaveLength(3);
      expect(
        within(screen.getAllByRole("article")[0]).getByText(
          "当前会话第一页评论",
        ),
      ).toBeVisible();
      expect(screen.getByRole("textbox", { name: "待办描述" })).toHaveValue(
        "当前会话描述草稿",
      );
      expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue(
        "当前会话评论草稿",
      );
    },
  );
});
