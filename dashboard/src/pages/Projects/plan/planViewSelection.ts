import type { PlanView } from "../../../api/modules/projectPlanViews";
export type PlanSelectionStorage = Pick<
  Storage,
  "getItem" | "setItem" | "removeItem"
>;
export const selectionKey = (accountId: number, projectId: string) =>
  `octop:plan-view:${encodeURIComponent(
    JSON.stringify([accountId, projectId]),
  )}`;
const activeFor = (views: readonly PlanView[], projectId: string) =>
  views
    .filter(
      (view) => view.project_id === projectId && view.archived_at === null,
    )
    .sort(
      (a, b) =>
        a.position - b.position ||
        (a.view_id < b.view_id ? -1 : a.view_id > b.view_id ? 1 : 0),
    );
export function readSelectedViewId(
  storage: PlanSelectionStorage | null,
  accountId: number,
  projectId: string,
  activeViews: readonly PlanView[],
  defaultViewId: string,
): string | null {
  const active = activeFor(activeViews, projectId);
  let selected: string | null = null;
  try {
    selected = storage?.getItem(selectionKey(accountId, projectId)) ?? null;
  } catch {
    /* Selection remains optional. */
  }
  if (active.some((view) => view.view_id === selected)) return selected;
  return (
    active.find((view) => view.view_id === defaultViewId)?.view_id ??
    active[0]?.view_id ??
    null
  );
}
export function writeSelectedViewId(
  storage: PlanSelectionStorage | null,
  accountId: number,
  projectId: string,
  viewId: string,
  activeViews: readonly PlanView[],
): void {
  if (
    !activeFor(activeViews, projectId).some((view) => view.view_id === viewId)
  )
    return;
  try {
    storage?.setItem(selectionKey(accountId, projectId), viewId);
  } catch {
    /* Browser storage may be unavailable. */
  }
}
export function clearSelectedViewId(
  storage: PlanSelectionStorage | null,
  accountId: number,
  projectId: string,
): void {
  try {
    storage?.removeItem(selectionKey(accountId, projectId));
  } catch {
    /* Browser storage may be unavailable. */
  }
}
