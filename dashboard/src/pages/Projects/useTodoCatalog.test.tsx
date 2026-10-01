import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectTodoCatalog } from "../../api/modules/projectTodoCatalog";
import { deferred, makePlanCatalog } from "./plan/planView.testFixtures";
import { useTodoCatalog } from "./useTodoCatalog";

const { getCatalog } = vi.hoisted(() => ({ getCatalog: vi.fn() }));
vi.mock("../../api/modules/projectTodoCatalog", () => ({
  projectTodoCatalogApi: { get: getCatalog },
}));

const catalog = (
  projectId = "p1",
  revision = 1,
  serverToday = "2026-09-29",
  name = "Initial catalog",
): ProjectTodoCatalog => {
  const initial = makePlanCatalog();
  return {
    ...initial,
    project_id: projectId,
    revision,
    server_today: serverToday,
    priorities: initial.priorities.map((item) => ({
      ...item,
      name: `${name} priority`,
    })),
    tags: initial.tags.map((item) => ({ ...item, name: `${name} tag` })),
  };
};
const httpError = (status: number, body: object) =>
  new Error(`Request failed: ${status} - ${JSON.stringify(body)}`);

beforeEach(() => {
  getCatalog.mockReset().mockResolvedValue(catalog());
});

describe("catalog read access-loss classification", () => {
  const misleadingBodies = [
    { name: "not found text", body: { detail: "upstream not found" } },
    { name: "NOT_FOUND code", body: { error: { code: "NOT_FOUND" } } },
    { name: "404 text", body: { detail: "upstream returned 404" } },
  ];
  const ordinaryErrors = [500, 422, 503].flatMap((status) =>
    misleadingBodies.map(({ name, body }) => ({ status, name, body })),
  );

  it.each(ordinaryErrors)(
    "retains the catalog on HTTP $status with $name",
    async ({ status, body }) => {
      const onAccessLost = vi.fn();
      const { result } = renderHook(() =>
        useTodoCatalog("p1", 1, onAccessLost),
      );
      await waitFor(() => expect(result.current.catalog).toEqual(catalog()));
      const failure = httpError(status, body);
      getCatalog.mockRejectedValueOnce(failure);

      await act(async () => {
        expect(await result.current.reload()).toBe(false);
      });

      expect(result.current.catalog).toEqual(catalog());
      expect(result.current.error).toBe(failure);
      expect(result.current.loading).toBe(false);
      expect(onAccessLost).not.toHaveBeenCalled();
    },
  );

  it("clears the catalog and notifies on actual HTTP 404", async () => {
    const onAccessLost = vi.fn();
    const { result } = renderHook(() => useTodoCatalog("p1", 1, onAccessLost));
    await waitFor(() => expect(result.current.catalog).toEqual(catalog()));
    const failure = httpError(404, { error: { code: "NOT_FOUND" } });
    getCatalog.mockRejectedValueOnce(failure);

    await act(async () => {
      expect(await result.current.reload()).toBe(false);
    });

    expect(result.current.catalog).toBeNull();
    expect(result.current.error).toBe(failure);
    expect(result.current.loading).toBe(false);
    expect(onAccessLost).toHaveBeenCalledTimes(1);
  });
});

describe.each([
  { name: "project A to B", nextProject: "p2", nextAccount: 1, aba: false },
  { name: "account A to B", nextProject: "p1", nextAccount: 2, aba: false },
  { name: "project ABA", nextProject: "p2", nextAccount: 1, aba: true },
  { name: "account ABA", nextProject: "p1", nextAccount: 2, aba: true },
])("pending catalog GET across $name", ({ nextProject, nextAccount, aba }) => {
  it.each(["success", "404"] as const)(
    "ignores the old %s and leaves current metadata and reload usable",
    async (outcome) => {
      const onAccessLost = vi.fn();
      const initialProps = { projectId: "p1", accountId: 1 };
      const { result, rerender } = renderHook(
        ({ projectId, accountId }) =>
          useTodoCatalog(projectId, accountId, onAccessLost),
        { initialProps },
      );
      await waitFor(() => expect(result.current.catalog).toEqual(catalog()));
      const pending = deferred<ProjectTodoCatalog>();
      getCatalog.mockReturnValueOnce(pending.promise);
      let oldReload!: Promise<boolean>;
      act(() => {
        oldReload = result.current.reload();
      });

      let currentCatalog = catalog(nextProject, 7, "2026-10-01", "New context");
      getCatalog.mockResolvedValueOnce(currentCatalog);
      rerender({ projectId: nextProject, accountId: nextAccount });
      await waitFor(() =>
        expect(result.current.catalog).toEqual(currentCatalog),
      );

      if (aba) {
        currentCatalog = catalog("p1", 9, "2026-10-03", "Returned context");
        getCatalog.mockResolvedValueOnce(currentCatalog);
        rerender(initialProps);
        await waitFor(() =>
          expect(result.current.catalog).toEqual(currentCatalog),
        );
      }

      await act(async () => {
        if (outcome === "success")
          pending.resolve(catalog("p1", 2, "2026-09-30", "Old context"));
        else pending.reject(httpError(404, { error: { code: "NOT_FOUND" } }));
        expect(await oldReload).toBe(false);
      });

      expect(result.current.catalog).toEqual(currentCatalog);
      expect(result.current.error).toBeNull();
      expect(result.current.loading).toBe(false);
      expect(onAccessLost).not.toHaveBeenCalled();

      const refreshed = catalog(
        currentCatalog.project_id,
        currentCatalog.revision + 1,
        "2026-10-04",
        "Explicitly refreshed context",
      );
      getCatalog.mockResolvedValueOnce(refreshed);
      await act(async () => {
        expect(await result.current.reload()).toBe(true);
      });
      expect(result.current.catalog).toEqual(refreshed);
      expect(result.current.error).toBeNull();
      expect(onAccessLost).not.toHaveBeenCalled();
    },
  );
});
