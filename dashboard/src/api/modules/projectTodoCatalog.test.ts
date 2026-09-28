import { beforeEach, describe, expect, it, vi } from "vitest";
const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../request", () => ({ request }));
import { projectTodoCatalogApi } from "./projectTodoCatalog";
beforeEach(() => request.mockReset());
describe("C1 actual catalog HTTP surface", () => {
  it("GET uses the authenticated request module and encoded project identity", () => {
    projectTodoCatalogApi.get("p 1/2");
    expect(request).toHaveBeenCalledWith("/projects/p%201%2F2/plan/catalog");
  });
  it.each([
    ["createPriority", "POST", "priorities"],
    ["createTag", "POST", "tags"],
  ] as const)(
    "%s persists the exact name/color/revision",
    (method, verb, suffix) => {
      const body = {
        expected_revision: 7,
        name: "项目原名",
        color: "purple" as const,
      };
      projectTodoCatalogApi[method]("p1", body);
      expect(request).toHaveBeenCalledWith(`/projects/p1/plan/${suffix}`, {
        method: verb,
        body: JSON.stringify(body),
      });
    },
  );
  it.each([
    ["updatePriority", "PATCH", "priorities", ""],
    ["updateTag", "PATCH", "tags", ""],
    ["archivePriority", "POST", "priorities", "/archive"],
    ["restorePriority", "POST", "priorities", "/restore"],
    ["archiveTag", "POST", "tags", "/archive"],
    ["restoreTag", "POST", "tags", "/restore"],
  ] as const)(
    "%s encodes the option id without invented data",
    (method, verb, kind, action) => {
      const body = {
        expected_revision: 8,
        ...(verb === "PATCH" ? { color: "red" as const } : {}),
      };
      projectTodoCatalogApi[method]("p1", "id /1", body);
      expect(request).toHaveBeenCalledWith(
        `/projects/p1/plan/${kind}/id%20%2F1${action}`,
        { method: verb, body: JSON.stringify(body) },
      );
    },
  );
  it("orders the complete active priority ids against an exact revision", () => {
    const body = { expected_revision: 9, priority_ids: ["b", "a"] };
    projectTodoCatalogApi.orderPriorities("p1", body);
    expect(request).toHaveBeenCalledWith("/projects/p1/plan/priorities/order", {
      method: "PUT",
      body: JSON.stringify(body),
    });
  });
});
