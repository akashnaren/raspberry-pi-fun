export function familyOf(modelId) {
  const id = String(modelId || "");
  const slash = id.indexOf("/");
  return slash === -1 ? id : id.slice(0, slash);
}

export function pickModel(employee, available) {
  const preferred = employee.preferredModels || [];
  const catalog = Array.isArray(available) ? available : [];
  const ids = new Set(catalog.map((item) => item.id));
  for (const id of preferred) {
    if (ids.has(id)) return id;
  }
  const family = employee.modelFamily;
  const match = catalog.find((item) => familyOf(item.id) === family);
  if (match) return match.id;
  if (preferred[0]) return preferred[0];
  throw new Error(`no model for ${employee.id} family ${family}`);
}

export async function fetchOpenRouterModels(fetchImpl = fetch) {
  const response = await fetchImpl("https://openrouter.ai/api/v1/models", {
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
