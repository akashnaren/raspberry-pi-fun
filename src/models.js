export function familyOf(modelId) {
  const id = String(modelId || "");
  const slash = id.indexOf("/");
  return slash === -1 ? id : id.slice(0, slash);
}

export function pickModel(employee, available) {
  const preferred = employee.preferredModels || [];
  const locked = employee.model || preferred[0];
  const catalog = Array.isArray(available) ? available : [];
  if (!catalog.length) {
    if (locked) return locked;
    throw new Error(`no model for ${employee.id} family ${employee.modelFamily}`);
  }
  const ids = new Set(catalog.map((item) => item.id));
  for (const id of [locked, ...preferred].filter(Boolean)) {
    if (ids.has(id)) return id;
  }
  const family = employee.modelFamily;
  const match = catalog.find((item) => familyOf(item.id) === family);
  if (match) return match.id;
  if (locked) return locked;
  throw new Error(`no model for ${employee.id} family ${family}`);
}

export function modelForTurn(employee, { kind = "auto" } = {}) {
  const write = employee.model || employee.preferredModels?.[0];
  const chatter = employee.chatterModel || write;
  if (kind === "write" || (kind === "auto" && employee.role === "programmer")) {
    return write;
  }
  return chatter;
}

export async function fetchOpenRouterModels(fetchImpl = fetch, baseUrl = "https://openrouter.ai/api/v1") {
  const root = String(baseUrl || "https://openrouter.ai/api/v1").replace(/\/$/, "");
  const response = await fetchImpl(`${root}/models`, {
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`OpenRouter models HTTP ${response.status}`);
  }
  const body = await response.json();
  return Array.isArray(body.data) ? body.data : [];
}

export function resolveEmployeeModels(employees, available) {
  return employees.map((employee) => ({
    ...employee,
    model: pickModel(employee, available),
  }));
}
