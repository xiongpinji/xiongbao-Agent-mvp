import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
const { query, getProject, getTodo, getCatalog } = vi.hoisted(() => ({
  query: vi.fn(),
  getProject: vi.fn(),
  getTodo: vi.fn(),
  getCatalog: vi.fn(),
}));
vi.mock("../../../api/modules/projectPlanViews", async (original) => ({
  ...(await original<typeof import("../../../api/modules/projectPlanViews")>()),
  projectPlanViewsApi: { query },
}));
vi.mock("../../../api/modules/projects", () => ({
  projectsApi: { get: getProject },
}));
vi.mock("../../../api/modules/projectTodos", async (original) => ({
  ...(await original<typeof import("../../../api/modules/projectTodos")>()),
  projectTodosApi: { get: getTodo },
}));
vi.mock("../../../api/modules/projectTodoCatalog", async (original) => ({
  ...(await original<
    typeof import("../../../api/modules/projectTodoCatalog")
  >()),
  projectTodoCatalogApi: { get: getCatalog },
}));
import {
  useProjectPlanQuery,
  type ProjectPlanQueryOptions,
} from "./useProjectPlanQuery";
import {
  deferred,
  makePlanDefinition,
  makePlanQueryResponse,
  makePlanTodo,
  makePlanCatalog,
  makePlanView,
} from "./planView.testFixtures";
import type {
  PlanQueryRequest,
  PlanQueryResponse,
} from "../../../api/modules/projectPlanViews";

const error = (status: number, reason?: string, condition_indices?: number[]) =>
  new Error(
    `Request failed: ${status} - ${JSON.stringify({
      error: {
        code: status === 404 ? "NOT_FOUND" : "INVITE_INVALID",
        details: { reason, condition_indices },
      },
    })}`,
  );
