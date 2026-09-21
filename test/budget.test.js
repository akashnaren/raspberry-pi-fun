import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createBudget } from "../src/budget.js";

test("daily ceiling pauses spend at $5 and resets the next UTC day", async () => {
  const dir = await mkdtemp(join(tmpdir(), "budget-"));
  let now = Date.parse("2026-09-21T10:00:00Z");
  const budget = await createBudget({
    filePath: join(dir, "spend.json"),
    dailyCeilingUsd: 5,
    now: () => now,
  });

  await budget.recordSpend(4.5);
  assert.equal(budget.isExhausted(), false);
  await budget.recordSpend(0.6);
  assert.equal(budget.snapshot().spentUsd, 5.1);
  assert.equal(budget.isExhausted(), true);

  now = Date.parse("2026-09-22T00:00:01Z");
  assert.equal(budget.isExhausted(), false);
  assert.equal(budget.snapshot().spentUsd, 0);
  assert.equal(budget.snapshot().day, "2026-09-22");
});
