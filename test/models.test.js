import { test } from "node:test";
import assert from "node:assert/strict";
import { modelForTurn, pickModel, resolveEmployeeModels } from "../src/models.js";

test("locked cast lands on four distinct families", () => {
  const available = [
    { id: "qwen/qwen3-coder-next" },
    { id: "nousresearch/hermes-3-llama-3.1-70b" },
    { id: "z-ai/glm-5.3-flash" },
    { id: "meta-llama/llama-4-scout" },
  ];
  const employees = [
    {
      id: "mira",
      modelFamily: "z-ai",
      model: "z-ai/glm-5.3-flash",
      preferredModels: ["z-ai/glm-5.3-flash"],
    },
    {
      id: "nova",
      modelFamily: "qwen",
      model: "qwen/qwen3-coder-next",
      preferredModels: ["qwen/qwen3-coder-next"],
      chatterModel: "z-ai/glm-5.3-flash",
    },
    {
      id: "kessler",
      modelFamily: "nousresearch",
      model: "nousresearch/hermes-3-llama-3.1-70b",
      preferredModels: ["nousresearch/hermes-3-llama-3.1-70b"],
    },
    {
      id: "jules",
      modelFamily: "meta-llama",
      model: "meta-llama/llama-4-scout",
      preferredModels: ["meta-llama/llama-4-scout"],
    },
  ];
  const resolved = resolveEmployeeModels(employees, available);
  const families = new Set(resolved.map((employee) => employee.model.split("/")[0]));
  assert.equal(families.size, 4);
  assert.equal(pickModel(employees[1], available), "qwen/qwen3-coder-next");
  assert.equal(modelForTurn(employees[1], { kind: "write" }), "qwen/qwen3-coder-next");
  assert.equal(modelForTurn(employees[1], { kind: "chatter" }), "z-ai/glm-5.3-flash");
  assert.equal(modelForTurn(employees[3], { kind: "chatter" }), "meta-llama/llama-4-scout");
});

test("falls back to locked model id when the catalog is empty", () => {
  const employee = {
    id: "nova",
    modelFamily: "qwen",
    model: "qwen/qwen3-coder-next",
    preferredModels: ["qwen/qwen3-coder-next"],
  };
  assert.equal(pickModel(employee, []), "qwen/qwen3-coder-next");
});

test("locked Stage 1 ids win, then preferred, then family", () => {
  const nova = {
    id: "nova",
    modelFamily: "qwen",
    model: "qwen/qwen3-coder-next",
    preferredModels: ["qwen/qwen3-coder-next", "qwen/qwen3-coder-plus"],
  };
  assert.equal(
    pickModel(nova, [{ id: "qwen/qwen3-coder-next" }, { id: "qwen/qwen3-coder-plus" }]),
    "qwen/qwen3-coder-next",
  );
  assert.equal(pickModel(nova, [{ id: "qwen/qwen3-coder-plus" }]), "qwen/qwen3-coder-plus");
  assert.equal(pickModel(nova, [{ id: "qwen/qwen3-coder-next-free" }]), "qwen/qwen3-coder-next-free");
});

test("research seats map onto the product cast, not river", () => {
  const employees = [
    { id: "nova", role: "programmer", model: "qwen/qwen3-coder-next", modelFamily: "qwen" },
    { id: "mira", role: "producer", model: "z-ai/glm-5.3-flash", modelFamily: "z-ai" },
    { id: "kessler", role: "qa", model: "nousresearch/hermes-3-llama-3.1-70b", modelFamily: "nousresearch" },
  ];
  assert.equal(employees.some((person) => person.id === "river"), false);
  const families = new Set(employees.map((person) => person.modelFamily));
  assert.deepEqual([...families].sort(), ["nousresearch", "qwen", "z-ai"]);
});
