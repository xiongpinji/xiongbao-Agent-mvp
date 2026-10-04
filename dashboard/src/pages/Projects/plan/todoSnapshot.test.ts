import { describe, expect, it, vi } from "vitest";
import {
  compareTodoSnapshot,
  uniqueTodoSnapshots,
  validTodoSnapshot,
} from "./todoSnapshot";
import { makePlanTodo } from "./planView.testFixtures";

describe("public todo snapshot partial order", () => {
  const accepted = makePlanTodo({
    display_revision: 3,
    catalog_revision: 2,
    assignee_user_id: null,
  });
  it.each([
    [3, 2, "accept"],
    [4, 2, "accept"],
    [3, 3, "accept"],
    [2, 2, "keep"],
    [3, 1, "keep"],
    [2, 1, "keep"],
    [4, 1, "incomparable"],
    [2, 3, "incomparable"],
  ] as const)(
    "compares display %s/catalog %s independently of body",
    (display_revision, catalog_revision, order) => {
      expect(
        compareTodoSnapshot(
          accepted,
          makePlanTodo({ display_revision, catalog_revision }),
        ),
      ).toBe(order);
    },
  );
  it.each([
    undefined,
    0,
    -1,
    1.5,
    NaN,
    Infinity,
    Number.MAX_SAFE_INTEGER + 1,
    true,
  ])("rejects invalid display %s", (display_revision) => {
    const incoming = { ...accepted, display_revision };
    expect(validTodoSnapshot(incoming)).toBe(false);
    expect(compareTodoSnapshot(accepted, incoming as typeof accepted)).toBe(
      "invalid",
    );
  });
  it("rejects incomplete data and different identity", () => {
    expect(validTodoSnapshot({ ...accepted, title: undefined })).toBe(false);
    expect(
      compareTodoSnapshot(accepted, makePlanTodo({ project_id: "other" })),
    ).toBe("invalid");
    expect(
      compareTodoSnapshot(accepted, makePlanTodo({ todo_id: "other" })),
    ).toBe("invalid");
  });
  it("pure dedup retains the accepted projection for incomparable/old inputs without I/O", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const old = makePlanTodo({
        display_revision: 2,
        catalog_revision: 2,
        assignee_user_id: 7,
      });
      const incomparable = makePlanTodo({
        display_revision: 2,
        catalog_revision: 3,
      });
      expect(uniqueTodoSnapshots([accepted, old, incomparable])).toEqual([
        accepted,
      ]);
      expect(uniqueTodoSnapshots([old, accepted])).toEqual([accepted]);
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
});
