import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearSelectedViewId,
  readSelectedViewId,
  selectionKey,
  writeSelectedViewId,
} from "./planViewSelection";
import { makePlanView } from "./planView.testFixtures";

const views = [
  makePlanView({ view_id: "v1" }),
  makePlanView({ view_id: "v2", position: 1 }),
  makePlanView({ view_id: "old", archived_at: 1 }),
  makePlanView({ view_id: "outside", project_id: "p2" }),
];
beforeEach(() => localStorage.clear());
describe("account/project validated view ID selection", () => {
  it("uses an unambiguous account/project key", () => {
    expect(selectionKey(1, "a:b")).not.toBe(selectionKey(1, "a%3Ab"));
    expect(selectionKey(1, "p1")).not.toBe(selectionKey(2, "p1"));
  });
  it("reads only an active same-project saved ID", () => {
    localStorage.setItem(selectionKey(1, "p1"), "v2");
    expect(readSelectedViewId(localStorage, 1, "p1", views, "v1")).toBe("v2");
  });
  it.each([
    "old",
    "outside",
    "deleted",
    '{"view_id":"v2","definition":{"filters":[]}}',
  ])("rejects %s and chooses a validated project default", (saved) => {
    localStorage.setItem(selectionKey(1, "p1"), saved);
    expect(readSelectedViewId(localStorage, 1, "p1", views, "v1")).toBe("v1");
  });
  it("falls back to the first valid active server view when the default is unavailable", () => {
    expect(
      readSelectedViewId(
        localStorage,
        1,
        "p1",
        [...views].reverse(),
        "missing",
      ),
    ).toBe("v1");
    expect(
      readSelectedViewId(
        localStorage,
        1,
        "p1",
        views.filter(
          (view) => view.archived_at !== null || view.project_id !== "p1",
        ),
        "old",
      ),
    ).toBeNull();
  });
  it("stores only a validated ID and isolates other accounts/projects", () => {
    writeSelectedViewId(localStorage, 1, "p1", "v2", views);
    expect(localStorage.getItem(selectionKey(1, "p1"))).toBe("v2");
    expect(localStorage.length).toBe(1);
    expect(readSelectedViewId(localStorage, 2, "p1", views, "v1")).toBe("v1");
    expect(readSelectedViewId(localStorage, 1, "p2", views, "outside")).toBe(
      "outside",
    );
    writeSelectedViewId(localStorage, 1, "p1", "old", views);
    expect(localStorage.getItem(selectionKey(1, "p1"))).toBe("v2");
  });
  it("clears only the specific account/project ID", () => {
    localStorage.setItem(selectionKey(1, "p1"), "v1");
    localStorage.setItem(selectionKey(2, "p1"), "v2");
    clearSelectedViewId(localStorage, 1, "p1");
    expect(localStorage.getItem(selectionKey(1, "p1"))).toBeNull();
    expect(localStorage.getItem(selectionKey(2, "p1"))).toBe("v2");
  });
  it("unavailable browser storage does not block a server-validated fallback", () => {
    const storage = {
      getItem: vi.fn(() => {
        throw new Error("blocked");
      }),
      setItem: vi.fn(() => {
        throw new Error("blocked");
      }),
      removeItem: vi.fn(() => {
        throw new Error("blocked");
      }),
    };
    expect(readSelectedViewId(storage, 1, "p1", views, "v1")).toBe("v1");
    expect(() =>
      writeSelectedViewId(storage, 1, "p1", "v1", views),
    ).not.toThrow();
    expect(() => clearSelectedViewId(storage, 1, "p1")).not.toThrow();
  });
});
