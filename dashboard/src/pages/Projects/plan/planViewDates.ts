import type { PlanQueryWindow } from "../../../api/modules/projectPlanViews";
import {
  addPlanDays,
  isPlanDate,
  planMonthGrid,
  validatePlanDates,
} from "../planDates";

export interface PlanDateValues {
  start_date: string | null;
  due_date: string | null;
}
export type PlanDateBasis = "start_date" | "due_date";
export type PlanDateProposal =
  | {
      ok: true;
      changes: Partial<PlanDateValues>;
      requiresConfirmation: boolean;
    }
  | {
      ok: false;
      reason: "metadata" | "invalid" | "range" | "pastDue" | "outOfBounds";
    };

export function planWeekGrid(anchor: string): (string | null)[] {
  if (!isPlanDate(anchor)) return [];
  const grid = planMonthGrid(anchor.slice(0, 7));
  const index = grid.indexOf(anchor);
  return grid.slice(Math.floor(index / 7) * 7, Math.floor(index / 7) * 7 + 7);
}
export function clipPlanWindow(
  start: string | null,
  end: string | null,
): PlanQueryWindow | null {
  if (start === null && end === null) return null;
  const first = start ?? "1900-01-01";
  const last = end ?? "9999-12-31";
  if (!isPlanDate(first) || !isPlanDate(last) || first > last) return null;
  const longest = addPlanDays(first, 365) ?? "9999-12-31";
  if (last > longest) return null;
  return { start_date: first, end_date: last };
}
export function planGanttInterval(
  todo: PlanDateValues,
): PlanQueryWindow | null {
  const start = todo.start_date ?? todo.due_date;
  const end = todo.due_date ?? todo.start_date;
  if (!isPlanDate(start) || !isPlanDate(end) || start > end) return null;
  return { start_date: start, end_date: end };
}

function validateProposal(
  todo: PlanDateValues,
  changes: Partial<PlanDateValues>,
  serverToday: string | null,
  requiresConfirmation = false,
): PlanDateProposal {
  const error = validatePlanDates(
    { ...todo, ...changes },
    serverToday,
    todo.due_date,
  );
  if (error) {
    const reasons = {
      metadata: "metadata",
      invalidDate: "invalid",
      dateOrder: "range",
      pastDue: "pastDue",
    } as const;
    return { ok: false, reason: reasons[error] };
  }
  return { ok: true, changes, requiresConfirmation };
}

export function proposeGanttShift(
  todo: PlanDateValues,
  delta: number,
  serverToday: string | null,
): PlanDateProposal {
  if (!isPlanDate(serverToday)) return { ok: false, reason: "metadata" };
  if (!Number.isSafeInteger(delta) || !planGanttInterval(todo))
    return { ok: false, reason: "invalid" };
  const changes: Partial<PlanDateValues> = {};
  for (const field of ["start_date", "due_date"] as const) {
    if (todo[field] === null) continue;
    const shifted = addPlanDays(todo[field], delta);
    if (!shifted) return { ok: false, reason: "outOfBounds" };
    changes[field] = shifted;
  }
  return validateProposal(todo, changes, serverToday);
}
export function proposeGanttResize(
  todo: PlanDateValues,
  edge: "start" | "end",
  date: string | null,
  serverToday: string | null,
): PlanDateProposal {
  const field = edge === "start" ? "start_date" : "due_date";
  const opposite = edge === "start" ? "due_date" : "start_date";
  return validateProposal(
    todo,
    { [field]: date },
    serverToday,
    todo[field] === null && todo[opposite] !== null && date !== null,
  );
}
export function proposeCalendarMove(
  todo: PlanDateValues,
  basis: PlanDateBasis,
  date: string | null,
  serverToday: string | null,
): PlanDateProposal {
  return validateProposal(todo, { [basis]: date }, serverToday);
}
