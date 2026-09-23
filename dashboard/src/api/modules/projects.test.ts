import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../request", () => ({ request }));

import { PROJECTS_PAGE_SIZE, projectsApi } from "./projects";

beforeEach(() => {
  request.mockClear();
});

describe("projectsApi", () => {
  it("lists with default paging and URL-encodes the search keyword", () => {
    projectsApi.list({ q: "团队 plan/x" });

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(
      "/projects?q=%E5%9B%A2%E9%98%9F+plan%2Fx&limit=20&offset=0",
    );
  });

  it("omits a blank q and honors explicit paging", () => {
    projectsApi.list({ q: "   ", limit: 5, offset: 10 });

    expect(request).toHaveBeenCalledWith("/projects?limit=5&offset=10");
    expect(PROJECTS_PAGE_SIZE).toBe(20);
  });

  it("creates a project via POST JSON", () => {
    projectsApi.create({
      name: "北斗计划",
      description: "跨部门交付",
      instructions: "按里程碑推进",
    });

    expect(request).toHaveBeenCalledWith("/projects", {
      method: "POST",
      body: JSON.stringify({
        name: "北斗计划",
        description: "跨部门交付",
        instructions: "按里程碑推进",
      }),
    });
  });

  it("URL-encodes the project id in detail, update and member routes", () => {
    projectsApi.get("p 1/2");
    projectsApi.update("p 1/2", { name: "renamed" });
    projectsApi.members("p 1/2");

    expect(request).toHaveBeenNthCalledWith(1, "/projects/p%201%2F2");
    expect(request).toHaveBeenNthCalledWith(2, "/projects/p%201%2F2", {
      method: "PATCH",
      body: JSON.stringify({ name: "renamed" }),
    });
    expect(request).toHaveBeenNthCalledWith(3, "/projects/p%201%2F2/members");
  });
});