const options = (
  extra: Partial<ProjectPlanQueryOptions> = {},
): ProjectPlanQueryOptions => ({
  accountId: 1,
  projectId: "p1",
  view: makePlanView(),
  catalogRevision: 1,
  window: null,
  onProjectAccessLost: vi.fn(),
  onViewUnavailable: vi.fn(),
  ...extra,
});
beforeEach(() => {
  query.mockReset().mockResolvedValue(makePlanQueryResponse());
  getProject.mockReset().mockResolvedValue({ project_id: "p1" });
  getTodo.mockReset().mockResolvedValue(makePlanTodo());
  getCatalog.mockReset().mockResolvedValue(makePlanCatalog());
});
describe("bounded query lanes and original operation scopes", () => {
  it("D1 accepts a real display-only assignee projection with the same body token", async () => {
    const assigned = {
      ...makePlanTodo({ assignee_user_id: 7 }),
      display_revision: 1,
    };
    query.mockResolvedValue(makePlanQueryResponse({ items: [assigned] }));
    const { result } = renderHook(() => useProjectPlanQuery(options()));
    await waitFor(() =>
      expect(result.current.lanes[0]?.items[0].assignee_user_id).toBe(7),
    );
    await act(async () => {
      await result.current.acceptTodo(
        result.current.captureOperation("external-delete")!,
        {
          ...assigned,
          assignee_user_id: null,
          display_revision: 2,
        },
      );
    });
    expect(result.current.lanes[0].items[0]).toMatchObject({
      version: assigned.version,
      display_revision: 2,
      assignee_user_id: null,
    });
  });
  it("D1 resolves an incomparable pair with exactly one current todo/catalog read", async () => {
    const accepted = makePlanTodo({
      display_revision: 3,
      assignee_user_id: null,
    });
    query.mockResolvedValue(makePlanQueryResponse({ items: [accepted] }));
    const refreshed = { ...accepted, catalog_revision: 2 };
    getTodo.mockResolvedValue(refreshed);
    getCatalog.mockResolvedValue(makePlanCatalog({ revision: 2 }));
    const onCatalogSnapshot = vi.fn();
    const { result } = renderHook(() =>
      useProjectPlanQuery(options({ onCatalogSnapshot })),
    );
    await waitFor(() =>
      expect(result.current.lanes[0]?.items[0]).toEqual(accepted),
    );
    await act(async () => {
      await result.current.acceptTodo(
        result.current.captureOperation("compare")!,
        makePlanTodo({
          display_revision: 2,
          catalog_revision: 2,
          assignee_user_id: 7,
        }),
      );
    });
    expect(result.current.lanes[0].items[0]).toEqual(refreshed);
    expect(getTodo).toHaveBeenCalledTimes(1);
    expect(getCatalog).toHaveBeenCalledTimes(1);
    expect(getTodo.mock.calls[0][2].signal).toBe(
      getCatalog.mock.calls[0][1].signal,
    );
    expect(onCatalogSnapshot).toHaveBeenCalledWith(
      expect.objectContaining({ revision: 2 }),
    );
  });
  it("D1 preserves the accepted projection after a still-incompatible refresh without recursion", async () => {
    const accepted = makePlanTodo({
      display_revision: 3,
      assignee_user_id: null,
    });
    query.mockResolvedValue(makePlanQueryResponse({ items: [accepted] }));
    getTodo.mockResolvedValue(
      makePlanTodo({ display_revision: 4, catalog_revision: 1 }),
    );
    getCatalog.mockResolvedValue(makePlanCatalog());
    const { result } = renderHook(() => useProjectPlanQuery(options()));
    await waitFor(() =>
      expect(result.current.lanes[0]?.items[0]).toEqual(accepted),
    );
    await act(async () => {
      expect(
        await result.current.acceptTodo(
          result.current.captureOperation("compare")!,
          makePlanTodo({ display_revision: 2, catalog_revision: 2 }),
        ),
      ).toBe(false);
    });
    expect(result.current.lanes[0].items[0]).toEqual(accepted);
    expect(result.current.errorKey).toBe("projects.planViews.failed");
    expect(getTodo).toHaveBeenCalledTimes(1);
    expect(getCatalog).toHaveBeenCalledTimes(1);
  });
  it.each(["project ABA", "unmount", "replacement operation"])(
    "D1 aborts a bounded snapshot refresh on %s",
    async (cancel) => {
      const accepted = makePlanTodo({
        display_revision: 3,
        assignee_user_id: null,
      });
      query.mockImplementation((p: string) =>
        Promise.resolve(
          makePlanQueryResponse({ items: [{ ...accepted, project_id: p }] }),
        ),
      );
      const pending = deferred<ReturnType<typeof makePlanTodo>>();
      getTodo.mockReturnValue(pending.promise);
      getCatalog.mockResolvedValue(makePlanCatalog({ revision: 2 }));
      const onCatalogSnapshot = vi.fn();
      const { result, rerender, unmount } = renderHook(
        ({ p }) =>
          useProjectPlanQuery(
            options({
              projectId: p,
              view: makePlanView({ project_id: p }),
              onCatalogSnapshot,
            }),
          ),
        { initialProps: { p: "p1" } },
      );
      await waitFor(() =>
        expect(result.current.lanes[0]?.items[0]).toEqual(accepted),
      );
      let operation!: Promise<boolean>;
      act(() => {
        operation = result.current.acceptTodo(
          result.current.captureOperation("compare")!,
          makePlanTodo({ display_revision: 2, catalog_revision: 2 }),
        );
      });
      await waitFor(() => expect(getTodo).toHaveBeenCalledTimes(1));
      const signal = getTodo.mock.calls[0][2].signal;
      if (cancel === "project ABA") {
        rerender({ p: "p2" });
        rerender({ p: "p1" });
      } else if (cancel === "unmount") unmount();
      else
        act(() => {
          result.current.captureOperation("compare");
        });
      expect(signal.aborted).toBe(true);
      await act(async () => {
        pending.resolve({ ...accepted, catalog_revision: 2 });
        expect(await operation).toBe(false);
      });
      expect(onCatalogSnapshot).not.toHaveBeenCalled();
      if (cancel !== "unmount")
        expect(result.current.getKnownTodo("todo1")?.catalog_revision).toBe(1);
    },
  );
  it("D1 rejects malformed query snapshots and keeps the legal current row", async () => {
    const { result } = renderHook(() => useProjectPlanQuery(options()));
    await waitFor(() => expect(result.current.lanes[0]?.items).toHaveLength(1));
    const accepted = result.current.lanes[0].items[0];
    query.mockResolvedValue(
      makePlanQueryResponse({
        items: [
          {
            ...accepted,
            display_revision: undefined,
          } as unknown as typeof accepted,
        ],
      }),
    );
    await act(async () => {
      expect(await result.current.loadFirst("all")).toBe(false);
    });
    expect(result.current.lanes[0].items[0]).toEqual(accepted);
    expect(getTodo).not.toHaveBeenCalled();
    expect(getCatalog).not.toHaveBeenCalled();
  });
  it("uses server complete counts beyond 200 and requests only a bounded first page", async () => {
    query.mockResolvedValue(
      makePlanQueryResponse({
        total: 251,
        matched_total: 251,
        items: [makePlanTodo()],
        next_cursor: "cursor-a",
      }),
    );
    const { result } = renderHook(() => useProjectPlanQuery(options()));
    await waitFor(() => expect(result.current.total).toBe(251));
    expect(result.current.lanes[0].serverCount).toBe(251);
    expect(result.current.lanes[0].items).toHaveLength(1);
    expect(query).toHaveBeenCalledTimes(1);
    expect(query.mock.calls[0][1]).toEqual({
      view_id: "view1",
      expected_view_version: 1,
      expected_catalog_revision: 1,
      limit: 50,
    });
    expect(query.mock.calls[0][2].signal).toBeInstanceOf(AbortSignal);
  });
  it("creates group lanes from SQL counts, loads only requested groups and permits distinct group fingerprints", async () => {
    const groups = [
      { key: { kind: "status" as const, id: "todo" }, count: 220 },
      { key: { kind: "status" as const, id: "done" }, count: 31 },
    ];
    query.mockImplementation((_project: string, body: PlanQueryRequest) =>
      Promise.resolve(
        makePlanQueryResponse({
          groups,
          total: 251,
          matched_total: 251,
          items: body.group_key
            ? [
                makePlanTodo({
                  todo_id: body.cursor ? "todo2" : body.group_key.id!,
                }),
              ]
            : [],
          next_cursor: body.group_key && !body.cursor ? "next" : null,
          query_fingerprint: `fp-${body.group_key?.id ?? "counts"}`,
        }),
      ),
    );
    const { result } = renderHook(() =>
      useProjectPlanQuery(options({ view: makePlanView({ type: "board" }) })),
    );
    await waitFor(() => expect(result.current.lanes).toHaveLength(2));
    expect(query).toHaveBeenCalledTimes(1);
    expect(query.mock.calls[0][1].limit).toBe(1);
    expect(result.current.lanes.map((lane) => lane.items.length)).toEqual([
      0, 0,
    ]);
    const first = result.current.lanes[0].laneId,
      second = result.current.lanes[1].laneId;
    await act(async () => {
      expect(await result.current.loadFirst(first)).toBe(true);
    });
    await act(async () => {
      expect(await result.current.loadFirst(second)).toBe(true);
    });
    await act(async () => {
      expect(await result.current.loadMore(first)).toBe(true);
    });
    expect(result.current.lanes[0].items).toHaveLength(2);
    expect(result.current.lanes[0].serverCount).toBe(220);
    expect(result.current.lanes[0].paused).toBe(false);
    expect(query.mock.calls[3][1]).toMatchObject({
      group_key: { kind: "status", id: "todo" },
      cursor: "next",
      limit: 50,
    });
  });
  it("keeps scheduled and unscheduled cursors and counts independent", async () => {
    query.mockImplementation((_project: string, body: PlanQueryRequest) =>
      Promise.resolve(
        makePlanQueryResponse({
          total: 251,
          matched_total: body.bucket === "scheduled" ? 200 : 51,
          unscheduled_total: 51,
          next_cursor: body.cursor ? null : `${body.bucket}-cursor`,
          query_fingerprint: `fp-${body.bucket}`,
        }),
      ),
    );
    const window = { start_date: "2026-09-01", end_date: "2026-10-12" };
    const { result } = renderHook(() =>
      useProjectPlanQuery(
        options({ view: makePlanView({ type: "calendar" }), window }),
      ),
    );
    await waitFor(() =>
      expect(result.current.lanes.every((lane) => lane.nextCursor)).toBe(true),
    );
    expect(result.current.lanes).toHaveLength(2);
    expect(result.current.matchedTotal).toBe(200);
    expect(result.current.unscheduledTotal).toBe(51);
    await act(async () => {
      await result.current.loadMore("unscheduled");
    });
    expect(query.mock.calls.at(-1)![1]).toMatchObject({
      bucket: "unscheduled",
      cursor: "unscheduled-cursor",
      window,
    });
    expect(
      result.current.lanes.find((lane) => lane.laneId === "scheduled")
        ?.nextCursor,
    ).toBe("scheduled-cursor");
  });
  it("refreshes all previously loaded pages with new cursors and preserves page depth", async () => {
    let round = 0;
    query.mockImplementation((_project: string, body: PlanQueryRequest) => {
      if (!body.cursor) round += 1;
      const page = body.cursor?.endsWith("2") ? 3 : body.cursor ? 2 : 1;
      return Promise.resolve(
        makePlanQueryResponse({
          items: [makePlanTodo({ todo_id: `r${round}-p${page}` })],
          total: 251,
          matched_total: 251,
          next_cursor: page < 3 ? `r${round}-cursor-${page}` : null,
        }),
      );
    });
    const { result } = renderHook(() => useProjectPlanQuery(options()));
    await waitFor(() => expect(result.current.lanes[0]?.items).toHaveLength(1));
    await act(async () => {
      await result.current.loadMore("all");
      await result.current.loadMore("all");
    });
    expect(result.current.lanes[0].items).toHaveLength(3);
    await act(async () => {
      expect(await result.current.refresh()).toBe(true);
    });
    expect(result.current.lanes[0].items.map((todo) => todo.todo_id)).toEqual([
      "r2-p1",
      "r2-p2",
      "r2-p3",
    ]);
    expect(query.mock.calls.slice(-3).map((call) => call[1].cursor)).toEqual([
      undefined,
      "r2-cursor-1",
      "r2-cursor-2",
    ]);
  });
  it("accepts the PATCH high water before a late GET and never downgrades its version/catalog", async () => {
    const pending = deferred<PlanQueryResponse>();
    const { result } = renderHook(() => useProjectPlanQuery(options()));
    await waitFor(() => expect(result.current.lanes[0]?.items).toHaveLength(1));
    query.mockReturnValueOnce(pending.promise);
    let reload!: Promise<boolean>;
    act(() => {
      reload = result.current.loadFirst("all");
    });
    act(() => {
      const scope = result.current.captureOperation("patch")!;
      result.current.acceptTodo(
        scope,
        makePlanTodo({
          version: 3,
          catalog_revision: 4,
          title: "Accepted patch",
        }),
      );
    });
    await act(async () => {
      pending.resolve(
        makePlanQueryResponse({
          items: [
            makePlanTodo({
              version: 2,
              catalog_revision: 1,
              title: "Late GET",
            }),
          ],
        }),
      );
      await reload;
    });
    expect(result.current.lanes[0].items[0]).toMatchObject({
      version: 3,
      catalog_revision: 4,
      title: "Accepted patch",
    });
  });
  it("invalidates original scopes on project ABA and aborts the old query", async () => {
    const pending = deferred<PlanQueryResponse>();
    query.mockReturnValueOnce(pending.promise);
    const { result, rerender } = renderHook(
      ({ p }) =>
        useProjectPlanQuery(
          options({ projectId: p, view: makePlanView({ project_id: p }) }),
        ),
      { initialProps: { p: "p1" } },
    );
    const scope = result.current.captureOperation("edit")!;
    const oldSignal = query.mock.calls[0][2].signal;
    rerender({ p: "p2" });
    rerender({ p: "p1" });
    await waitFor(() =>
      expect(result.current.lanes[0]?.items[0].todo_id).toBe("todo1"),
    );
    expect(oldSignal.aborted).toBe(true);
    expect(result.current.isCurrentOperation(scope)).toBe(false);
    await act(async () => {
      pending.resolve(
        makePlanQueryResponse({
          items: [makePlanTodo({ title: "Old private title" })],
        }),
      );
      await pending.promise;
    });
    expect(result.current.lanes[0].items[0].title).not.toBe(
      "Old private title",
    );
  });
  it("ignores old catch/finally in a newer same-lane operation", async () => {
    const { result } = renderHook(() => useProjectPlanQuery(options()));
    await waitFor(() => expect(result.current.lanes[0]?.items).toHaveLength(1));
    const old = deferred<PlanQueryResponse>(),
      newer = deferred<PlanQueryResponse>();
    query.mockReturnValueOnce(old.promise).mockReturnValueOnce(newer.promise);
    let first!: Promise<boolean>, second!: Promise<boolean>;
    act(() => {
      first = result.current.loadFirst("all");
      second = result.current.loadFirst("all");
    });
    await act(async () => {
      old.reject(error(403));
      await first;
    });
    expect(result.current.lanes[0].loadingFirst).toBe(true);
    expect(result.current.lanes[0].errorKey).toBeNull();
    await act(async () => {
      newer.resolve(
        makePlanQueryResponse({
          items: [makePlanTodo({ title: "New request", version: 2 })],
        }),
      );
      await second;
    });
    expect(result.current.lanes[0].loadingFirst).toBe(false);
    expect(result.current.lanes[0].items[0].title).toBe("New request");
  });
  it("pauses query_changed pagination/date writes while retaining temporary settings until explicit refresh", async () => {
    query
      .mockResolvedValueOnce(
        makePlanQueryResponse({ next_cursor: "old-cursor" }),
      )
      .mockRejectedValueOnce(error(409, "query_changed"));
    const overrideDefinition = {
      ...makePlanDefinition(),
      filters: [
        {
          field: "title" as const,
          op: "contains" as const,
          value: "Draft search",
        },
      ],
    };
    const { result } = renderHook(() =>
      useProjectPlanQuery(options({ overrideDefinition })),
    );
    await waitFor(() =>
      expect(result.current.lanes[0]?.nextCursor).toBe("old-cursor"),
    );
    await act(async () => {
      expect(await result.current.loadMore("all")).toBe(false);
    });
    expect(result.current.paused).toBe(true);
    expect(result.current.serverToday).toBeNull();
    expect(result.current.lanes[0].items).toHaveLength(1);
    expect(await result.current.loadMore("all")).toBe(false);
    query.mockResolvedValueOnce(makePlanQueryResponse());
    await act(async () => {
      expect(await result.current.refresh()).toBe(true);
    });
    expect(query.mock.calls.at(-1)![1].override_definition).toEqual(
      overrideDefinition,
    );
    expect(result.current.paused).toBe(false);
  });
  it("reports only valid lost-assignee condition indices for explicit repair", async () => {
    query.mockRejectedValue(
      error(409, "filter_reference_unavailable", [0, 99, -1]),
    );
    const overrideDefinition = {
      ...makePlanDefinition(),
      filters: [
        { field: "assignee" as const, op: "in" as const, values: [999] },
      ],
    };
    const { result } = renderHook(() =>
      useProjectPlanQuery(options({ overrideDefinition })),
    );
    await waitFor(() =>
      expect(result.current.unavailableFilterIndices).toEqual([0]),
    );
    expect(result.current.paused).toBe(true);
  });
  it("rechecks project read before reporting a view 404 or clearing project private state", async () => {
    query.mockRejectedValue(error(404));
    const opts = options();
    const { unmount } = renderHook(() => useProjectPlanQuery(opts));
    await waitFor(() =>
      expect(opts.onViewUnavailable).toHaveBeenCalledTimes(1),
    );
    expect(getProject).toHaveBeenCalledWith("p1");
    expect(opts.onProjectAccessLost).not.toHaveBeenCalled();
    unmount();
    getProject.mockRejectedValue(error(404));
    const lost = options();
    renderHook(() => useProjectPlanQuery(lost));
    await waitFor(() =>
      expect(lost.onProjectAccessLost).toHaveBeenCalledTimes(1),
    );
  });
  it("refresh returns false when the context leaves and returns during its requests", async () => {
    const { result, rerender } = renderHook(
      ({ accountId }) => useProjectPlanQuery(options({ accountId })),
      { initialProps: { accountId: 1 } },
    );
    await waitFor(() => expect(result.current.lanes[0]?.items).toHaveLength(1));
    const pending = deferred<PlanQueryResponse>();
    query.mockReturnValueOnce(pending.promise);
    let refresh!: Promise<boolean>;
    act(() => {
      refresh = result.current.refresh();
    });
    rerender({ accountId: 2 });
    rerender({ accountId: 1 });
    await act(async () => {
      pending.resolve(makePlanQueryResponse());
      expect(await refresh).toBe(false);
    });
  });
  it("never accepts a stale unmounted success/error callback", async () => {
    const pending = deferred<PlanQueryResponse>();
    query.mockReturnValueOnce(pending.promise);
    const opts = options();
    const { result, unmount } = renderHook(() => useProjectPlanQuery(opts));
    const scope = result.current.captureOperation("detail")!;
    unmount();
    await act(async () => {
      pending.reject(error(404));
      await pending.promise.catch(() => {});
    });
    expect(result.current.isCurrentOperation(scope)).toBe(false);
    expect(opts.onProjectAccessLost).not.toHaveBeenCalled();
    expect(getProject).not.toHaveBeenCalled();
  });
  it("keeps logical lifetime across refresh/filter changes but advances it for active-view ABA", async () => {
    query.mockImplementation((_project: string, body: PlanQueryRequest) =>
      Promise.resolve(makePlanQueryResponse({ view_id: body.view_id })),
    );
    const { result, rerender } = renderHook(
      ({ viewId, filter }) =>
        useProjectPlanQuery(
          options({
            view: makePlanView({ view_id: viewId }),
            overrideDefinition: filter
              ? {
                  ...makePlanDefinition(),
                  filters: [{ field: "title", op: "contains", value: filter }],
                }
              : undefined,
          }),
        ),
      { initialProps: { viewId: "view1", filter: "" } },
    );
    await waitFor(() => expect(result.current.lanes[0]?.items).toHaveLength(1));
    const original = result.current.captureOperation("editor")!;
    await act(async () => {
      await result.current.refresh();
    });
    const refreshed = result.current.captureOperation("editor")!;
    expect(refreshed.lifetime).toBe(original.lifetime);
    expect(refreshed.queryGeneration).toBeGreaterThan(original.queryGeneration);
    await act(async () => {
      rerender({ viewId: "view1", filter: "new" });
    });
    const filtered = result.current.captureOperation("editor")!;
    expect(filtered.lifetime).toBe(original.lifetime);
    expect(result.current.isCurrentOperation(refreshed)).toBe(false);
    await act(async () => {
      rerender({ viewId: "view2", filter: "" });
    });
    await act(async () => {
      rerender({ viewId: "view1", filter: "" });
    });
    const returned = result.current.captureOperation("editor")!;
    expect(returned.lifetime).toBeGreaterThan(original.lifetime);
    expect(result.current.isCurrentOperation(original)).toBe(false);
  });
  it("refreshes every loaded grouped page with fresh cursors without loading untouched groups", async () => {
    let round = 0;
    const groups = ["todo", "done", "in_progress"].map((id) => ({
      key: { kind: "status" as const, id },
      count: 101,
    }));
    query.mockImplementation((_project: string, body: PlanQueryRequest) => {
      if (!body.group_key) round += 1;
      const id = body.group_key?.id ?? "counts",
        page = body.cursor ? 2 : 1;
      return Promise.resolve(
        makePlanQueryResponse({
          groups,
          total: 303,
          matched_total: 303,
          items: body.group_key
            ? [makePlanTodo({ todo_id: `${round}-${id}-${page}` })]
            : [],
          query_fingerprint: `fp-${round}-${id}`,
          next_cursor:
            body.group_key && !body.cursor ? `cursor-${round}-${id}` : null,
        }),
      );
    });
    const { result } = renderHook(() =>
      useProjectPlanQuery(options({ view: makePlanView({ type: "board" }) })),
    );
    await waitFor(() => expect(result.current.lanes).toHaveLength(3));
    const a = result.current.lanes[0].laneId,
      b = result.current.lanes[1].laneId;
    await act(async () => {
      await result.current.loadFirst(a);
      await result.current.loadMore(a);
      await result.current.loadFirst(b);
    });
    const callsBefore = query.mock.calls.length;
    await act(async () => {
      expect(await result.current.refresh()).toBe(true);
    });
    const calls = query.mock.calls.slice(callsBefore).map((call) => call[1]);
    expect(calls).toHaveLength(4);
    expect(calls.map((body) => body.group_key?.id)).not.toContain(
      "in_progress",
    );
    expect(calls.find((body) => body.cursor)?.cursor).toBe("cursor-2-todo");
    expect(result.current.lanes.map((lane) => lane.items.length)).toEqual([
      2, 1, 0,
    ]);
    expect(result.current.total).toBe(303);
  });
  it("a sibling date lane late response cannot clear a query_changed pause", async () => {
    query.mockImplementation((_project: string, body: PlanQueryRequest) =>
      Promise.resolve(
        makePlanQueryResponse({
          matched_total: 1,
          unscheduled_total: 1,
          next_cursor: `${body.bucket}-next`,
        }),
      ),
    );
    const { result } = renderHook(() =>
      useProjectPlanQuery(
        options({
          view: makePlanView({ type: "calendar" }),
          window: { start_date: "2026-09-01", end_date: "2026-10-12" },
        }),
      ),
    );
    await waitFor(() => expect(result.current.lanes).toHaveLength(2));
    await waitFor(() =>
      expect(result.current.lanes.every((lane) => !lane.loadingFirst)).toBe(
        true,
      ),
    );
    const pending = deferred<PlanQueryResponse>();
    query
      .mockReturnValueOnce(pending.promise)
      .mockRejectedValueOnce(error(409, "query_changed"));
    let late!: Promise<boolean>;
    act(() => {
      late = result.current.loadMore("scheduled");
    });
    await act(async () => {
      expect(await result.current.loadMore("unscheduled")).toBe(false);
    });
    await act(async () => {
      pending.resolve(
        makePlanQueryResponse({
          items: [makePlanTodo({ todo_id: "must-not-load" })],
          matched_total: 1,
          unscheduled_total: 1,
        }),
      );
      expect(await late).toBe(false);
    });
    expect(result.current.paused).toBe(true);
    expect(result.current.lanes.every((lane) => lane.paused)).toBe(true);
    expect(
      result.current.lanes
        .flatMap((lane) => lane.items)
        .some((todo) => todo.todo_id === "must-not-load"),
    ).toBe(false);
    expect(result.current.errorKey).toBe("projects.planViews.queryChanged");
  });
});
