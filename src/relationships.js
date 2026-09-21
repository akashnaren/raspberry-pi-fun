import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

export const SEED_PAIRS = [
  { a: "nova", b: "kessler", score: -1, note: "speed vs craft" },
  { a: "mira", b: "nova", score: 1, note: "wants the ship" },
  { a: "mira", b: "kessler", score: 0, note: "allies when quality blocks Friday" },
];

export function emptyMatrix() {
  return {
    updatedAt: 0,
    decayDay: "",
    decayPerDay: 0.15,
    pairs: SEED_PAIRS.map((pair) => ({ ...pair })),
  };
}

export function validateRelationships(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "relationships.json must be an object";
  }
  if (!Array.isArray(value.pairs) || value.pairs.length === 0) {
    return "relationships.json.pairs must be a non-empty array";
  }
  for (const pair of value.pairs) {
    if (!pair || typeof pair.a !== "string" || typeof pair.b !== "string") {
      return "each pair needs a and b";
    }
    if (!Number.isFinite(Number(pair.score))) return "each pair needs a numeric score";
  }
  return null;
}

export function pairKey(a, b) {
  return [a, b].sort().join("::");
}

export function opinionsFor(matrix, id, limit = 6) {
  const pairs = matrix?.pairs || [];
  return pairs
    .filter((pair) => pair.a === id || pair.b === id)
    .map((pair) => ({
      other: pair.a === id ? pair.b : pair.a,
      score: Number(pair.score) || 0,
      note: pair.note || "",
    }))
    .slice(0, limit);
}

export function decayTowardZero(matrix, days = 1) {
  const rate = Number(matrix.decayPerDay) || 0.15;
  const next = {
    ...matrix,
    pairs: (matrix.pairs || []).map((pair) => {
      let score = Number(pair.score) || 0;
      const step = rate * days;
      if (score > 0) score = Math.max(0, score - step);
      else if (score < 0) score = Math.min(0, score + step);
      return { ...pair, score: Math.round(score * 100) / 100 };
    }),
  };
  return next;
}

export function bumpPair(matrix, a, b, delta) {
  const key = pairKey(a, b);
  const pairs = (matrix.pairs || []).map((pair) => {
    if (pairKey(pair.a, pair.b) !== key) return pair;
    const score = Math.max(-2, Math.min(2, (Number(pair.score) || 0) + delta));
    return { ...pair, score: Math.round(score * 100) / 100 };
  });
  return { ...matrix, pairs, updatedAt: Date.now() };
}

export function applyRelationshipEvent(matrix, event) {
  if (!event) return matrix;
  if (event.type === "build_failed") {
    return bumpPair(matrix, "kessler", "nova", -0.2);
  }
  if (event.type === "build_passed" && event.data?.stage !== "seed") {
    return bumpPair(matrix, "mira", "nova", 0.1);
  }
  return matrix;
}

export async function createRelationships({ filePath, now = () => Date.now() }) {
  await mkdir(dirname(filePath), { recursive: true });
  let state = await load(filePath);

  async function save() {
    state.updatedAt = now();
    await writeFile(filePath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  }

  return {
    snapshot() {
      return structuredClone(state);
    },
    opinionsFor(id, limit = 6) {
      return opinionsFor(state, id, limit);
    },
    async decayIfNewDay() {
      const day = new Date(now()).toISOString().slice(0, 10);
      if (state.decayDay === day) return state;
      const elapsed = state.decayDay ? 1 : 0;
      if (elapsed) state = decayTowardZero(state, elapsed);
      state.decayDay = day;
      await save();
      return state;
    },
    async applyEvent(event) {
      const next = applyRelationshipEvent(state, event);
      if (next === state) return state;
      state = next;
      await save();
      return state;
    },
  };
}

async function load(filePath) {
  try {
    const parsed = JSON.parse(await readFile(filePath, "utf8"));
    if (validateRelationships(parsed)) return emptyMatrix();
    return {
      ...emptyMatrix(),
      ...parsed,
      pairs: parsed.pairs.map((pair) => ({ ...pair, score: Number(pair.score) })),
    };
  } catch {
    return emptyMatrix();
  }
}
