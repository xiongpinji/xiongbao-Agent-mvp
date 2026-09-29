import { beforeEach, describe, expect, it, vi } from "vitest";
const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../request", () => ({ request }));
import {
  projectPlanViewsApi,
  type PlanQueryRequest,
  type PlanViewCreateBody,
} from "./projectPlanViews";
import {
  makePlanDefinition,
  makePlanQueryResponse,
  makePlanView,
} from "../../pages/Projects/plan/planView.testFixtures";

beforeEach(() => request.mockReset().mockResolvedValue({}));
describe("shared plan view authenticated HTTP wire", () => {
  it.each(["list", "get"] as const)(
    "%s encodes every ID and fixes GET while retaining AbortSignal",
    async (method) => {
      const controller = new AbortController();
      const options = {
        method: "DELETE",
        body: "untrusted",
        signal: controller.signal,
        headers: { "X-Test": "one" },
      };
      const result = makePlanView();
      request.mockResolvedValueOnce(result);
      const received =
        method === "get"
          ? await projectPlanViewsApi.get("p /?", "v /?", options)
          : await projectPlanViewsApi.list("p /?", options);
      expect(request).toHaveBeenCalledWith(
        `/projects/p%20%2F%3F/plan/views${
          method === "get" ? "/v%20%2F%3F" : ""
        }`,
        { ...options, method: "GET", body: undefined },
      );
      expect(received).toBe(result);
    },
  );
  it("create sends a full calendar definition and exact two revisions", async () => {
    const body = {
      expected_revision: 7,
      expected_catalog_revision: 11,
      name: "Shared calendar",
      type: "calendar",
      definition: makePlanDefinition("calendar"),
    } as PlanViewCreateBody;
    await projectPlanViewsApi.create("p1", body, {
      method: "DELETE",
      body: "wrong",
      cache: "no-store",
    });
    expect(request).toHaveBeenCalledWith("/projects/p1/plan/views", {
      cache: "no-store",
      method: "POST",
      body: JSON.stringify(body),
    });
  });
  it("update fixes PATCH and sends the captured version plus full definition", async () => {
    const body = {
      expected_version: 4,
      expected_catalog_revision: 9,
      definition: makePlanDefinition(),
    };
    const signal = new AbortController().signal;
    await projectPlanViewsApi.update("p1", "v /1", body, {
      method: "POST",
      body: "wrong",
      signal,
    });
    expect(request).toHaveBeenCalledWith("/projects/p1/plan/views/v%20%2F1", {
      signal,
      method: "PATCH",
      body: JSON.stringify(body),
    });
  });
  it("PUT order uses only view_ids and the collection revision", async () => {
    const body = { expected_revision: 8, view_ids: ["v2", "v1"] };
    await projectPlanViewsApi.order("p1", body);
    expect(request).toHaveBeenCalledWith("/projects/p1/plan/views/order", {
      method: "PUT",
      body: JSON.stringify(body),
    });
  });
  it("PUT default returns the actual revision/default envelope", async () => {
    const body = { expected_revision: 8, view_id: "v2" };
    const response = { revision: 9, default_view_id: "v2" };
    request.mockResolvedValueOnce(response);
    expect(await projectPlanViewsApi.setDefault("p1", body)).toBe(response);
    expect(request).toHaveBeenCalledWith("/projects/p1/plan/views/default", {
      method: "PUT",
      body: JSON.stringify(body),
    });
  });
  it.each(["archive", "restore"] as const)(
    "%s sends the original single-view version and encoded path",
    async (action) => {
      const body = { expected_version: 5 };
      const signal = new AbortController().signal;
      await projectPlanViewsApi[action]("p /1", "v /2", body, {
        signal,
        method: "PUT",
      });
      expect(request).toHaveBeenCalledWith(
        `/projects/p%20%2F1/plan/views/v%20%2F2/${action}`,
        { signal, method: "POST", body: JSON.stringify(body) },
      );
    },
  );
  it("query preserves opaque cursor, exact expected values, singular sort and nullable counts", async () => {
    const body: PlanQueryRequest = {
      view_id: "v1",
      expected_view_version: 3,
      expected_catalog_revision: 6,
      override_definition: {
        ...makePlanDefinition(),
        sort: [{ field: "title", direction: "asc" }],
      },
      group_key: { kind: "assignee", id: "12" },
      cursor: "opaque+/cursor",
      limit: 50,
    };
    const response = makePlanQueryResponse({
      total: 251,
      matched_total: 251,
      unscheduled_total: null,
    });
    request.mockResolvedValueOnce(response);
    const signal = new AbortController().signal;
    expect(
      await projectPlanViewsApi.query("p1", body, {
        signal,
        method: "GET",
        body: "wrong",
      }),
    ).toBe(response);
    expect(request).toHaveBeenCalledWith("/projects/p1/plan/query", {
      signal,
      method: "POST",
      body: JSON.stringify(body),
    });
    expect(
      JSON.parse(request.mock.calls[0][1].body).override_definition,
    ).toHaveProperty("sort");
    expect(
      JSON.parse(request.mock.calls[0][1].body).override_definition,
    ).not.toHaveProperty("sorts");
  });
});
