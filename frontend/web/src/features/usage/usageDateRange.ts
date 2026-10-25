export type UsagePeriod = "today" | "7days" | "30days" | "custom";

/** Inclusive calendar dates in Beijing, independent of the browser timezone. */
export function usageDateRange(days: number, now = new Date()) {
  const end = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - days + 1);
  return {
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  };
}

export function isUsageDateRangeValid(range: { start: string; end: string }) {
  const validDate = (value: string) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
    const date = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
  };
  return validDate(range.start) && validDate(range.end) && range.start <= range.end;
}
