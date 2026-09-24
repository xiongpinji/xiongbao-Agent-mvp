import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../request", () => ({ request }));

import {
  PROJECT_TASK_MESSAGE_PAGE_SIZE,
  PROJECT_TASKS_PAGE_SIZE,
  projectTasksApi,
  type ProjectTask,
  type ProjectTaskMessagesResponse,
  type ProjectTaskShare,
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
  access: "owner",
  can_read_text: true,
};

const share: ProjectTaskShare = {
  user_id: 7,
  role: "reader",
  granted_at: 1_700_000_500,
  can_read_text: false,
};

beforeEach(() => {
  request.mockClear();
});

describe("projectTasksApi against the PS-05B-1 contract", () => {
  it("lists in the default own scope with encoded project id and literal search", () => {
    projectTasksApi.list("p 1/2", { q: "周报 100%_x" });

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(
      "/projects/p%201%2F2/tasks?scope=own&q=%E5%91%A8%E6%8A%A5+100%25_x&limit=50&offset=0",
    );
  });

  it("sends the selected shared/all scope instead of filtering locally", () => {
    projectTasksApi.list("p1", { scope: "shared", offset: 50 });
    expect(request).toHaveBeenLastCalledWith(
      "/projects/p1/tasks?scope=shared&limit=50&offset=50",
    );

    projectTasksApi.list("p1", { scope: "all", q: "   " });
    expect(request).toHaveBeenLastCalledWith(
      "/projects/p1/tasks?scope=all&limit=50&offset=0",
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

  it("lists active shares from the envelope with only user_id/role/granted_at/can_read_text", () => {
    projectTasksApi.shares("p1", "t 1/2");

    expect(request).toHaveBeenCalledWith("/projects/p1/tasks/t%201%2F2/shares");
    expect(Object.keys(share).sort()).toEqual([
      "can_read_text",
      "granted_at",
      "role",
      "user_id",
    ]);
    expect(share.can_read_text).toBe(false);
  });

  it("grants text through the separate route with an empty body", () => {
    projectTasksApi.grantText("p1", "t 1/2", 7);

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/tasks/t%201%2F2/shares/7/text",
      { method: "POST" },
    );
    expect(request.mock.calls[0][1]).not.toHaveProperty("body");
  });

  it("revokes text separately from the card share", () => {
    projectTasksApi.revokeText("p1", "t1", 7);

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/tasks/t1/shares/7/text",
      {
        method: "DELETE",
      },
    );
    expect(request.mock.calls[0][1]).not.toHaveProperty("body");
  });

  it("reads a text page with the contract default limit and optional before_seq cursor", () => {
    projectTasksApi.messages("p1", "t1");

    expect(request).toHaveBeenLastCalledWith(
      "/projects/p1/tasks/t1/messages?limit=50",
    );

    projectTasksApi.messages("p1", "t 1/2", { limit: 10, beforeSeq: 11 });
    expect(request).toHaveBeenLastCalledWith(
      "/projects/p1/tasks/t%201%2F2/messages?limit=10&before_seq=11",
    );
  });

  it("keeps the text page DTO to the contract fields", () => {
    const response: ProjectTaskMessagesResponse = {
      status: "ready",
      items: [
        {
          seq: 9,
          role: "user",
          text: "正文",
          created_at: 1_700_000_700,
          truncated: false,
        },
      ],
      has_more: false,
      next_before_seq: null,
    };

    expect(Object.keys(response).sort()).toEqual([
      "has_more",
      "items",
      "next_before_seq",
      "status",
    ]);
    expect(Object.keys(response.items[0]).sort()).toEqual([
      "created_at",
      "role",
      "seq",
      "text",
      "truncated",
    ]);
  });

  it("grants a reader share by numeric user_id only", () => {
    projectTasksApi.share("p1", "t1", 7);

    expect(request).toHaveBeenCalledWith("/projects/p1/tasks/t1/shares", {
      method: "POST",
      body: JSON.stringify({ user_id: 7 }),
    });
    const body = JSON.parse(request.mock.calls[0][1].body as string) as Record<
      string,
      unknown
    >;
    expect(Object.keys(body)).toEqual(["user_id"]);
  });

  it("revokes one grantee with DELETE and no body", () => {
    projectTasksApi.revoke("p1", "t1", 7);

    expect(request).toHaveBeenCalledWith("/projects/p1/tasks/t1/shares/7", {
      method: "DELETE",
    });
  });

  it("keeps the default page sizes within the contract cap", () => {
    expect(PROJECT_TASKS_PAGE_SIZE).toBeLessThanOrEqual(100);
    expect(PROJECT_TASKS_PAGE_SIZE).toBeGreaterThan(0);
    expect(PROJECT_TASK_MESSAGE_PAGE_SIZE).toBe(50);
  });

  it("exposes only the safe summary fields plus access and can_read_text", () => {
    const keys = Object.keys(task).sort();
    expect(keys).toEqual([
      "access",
      "agent_id",
      "can_read_text",
      "created_at",
      "last_active",
      "owner_user_id",
      "project_id",
      "source",
      "thread_id",
      "title",
    ]);
    expect(task.access).toBe("owner");
    expect(task.can_read_text).toBe(true);
  });
});
