/** HDMI must keep a room if HTTP or the socket drop. No Node imports. */

export function reconnectDelayMs(attempt, { base = 1000, cap = 30000 } = {}) {
  const n = Math.max(0, Math.floor(Number(attempt) || 0));
  const wait = Number(base) * 2 ** n;
  const limit = Number(cap);
  if (!Number.isFinite(wait) || wait < 0) return Math.max(0, Number(base) || 1000);
  return Math.min(Number.isFinite(limit) ? limit : 30000, wait);
}

export function usableOffice(value) {
  return Boolean(
    value &&
      typeof value === "object" &&
      Array.isArray(value.rooms) &&
      value.rooms.length > 0 &&
      Array.isArray(value.desks) &&
      value.desks.length > 0,
  );
}

export function usableCast(value) {
  return Array.isArray(value) && value.length > 0;
}

/** Keep seed office/cast if /api/state is empty, 500, or missing rooms. */
export function mergeStudioState(current, next, fallbackOffice, fallbackCast) {
  const base = current && typeof current === "object" ? current : {};
  if (!next || typeof next !== "object") {
    return {
      ...base,
      office: usableOffice(base.office) ? base.office : fallbackOffice,
      employees: usableCast(base.employees) ? base.employees : fallbackCast,
    };
  }
  const office = usableOffice(next.office)
    ? next.office
    : usableOffice(base.office)
      ? base.office
      : fallbackOffice;
  const employees = usableCast(next.employees)
    ? next.employees
    : usableCast(base.employees)
      ? base.employees
      : fallbackCast;
  return { ...base, ...next, office, employees };
}

export function parseSocketMessage(raw) {
  try {
    const msg = JSON.parse(String(raw ?? ""));
    return msg && typeof msg === "object" ? msg : null;
  } catch {
    return null;
  }
}
