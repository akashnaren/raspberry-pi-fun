import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const SKIN = ["fair", "warm", "olive", "deep"];
export const HAIR = ["short-black", "bun-amber", "wave-teal", "crop-violet", "ponytail"];
export const TOPS = ["hoodie", "tee", "blazer", "cardigan", "henley"];
export const BOTTOMS = ["jeans", "chinos", "skirt", "trousers"];
export const SHOES = ["sneakers", "boots", "loafers"];
export const ACCESSORIES = ["none", "earbuds", "glasses", "watch", "badge"];

export const WARDROBE = new Set([...TOPS, ...BOTTOMS, ...SHOES, ...ACCESSORIES]);

export function defaultAesthetics(id) {
  const seeds = {
    nova: {
      skin: "warm",
      hair: "short-black",
      outfit: { top: "hoodie", bottom: "jeans", shoes: "sneakers", accessory: "earbuds" },
      desk_style: "messy cables + stickers",
    },
    kessler: {
      skin: "fair",
      hair: "wave-teal",
      outfit: { top: "cardigan", bottom: "chinos", shoes: "loafers", accessory: "glasses" },
      desk_style: "aligned sticky notes",
    },
    mira: {
      skin: "olive",
      hair: "bun-amber",
      outfit: { top: "blazer", bottom: "trousers", shoes: "boots", accessory: "watch" },
      desk_style: "one notebook, clear desk",
    },
    reed: {
      skin: "deep",
      hair: "crop-violet",
      outfit: { top: "henley", bottom: "chinos", shoes: "sneakers", accessory: "badge" },
      desk_style: "clipboard and floor-plan printouts",
    },
  };
  return seeds[id] || seeds.nova;
}

export function defaultWardrobe() {
  return [...WARDROBE];
}

export function validateAesthetics(value, wardrobe = defaultWardrobe()) {
  if (!value || typeof value !== "object") return "aesthetics must be an object";
  if (!SKIN.includes(value.skin)) return `unknown skin ${value.skin}`;
  if (!HAIR.includes(value.hair)) return `unknown hair ${value.hair}`;
  const outfit = value.outfit || {};
  if (!TOPS.includes(outfit.top)) return `unknown top ${outfit.top}`;
  if (!BOTTOMS.includes(outfit.bottom)) return `unknown bottom ${outfit.bottom}`;
  if (!SHOES.includes(outfit.shoes)) return `unknown shoes ${outfit.shoes}`;
  if (!ACCESSORIES.includes(outfit.accessory || "none")) return `unknown accessory ${outfit.accessory}`;
  const unlocked = new Set(wardrobe);
  for (const piece of [outfit.top, outfit.bottom, outfit.shoes, outfit.accessory || "none"]) {
    if (piece !== "none" && !unlocked.has(piece)) return `${piece} is not in the unlocked wardrobe`;
  }
  if (typeof value.desk_style !== "string" || !value.desk_style.trim()) {
    return "desk_style required";
  }
  return null;
}

export function validateEmployeeRecord(value, expectedId) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "employee record must be an object";
  }
  if (expectedId && value.id && value.id !== expectedId) return "cannot change employee id";
  if (typeof value.name !== "string") return "name required";
  if (typeof value.role !== "string") return "role required";
  if (!Array.isArray(value.wardrobe_unlocked) || value.wardrobe_unlocked.length === 0) {
    return "wardrobe_unlocked required";
  }
  return validateAesthetics(value.aesthetics, value.wardrobe_unlocked);
}

export function applyAestheticsPatch(record, patch = {}) {
  const next = structuredClone(record);
  next.aesthetics = {
    ...record.aesthetics,
    ...patch,
    outfit: { ...record.aesthetics.outfit, ...(patch.outfit || {}) },
  };
  const error = validateAesthetics(next.aesthetics, next.wardrobe_unlocked);
  if (error) throw new Error(error);
  return next;
}

export async function loadEmployeeRecord(workspaceRoot, id) {
  try {
    const parsed = JSON.parse(await readFile(join(workspaceRoot, `employees/${id}.json`), "utf8"));
    const error = validateEmployeeRecord(parsed, id);
    if (error) return null;
    return parsed;
  } catch {
    return null;
  }
}

export async function mergeEmployees(workspaceRoot, employees) {
  const merged = [];
  for (const employee of employees) {
    const record = await loadEmployeeRecord(workspaceRoot, employee.id);
    merged.push({
      ...employee,
      color: record?.accent || employee.color,
      accent: record?.accent || employee.color,
      aesthetics: record?.aesthetics || defaultAesthetics(employee.id),
      wardrobe_unlocked: record?.wardrobe_unlocked || defaultWardrobe(),
      desk_style: record?.aesthetics?.desk_style || "",
    });
  }
  return merged;
}
