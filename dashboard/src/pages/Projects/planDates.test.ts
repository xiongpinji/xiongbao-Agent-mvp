import { describe, expect, it } from "vitest";
import {
  addPlanDays,
  comparePlanDates,
  isPlanDate,
  validatePlanDates,
  planMonthGrid,
  shiftPlanMonth,
} from "./planDates";

describe("C1 calendar days independent of browser timezone", () => {
  it("builds a Monday-first month grid and shifts months without any browser date", () => {
    const grid = planMonthGrid("2024-02");
    expect(grid).toHaveLength(42);
    expect(grid[0]).toBe("2024-01-29");
    expect(grid[3]).toBe("2024-02-01");
    expect(grid[31]).toBe("2024-02-29");
    expect(planMonthGrid("1900-01")[0]).toBe("1900-01-01");
    expect(planMonthGrid("9999-12").at(-1)).toBeNull();
    expect(shiftPlanMonth("2026-01", -1)).toBe("2025-12");
    expect(shiftPlanMonth("1900-01", -1)).toBeNull();
    expect(shiftPlanMonth("9999-12", 1)).toBeNull();
  });
  it.each(["1900-01-01", "2000-02-29", "2024-02-29", "9999-12-31"])(
    "accepts %s",
    (value) => {
      expect(isPlanDate(value)).toBe(true);
    },
  );
  it.each([
    "1899-12-31",
    "10000-01-01",
    "1900-02-29",
    "2026-02-29",
    "2026-04-31",
    "2026-13-01",
    "2026-00-01",
    "2026-01-00",
    "2026-1-01",
    "2026-09-28T00:00:00Z",
    "2026-09-28+08:00",
    "",
    null,
  ])("rejects %s without normalizing", (value) => {
    expect(isPlanDate(value)).toBe(false);
  });
  it("compares and shifts across leap days, month/year edges and range limits", () => {
    expect(comparePlanDates("2026-09-28", "2026-09-29")).toBe(-1);
    expect(comparePlanDates("2026-09-28", "2026-09-28")).toBe(0);
    expect(addPlanDays("2024-03-01", -1)).toBe("2024-02-29");
    expect(addPlanDays("1900-02-28", 1)).toBe("1900-03-01");
    expect(addPlanDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addPlanDays("1900-01-01", -1)).toBeNull();
    expect(addPlanDays("9999-12-31", 1)).toBeNull();
  });
  it("allows past starts, unchanged old due dates and explicit clears", () => {
    expect(
      validatePlanDates(
        { start_date: "2020-01-01", due_date: "2026-09-28" },
        "2026-09-28",
      ),
    ).toBeNull();
    expect(
      validatePlanDates(
        { start_date: null, due_date: "2020-01-01" },
        "2026-09-28",
        "2020-01-01",
      ),
    ).toBeNull();
    expect(
      validatePlanDates({ start_date: null, due_date: null }, "2026-09-28"),
    ).toBeNull();
  });
  it("requires server metadata and validates merged start/due and new past due", () => {
    expect(validatePlanDates({ start_date: null, due_date: null }, null)).toBe(
      "metadata",
    );
    expect(
      validatePlanDates(
        { start_date: null, due_date: "2026-09-27" },
        "2026-09-28",
      ),
    ).toBe("pastDue");
    expect(
      validatePlanDates(
        { start_date: "2026-09-29", due_date: "2026-09-28" },
        "2026-09-28",
      ),
    ).toBe("dateOrder");
    expect(
      validatePlanDates(
        { start_date: "2026-02-30", due_date: null },
        "2026-09-28",
      ),
    ).toBe("invalidDate");
  });
});
