export function validateOffice(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "office.json must be an object";
  }
  if (typeof value.walls !== "string" || typeof value.floor !== "string") {
    return "office.json needs walls and floor strings";
  }
  if (!Array.isArray(value.desks) || value.desks.length === 0) {
    return "office.json needs at least one desk";
  }
  for (const desk of value.desks) {
    if (!desk || typeof desk.owner !== "string") return "desk.owner required";
    if (!Number.isFinite(desk.x) || !Number.isFinite(desk.y)) return "desk x/y required";
  }
  if (!Array.isArray(value.rooms)) return "office.json.rooms must be an array";
  if (!Array.isArray(value.decor)) return "office.json.decor must be an array";
  return null;
}

export function validateBacklog(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "backlog.json must be an object";
  }
  if (!Array.isArray(value.tasks)) return "backlog.json.tasks must be an array";
  for (const task of value.tasks) {
    if (!task || typeof task.id !== "string" || typeof task.text !== "string") {
      return "each task needs id and text";
    }
    if (task.status !== "open" && task.status !== "closed") {
      return "task.status must be open or closed";
    }
  }
  return null;
}

export function emptyBacklog() {
  return { tasks: [] };
}
