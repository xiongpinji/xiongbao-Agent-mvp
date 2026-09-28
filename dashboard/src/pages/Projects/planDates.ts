/** Calendar days only. Audit timestamps use the separate server-timezone helper. */
const leap = (year: number) =>
  year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
const monthDays = (year: number, month: number) =>
  [31, leap(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];

export function isPlanDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const [year, month, day] = value.split("-").map(Number);
  return (
    year >= 1900 &&
    year <= 9999 &&
    month >= 1 &&
    month <= 12 &&
    day >= 1 &&
    day <= monthDays(year, month)
  );
}

export function comparePlanDates(a: string, b: string): number {
  if (!isPlanDate(a) || !isPlanDate(b)) throw new Error("Invalid calendar day");
  return a === b ? 0 : a < b ? -1 : 1;
}

const beforeYear = (year: number) => {
  const previous = year - 1;
  return (
    previous * 365 +
    Math.floor(previous / 4) -
    Math.floor(previous / 100) +
    Math.floor(previous / 400)
  );
};

export function addPlanDays(value: string, days: number): string | null {
  if (!isPlanDate(value) || !Number.isSafeInteger(days)) return null;
  const [year, month, day] = value.split("-").map(Number);
  let ordinal = beforeYear(year) + day - 1 + days;
  for (let m = 1; m < month; m += 1) ordinal += monthDays(year, m);
  if (ordinal < beforeYear(1900) || ordinal >= beforeYear(10000)) return null;
  let low = 1900,
    high = 9999;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (beforeYear(middle) <= ordinal) low = middle;
    else high = middle - 1;
  }
  let remaining = ordinal - beforeYear(low),
    nextMonth = 1;
  while (remaining >= monthDays(low, nextMonth)) {
    remaining -= monthDays(low, nextMonth);
    nextMonth += 1;
  }
  return `${String(low).padStart(4, "0")}-${String(nextMonth).padStart(
    2,
    "0",
  )}-${String(remaining + 1).padStart(2, "0")}`;
}

export type PlanDateError =
  | "metadata"
  | "invalidDate"
  | "dateOrder"
  | "pastDue";
export function planMonthGrid(month: string): (string | null)[] {
  const first = `${month}-01`;
  if (!isPlanDate(first)) return [];
  const [year, m] = first.split("-").map(Number);
  let ordinal = beforeYear(year);
  for (let previous = 1; previous < m; previous += 1)
    ordinal += monthDays(year, previous);
  // 0001-01-01 is Monday in the proleptic Gregorian calendar.
  const weekday = ordinal % 7;
  return Array.from({ length: 42 }, (_, index) =>
    addPlanDays(first, index - weekday),
  );
}
export function shiftPlanMonth(month: string, amount: number): string | null {
  if (!isPlanDate(`${month}-01`) || !Number.isSafeInteger(amount)) return null;
  const [year, m] = month.split("-").map(Number);
  const index = year * 12 + m - 1 + amount;
  const nextYear = Math.floor(index / 12),
    nextMonth = (index % 12) + 1;
  return nextYear >= 1900 && nextYear <= 9999
    ? `${String(nextYear).padStart(4, "0")}-${String(nextMonth).padStart(
        2,
        "0",
      )}`
    : null;
}
export function validatePlanDates(
  values: { start_date: string | null; due_date: string | null },
  today: string | null,
  originalDue: string | null = null,
): PlanDateError | null {
  if (!isPlanDate(today)) return "metadata";
  const { start_date: start, due_date: due } = values;
  if (
    (start !== null && !isPlanDate(start)) ||
    (due !== null && !isPlanDate(due))
  )
    return "invalidDate";
  if (start !== null && due !== null && comparePlanDates(start, due) > 0)
    return "dateOrder";
  if (due !== null && due !== originalDue && comparePlanDates(due, today) < 0)
    return "pastDue";
  return null;
}
