import { createDryRunDriver } from "./dry-run.js";
import { TOOL_SCHEMAS } from "./tools.js";

const DEFAULT_RATES = {
  inputPerMillion: 0.15,
  outputPerMillion: 0.6,
};

export function estimateCostUsd(usage, rates = DEFAULT_RATES) {
  const prompt = Number(usage?.prompt_tokens) || 0;
  const completion = Number(usage?.completion_tokens) || 0;
  return (prompt / 1e6) * rates.inputPerMillion + (completion / 1e6) * rates.outputPerMillion;
}

export function parseToolCalls(message) {
  const calls = message?.tool_calls;
  if (!Array.isArray(calls)) return [];
  return calls.map((call) => {
    let args = {};
    try {
      args = JSON.parse(call.function?.arguments || "{}");
    } catch {
      args = {};
    }
    return { name: call.function?.name, arguments: args };
  });
}

export function createLlm({
  apiKey,
  referer,
  title,
  fetchImpl = fetch,
  dryRunDriver = createDryRunDriver(),
}) {
  const enabled = Boolean(apiKey);

  return {
    get dryRun() {
      return !enabled;
    },
    async complete({ employee, messages, tools = TOOL_SCHEMAS }) {
      if (!enabled) {
        return dryRunDriver.complete({ employee, messages });
      }
      const response = await fetchImpl("https://openrouter.ai/api/v1/chat/completions", {
        method: "POST",
        headers: {
          authorization: `Bearer ${apiKey}`,
          "content-type": "application/json",
          "http-referer": referer || "http://127.0.0.1:8787",
          "x-title": title || "Meridian Desk",
        },
        body: JSON.stringify({
          model: employee.model,
          messages,
          tools,
          tool_choice: "auto",
          temperature: employee.role === "programmer" ? 0.4 : 0.7,
        }),
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(`OpenRouter HTTP ${response.status}: ${detail.slice(0, 240)}`);
      }
      const body = await response.json();
      const message = body.choices?.[0]?.message || {};
      let costUsd = estimateCostUsd(body.usage);
      if (typeof body.usage?.cost === "number") costUsd = body.usage.cost;
      const toolCalls = parseToolCalls(message);
      return {
        toolCalls,
        text: message.content || "",
        costUsd,
        dryRun: false,
        model: body.model || employee.model,
        usage: body.usage || {},
        id: body.id,
      };
    },
  };
}
