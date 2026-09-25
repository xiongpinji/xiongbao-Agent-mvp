import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../request", () => ({ request }));

import { PROJECT_EXPERTS_LIMIT, projectExpertsApi } from "./projectExperts";

beforeEach(() => {
  request.mockClear();
});

describe("projectExpertsApi", () => {
  it("GETs the project expert list with an encoded project id", () => {
    projectExpertsApi.list("p 1/2");

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("/projects/p%201%2F2/experts");
  });

  it("PUTs expected_revision with the exact ordered agent_ids", () => {
    projectExpertsApi.set("p1", 3, ["a2", "a1"]);

    expect(request).toHaveBeenCalledWith("/projects/p1/experts", {
      method: "PUT",
      body: JSON.stringify({ expected_revision: 3, agent_ids: ["a2", "a1"] }),
    });
  });

  it("exposes the server-side 20 expert cap", () => {
    expect(PROJECT_EXPERTS_LIMIT).toBe(20);
  });
});
