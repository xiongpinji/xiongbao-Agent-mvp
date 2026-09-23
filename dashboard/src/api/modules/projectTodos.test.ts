import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../request", () => ({ request }));

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
  status: "todo",
  creator_user_id: 1,
  assignee_user_id: null,
  version: 2,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_100,
};

beforeEach(() => {
  request.mockClear();
});

describe("projectTodosApi", () => {
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
