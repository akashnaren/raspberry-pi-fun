import { readFile } from "node:fs/promises";
import { join } from "node:path";
import vocab from "../wardrobe-vocab.json" with { type: "json" };

export const SKIN = vocab.enums.skin;
export const HAIR = vocab.enums.hair;
export const TOPS = vocab.enums.top;
export const BOTTOMS = vocab.enums.bottom;
export const SHOES = vocab.enums.shoes;
export const ACCESSORIES = vocab.enums.accessory;
export const DESK_STYLES = vocab.enums.desk_style;

export const WARDROBE = new Set([...TOPS, ...BOTTOMS, ...SHOES, ...ACCESSORIES]);

export function defaultAesthetics(id) {
  const seed = vocab.seed_outfits[id] || vocab.seed_outfits.nova;
  return {
    skin: seed.skin,
    hair: seed.hair,
    outfit: {
      top: seed.top,
      bottom: seed.bottom,
      shoes: seed.shoes,
      accessory: seed.accessory,
    },
    desk_style: seed.desk_style,
  };
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
  if (!DESK_STYLES.includes(value.desk_style)) {
    return `unknown desk_style ${value.desk_style}`;
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
