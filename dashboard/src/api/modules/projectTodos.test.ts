import { beforeEach, describe, expect, it, vi } from "vitest";

const { request, requestUpload, requestBlob } = vi.hoisted(() => ({
  request: vi.fn(),
  requestUpload: vi.fn(),
  requestBlob: vi.fn(),
}));

vi.mock("../request", () => ({ request, requestUpload, requestBlob }));

import {
  PROJECT_TODOS_BULK_MAX,
  PROJECT_TODOS_PAGE_SIZE,
  normalizeTodoBulkResponse,
  projectTodosApi,
  type ProjectTodo,
} from "./projectTodos";

const todo: ProjectTodo = {
  todo_id: "t1",
  project_id: "p1",
  title: "写周报",
  description: "",
  description_format: "plain",
  status: "todo",
  start_date: null,
  due_date: null,
  priority_id: null,
  tag_ids: [],
  catalog_revision: 1,
  creator_user_id: 1,
  assignee_user_id: null,
  version: 2,
  display_revision: 2,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_100,
};

beforeEach(() => {
  request.mockClear();
  requestUpload.mockClear();
  requestBlob.mockClear();
});

describe("projectTodosApi", () => {
  it("D1 GET retains AbortSignal and fixes its read method without adding a write token", () => {
    const signal = new AbortController().signal;
    projectTodosApi.get("p1", "t1", {
      signal,
      method: "DELETE",
      body: "wrong",
    });
    expect(request).toHaveBeenCalledWith("/projects/p1/todos/t1", {
      signal,
      method: "GET",
      body: undefined,
    });
  });
  it("preserves C1 null clears, omitted dates, full tag replacement and exact catalog revision", () => {
    projectTodosApi.update("p1", "t1", {
      expected_version: 3,
      due_date: null,
      priority_id: null,
      tag_ids: [],
      expected_catalog_revision: 7,
    });
    const body = JSON.parse(request.mock.calls[0][1].body);
    expect(body).toEqual({
      expected_version: 3,
      due_date: null,
      priority_id: null,
      tag_ids: [],
      expected_catalog_revision: 7,
    });
    expect(body).not.toHaveProperty("start_date");
    projectTodosApi.create("p1", {
      title: "计划",
      status: "in_progress",
      start_date: "2020-01-01",
      due_date: "2026-09-28",
      priority_id: "pr1",
      tag_ids: ["a", "b"],
      expected_catalog_revision: 7,
    });
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({
      title: "计划",
      status: "in_progress",
      start_date: "2020-01-01",
      due_date: "2026-09-28",
      priority_id: "pr1",
      tag_ids: ["a", "b"],
      expected_catalog_revision: 7,
    });
  });
  it("lists with server-side filters, encoded ids and default paging", () => {
    projectTodosApi.list("p 1/2", { q: "周报 100%_x", status: "in_progress" });

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(
      "/projects/p%201%2F2/todos?q=%E5%91%A8%E6%8A%A5+100%25_x&status=in_progress&limit=50&offset=0",
    );
  });

  it("omits blank q and unset filters but keeps explicit paging", () => {
    projectTodosApi.list("p1", {
      q: "   ",
      limit: PROJECT_TODOS_PAGE_SIZE,
      offset: 50,
    });

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/todos?limit=50&offset=50",
    );
  });

  it("sends an assignee filter only when it is a user id", () => {
    projectTodosApi.list("p1", { assigneeUserId: 7, offset: 100 });

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/todos?assignee_user_id=7&limit=50&offset=100",
    );
  });

  it("creates a todo via POST JSON without any actor id", () => {
    projectTodosApi.create("p1", { title: "写周报", assignee_user_id: 2 });

    expect(request).toHaveBeenCalledWith("/projects/p1/todos", {
      method: "POST",
      body: JSON.stringify({ title: "写周报", assignee_user_id: 2 }),
    });
  });

  it("encodes the todo id for detail and PATCH carries expected_version", () => {
    projectTodosApi.get("p1", "t 1/2");
    projectTodosApi.update("p1", "t 1/2", {
      expected_version: 3,
      status: "done",
    });

    expect(request).toHaveBeenNthCalledWith(1, "/projects/p1/todos/t%201%2F2");
    expect(request).toHaveBeenNthCalledWith(2, "/projects/p1/todos/t%201%2F2", {
      method: "PATCH",
      body: JSON.stringify({ expected_version: 3, status: "done" }),
    });
  });

  it("carries the explicit Markdown format with description writes", () => {
    projectTodosApi.create("p1", {
      title: "写周报",
      description: "# 周报",
      description_format: "markdown",
    });
    projectTodosApi.update("p1", "t1", {
      expected_version: 2,
      description: "# 新周报",
      description_format: "markdown",
    });

    expect(request).toHaveBeenNthCalledWith(1, "/projects/p1/todos", {
      method: "POST",
      body: JSON.stringify({
        title: "写周报",
        description: "# 周报",
        description_format: "markdown",
      }),
    });
    expect(request).toHaveBeenNthCalledWith(2, "/projects/p1/todos/t1", {
      method: "PATCH",
      body: JSON.stringify({
        expected_version: 2,
        description: "# 新周报",
        description_format: "markdown",
      }),
    });
  });

  it("lists comments with an opaque cursor and posts only the text and request id", () => {
    projectTodosApi.listComments("p 1", "t/2", {
      limit: 20,
      cursor: "opaque+cursor",
    });
    projectTodosApi.createComment("p 1", "t/2", {
      body: "成员评论",
      client_request_id: "d6e47312-3f3d-4a27-a43a-23c5df13218b",
    });

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/projects/p%201/todos/t%2F2/comments?limit=20&cursor=opaque%2Bcursor",
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      "/projects/p%201/todos/t%2F2/comments",
      {
        method: "POST",
        body: JSON.stringify({
          body: "成员评论",
          client_request_id: "d6e47312-3f3d-4a27-a43a-23c5df13218b",
        }),
      },
    );
  });

  it("posts ordered comment images in one abortable multipart request and reads each through auth", () => {
    const first = new File(["a"], "first.png", { type: "image/png" });
    const second = new File(["b"], "second.webp", { type: "image/webp" });
    const controller = new AbortController();
    const onProgress = vi.fn();

    projectTodosApi.createComment(
      "p 1",
      "t/2",
      {
        body: "配图说明",
        client_request_id: "d6e47312-3f3d-4a27-a43a-23c5df13218b",
        images: [first, second],
      },
      { signal: controller.signal },
      onProgress,
    );
    expect(request).not.toHaveBeenCalled();
    expect(requestUpload).toHaveBeenCalledTimes(1);
    const [path, form, options, progress] = requestUpload.mock.calls[0];
    expect(path).toBe("/projects/p%201/todos/t%2F2/comments");
    expect(Array.from((form as FormData).entries())).toEqual([
      ["client_request_id", "d6e47312-3f3d-4a27-a43a-23c5df13218b"],
      ["body", "配图说明"],
      ["images", first],
      ["images", second],
    ]);
    expect(options.signal).toBe(controller.signal);
    expect(progress).toBe(onProgress);

    projectTodosApi.readCommentImage("p 1", "t/2", "c 3", "i/4", {
      signal: controller.signal,
    });
    expect(requestBlob).toHaveBeenCalledWith(
      "/projects/p%201/todos/t%2F2/comments/c%203/images/i%2F4",
      { signal: controller.signal },
    );
  });

  it("deletes with expected_version as a query parameter and no body", () => {
    projectTodosApi.remove("p1", "t1", 4);

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/todos/t1?expected_version=4",
      { method: "DELETE" },
    );
  });

  it("posts an atomic bulk update with per-record versions", () => {
    projectTodosApi.bulk("p1", {
      items: [
        { todo_id: "t1", expected_version: 1 },
        { todo_id: "t2", expected_version: 2 },
      ],
      status: "done",
    });

    expect(request).toHaveBeenCalledWith("/projects/p1/todos/bulk", {
      method: "POST",
      body: JSON.stringify({
        items: [
          { todo_id: "t1", expected_version: 1 },
          { todo_id: "t2", expected_version: 2 },
        ],
        status: "done",
      }),
    });
  });

  it("exposes the contract caps", () => {
    expect(PROJECT_TODOS_PAGE_SIZE).toBeLessThanOrEqual(100);
    expect(PROJECT_TODOS_BULK_MAX).toBe(50);
  });

  it("normalizes both bulk response envelopes", () => {
    expect(normalizeTodoBulkResponse({ items: [todo] })).toEqual([todo]);
    expect(normalizeTodoBulkResponse([todo])).toEqual([todo]);
    expect(normalizeTodoBulkResponse(undefined)).toEqual([]);
    expect(normalizeTodoBulkResponse({ unexpected: true })).toEqual([]);
  });
});
