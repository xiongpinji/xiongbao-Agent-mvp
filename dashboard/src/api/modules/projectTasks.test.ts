import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../request", () => ({ request }));

import {
  PROJECT_TASKS_PAGE_SIZE,
  projectTasksApi,
  type ProjectTask,
} from "./projectTasks";

const task: ProjectTask = {
  project_id: "p1",
  thread_id: "t1",
  owner_user_id: 2,
  agent_id: "agent-1",
  title: "写周报",
  source: "manual",
  last_active: 1_700_000_100,
  created_at: 1_700_000_000,
};

beforeEach(() => {
  request.mockClear();
});

describe("projectTasksApi against the PS-05 ACL contract", () => {
  it("lists with encoded project id, literal search and default paging", () => {
    projectTasksApi.list("p 1/2", { q: "周报 100%_x" });

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(
      "/projects/p%201%2F2/tasks?q=%E5%91%A8%E6%8A%A5+100%25_x&limit=50&offset=0",
    );
  });

  it("omits blank q but keeps explicit paging", () => {
    projectTasksApi.list("p1", {
      q: "   ",
      limit: PROJECT_TASKS_PAGE_SIZE,
      offset: 50,
    });

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/tasks?limit=50&offset=50",
    );
  });

  it("reads a single owned task by encoded thread id", () => {
    projectTasksApi.get("p1", "t 1/2");

    expect(request).toHaveBeenCalledWith("/projects/p1/tasks/t%201%2F2");
  });

  it("links a thread with only {thread_id} — no actor, role or source", () => {
    projectTasksApi.link("p1", "t 1/2");

    expect(request).toHaveBeenCalledWith("/projects/p1/tasks/links", {
      method: "POST",
      body: JSON.stringify({ thread_id: "t 1/2" }),
    });
    const body = JSON.parse(request.mock.calls[0][1].body as string) as Record<
      string,
      unknown
    >;
    expect(Object.keys(body)).toEqual(["thread_id"]);
  });

  it("unlinks with DELETE and no body, preserving the conversation", () => {
    projectTasksApi.unlink("p1", "t1");

    expect(request).toHaveBeenCalledWith("/projects/p1/tasks/t1", {
      method: "DELETE",
    });
  });

  it("keeps the default page size within the contract cap", () => {
    expect(PROJECT_TASKS_PAGE_SIZE).toBeLessThanOrEqual(100);
    expect(PROJECT_TASKS_PAGE_SIZE).toBeGreaterThan(0);
  });

  it("exposes only the safe summary fields in its type surface", () => {
    const keys = Object.keys(task).sort();
    expect(keys).toEqual([
      "agent_id",
      "created_at",
      "last_active",
      "owner_user_id",
      "project_id",
      "source",
      "thread_id",
      "title",
    ]);
  });
});
