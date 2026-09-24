import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../request", () => ({ request }));

import {
  PROJECT_ACTIVITY_PAGE_SIZE,
  PROJECT_MESSAGE_MAX_LENGTH,
  projectActivityApi,
} from "./projectActivity";

beforeEach(() => {
  request.mockClear();
});

describe("projectActivityApi", () => {
  it("lists related events with the default page size and no cursor", () => {
    projectActivityApi.list("p 1/2", { scope: "related" });

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(
      "/projects/p%201%2F2/activity?scope=related&limit=20",
    );
  });

  it("sends only scope, limit and the opaque cursor for members scope", () => {
    projectActivityApi.list("p1", {
      scope: "members",
      cursor: "MTpubnM6MTI",
      limit: 50,
    });

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/activity?scope=members&limit=50&cursor=MTpubnM6MTI",
    );
  });

  it("omits a blank cursor so a refresh starts from the first page", () => {
    projectActivityApi.list("p1", {
      scope: "related",
      cursor: "  ",
      limit: 20,
    });

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/activity?scope=related&limit=20",
    );
  });

  it("posts only the message body without any actor or target fields", () => {
    projectActivityApi.postMessage("p 1/2", "  你好，项目  ");

    expect(request).toHaveBeenCalledWith("/projects/p%201%2F2/messages", {
      method: "POST",
      body: JSON.stringify({ body: "  你好，项目  " }),
    });
  });

  it("exposes the contract caps", () => {
    expect(PROJECT_ACTIVITY_PAGE_SIZE).toBe(20);
    expect(PROJECT_MESSAGE_MAX_LENGTH).toBe(4000);
  });
});
