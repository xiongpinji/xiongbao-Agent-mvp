import { describe, expect, it } from "vitest";
import {
  clipPlanWindow,
  planGanttInterval,
  planWeekGrid,
  proposeCalendarMove,
  proposeGanttResize,
  proposeGanttShift,
} from "./planViewDates";

const today = "2026-09-29";

describe("plan calendar day windows", () => {
  it("uses Monday-first seven-day weeks across year and leap-day boundaries", () => {
    expect(planWeekGrid("2024-02-29")).toEqual([
      "2024-02-26",
      "2024-02-27",
      "2024-02-28",
      "2024-02-29",
      "2024-03-01",
      "2024-03-02",
      "2024-03-03",
    ]);
    expect(planWeekGrid("2027-01-01")).toEqual([
      "2026-12-28",
      "2026-12-29",
      "2026-12-30",
      "2026-12-31",
      "2027-01-01",
      "2027-01-02",
      "2027-01-03",
    ]);
  });

  it("retains null grid positions beyond the supported upper year", () => {
    expect(planWeekGrid("9999-12-31")).toEqual([
      "9999-12-27",
      "9999-12-28",
      "9999-12-29",
      "9999-12-30",
      "9999-12-31",
      null,
      null,
    ]);
    expect(planWeekGrid("1900-02-29")).toEqual([]);
  });

  it("clips boundary grid nulls into a closed legal query window", () => {
    expect(clipPlanWindow("9999-12-27", null)).toEqual({
      start_date: "9999-12-27",
      end_date: "9999-12-31",
    });
    expect(clipPlanWindow(null, "1900-01-07")).toEqual({
      start_date: "1900-01-01",
      end_date: "1900-01-07",
    });
    expect(clipPlanWindow(null, null)).toBeNull();
    expect(clipPlanWindow("2026-10-01", "2026-09-29")).toBeNull();
    expect(clipPlanWindow("2026-01-01", "2027-01-02")).toBeNull();
  });

  it("allows the full 366-day closed leap-year window", () => {
    expect(clipPlanWindow("2024-01-01", "2024-12-31")).toEqual({
      start_date: "2024-01-01",
      end_date: "2024-12-31",
    });
  });
});

describe("real gantt intervals", () => {
  it.each([
    [
      { start_date: "2026-09-29", due_date: "2026-10-02" },
      { start_date: "2026-09-29", end_date: "2026-10-02" },
    ],
    [
      { start_date: "2026-09-29", due_date: null },
      { start_date: "2026-09-29", end_date: "2026-09-29" },
    ],
    [
      { start_date: null, due_date: "2026-10-02" },
      { start_date: "2026-10-02", end_date: "2026-10-02" },
    ],
    [{ start_date: null, due_date: null }, null],
  ])(
    "represents actual dates without inventing missing endpoints: %j",
    (todo, interval) => {
      expect(planGanttInterval(todo)).toEqual(interval);
    },
  );

  it("rejects malformed or reversed persisted date intervals", () => {
    expect(
      planGanttInterval({ start_date: "2026-02-30", due_date: null }),
    ).toBeNull();
    expect(
      planGanttInterval({ start_date: "2026-10-02", due_date: "2026-10-01" }),
    ).toBeNull();
  });
});

describe("versioned calendar-day proposals", () => {
  it("shifts both real endpoints while preserving a leap-day interval", () => {
    expect(
      proposeGanttShift(
        { start_date: "2028-02-28", due_date: "2028-03-01" },
        1,
        today,
      ),
    ).toEqual({
      ok: true,
      changes: { start_date: "2028-02-29", due_date: "2028-03-02" },
      requiresConfirmation: false,
    });
  });

  it.each([
    [
      { start_date: "2026-10-01", due_date: null },
      { start_date: "2026-10-02" },
    ],
    [{ start_date: null, due_date: "2026-10-01" }, { due_date: "2026-10-02" }],
  ])(
    "preserves the absent endpoint during whole-bar shifts: %j",
    (todo, changes) => {
      expect(proposeGanttShift(todo, 1, today)).toEqual({
        ok: true,
        changes,
        requiresConfirmation: false,
      });
    },
  );

  it("requires an explicit confirmation before adding a missing gantt edge", () => {
    const todo = { start_date: "2026-10-01", due_date: null };
    expect(proposeGanttResize(todo, "end", "2026-10-03", today)).toEqual({
      ok: true,
      changes: { due_date: "2026-10-03" },
      requiresConfirmation: true,
    });
    expect(
      proposeGanttResize(
        { start_date: "2026-09-29", due_date: "2026-10-02" },
        "start",
        "2026-09-30",
        today,
      ),
    ).toEqual({
      ok: true,
      changes: { start_date: "2026-09-30" },
      requiresConfirmation: false,
    });
  });

  it("moves only the calendar date basis and keeps the opposite date unchanged", () => {
    const todo = { start_date: "2026-09-29", due_date: "2026-10-03" };
    expect(
      proposeCalendarMove(todo, "start_date", "2026-10-01", today),
    ).toEqual({
      ok: true,
      changes: { start_date: "2026-10-01" },
      requiresConfirmation: false,
    });
    expect(proposeCalendarMove(todo, "due_date", null, today)).toEqual({
      ok: true,
      changes: { due_date: null },
      requiresConfirmation: false,
    });
  });

  it("keeps the original overdue due date but rejects a different past due date", () => {
    const todo = { start_date: null, due_date: "2026-09-20" };
    expect(
      proposeCalendarMove(todo, "due_date", "2026-09-20", today),
    ).toMatchObject({ ok: true });
    expect(proposeCalendarMove(todo, "due_date", "2026-09-21", today)).toEqual({
      ok: false,
      reason: "pastDue",
    });
    expect(proposeGanttShift(todo, 1, today)).toEqual({
      ok: false,
      reason: "pastDue",
    });
    expect(
      proposeCalendarMove(todo, "start_date", "2026-09-19", today),
    ).toMatchObject({ ok: true });
  });

  it("checks merged date order rather than just the changed field", () => {
    expect(
      proposeCalendarMove(
        { start_date: "2026-10-02", due_date: "2026-10-03" },
        "due_date",
        "2026-10-01",
        today,
      ),
    ).toEqual({ ok: false, reason: "range" });
    expect(
      proposeGanttResize(
        { start_date: "2026-10-02", due_date: "2026-10-03" },
        "start",
        "2026-10-04",
        today,
      ),
    ).toEqual({ ok: false, reason: "range" });
  });

  it("rejects missing server metadata, malformed dates and out-of-bounds shifts", () => {
    const todo = { start_date: "2026-10-01", due_date: null };
    expect(proposeGanttShift(todo, 1, null)).toEqual({
      ok: false,
      reason: "metadata",
    });
    expect(
      proposeCalendarMove(todo, "start_date", "2026-02-30", today),
    ).toEqual({ ok: false, reason: "invalid" });
    expect(proposeGanttShift(todo, 1.5, today)).toEqual({
      ok: false,
      reason: "invalid",
    });
    expect(
      proposeGanttShift({ start_date: "9999-12-31", due_date: null }, 1, today),
    ).toEqual({ ok: false, reason: "outOfBounds" });
    expect(
      proposeGanttShift(
        { start_date: "1900-01-01", due_date: null },
        -1,
        today,
      ),
    ).toEqual({ ok: false, reason: "outOfBounds" });
    expect(
      proposeGanttShift({ start_date: null, due_date: null }, 1, today),
    ).toEqual({ ok: false, reason: "invalid" });
  });
});
