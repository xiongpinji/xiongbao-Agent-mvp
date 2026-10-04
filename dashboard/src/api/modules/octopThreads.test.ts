import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../request", () => ({ request }));
import { octopThreadsApi } from "./octopThreads";

beforeEach(() => request.mockClear());

describe("thread search and metadata", () => {
  it("preserves the existing no-query request", () => {
    octopThreadsApi.list("a /b", 21);
    expect(request).toHaveBeenCalledWith("/agents/a%20%2Fb/threads?limit=21");
  });

  it("encodes the raw query literally without changing the prefix", () => {
    const q = "  Straße %_\\\u0000  ";
    octopThreadsApi.list("a", 31, q);
    expect(request).toHaveBeenCalledWith(
      `/agents/a/threads?limit=31&q=${encodeURIComponent(q)}`,
    );
  });

  it("omits empty queries", () => {
    octopThreadsApi.list("a", 11, "");
    expect(request).toHaveBeenCalledWith("/agents/a/threads?limit=11");
  });

  it("reads one metadata row without a history or mutation request", () => {
    octopThreadsApi.metadata("a /b", "t /1");
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("/agents/a%20%2Fb/threads/t%20%2F1");
  });
});
