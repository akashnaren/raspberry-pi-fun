import { test } from "node:test";
import assert from "node:assert/strict";
import { pickModel, resolveEmployeeModels } from "../src/models.js";

test("each employee lands on a different model family", () => {
  const available = [
    { id: "x-ai/grok-4-fast" },
    { id: "openai/gpt-4o-mini" },
    { id: "nousresearch/hermes-3-llama-3.1-70b" },
  ];
  const employees = [
    { id: "mira", modelFamily: "x-ai", preferredModels: ["x-ai/grok-4-fast"] },
    { id: "nova", modelFamily: "openai", preferredModels: ["openai/gpt-4o-mini"] },
    { id: "kessler", modelFamily: "nousresearch", preferredModels: ["nousresearch/hermes-3-llama-3.1-70b"] },
  ];
  const resolved = resolveEmployeeModels(employees, available);
  const families = new Set(resolved.map((employee) => employee.model.split("/")[0]));
  assert.equal(families.size, 3);
  assert.equal(pickModel(employees[1], available), "openai/gpt-4o-mini");
});

test("falls back to preferred id when the catalog is empty", () => {
  const employee = {
    id: "nova",
    modelFamily: "openai",
    preferredModels: ["openai/gpt-4o-mini"],
  };
  assert.equal(pickModel(employee, []), "openai/gpt-4o-mini");
});
