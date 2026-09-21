const SCRIPTS = {
  mira: [
    [
      {
        name: "say",
        arguments: {
          message: "Stamp stays a timestamp tool. If it grows a settings page I will cut it.",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Accept fractional Unix seconds without rounding away the millis." },
      },
    ],
    [
      {
        name: "journal",
        arguments: {
          text: "Nova wants polish. Kessler wants edge cases. I want one screen that converts a time and copies it.",
        },
      },
    ],
  ],
  nova: [
    [
      { name: "read_file", arguments: { path: "product/index.html" } },
      {
        name: "journal",
        arguments: {
          text: "Seed is a single file. I will not split it. Next: clearer copy on the epoch field.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Working copy is on my desk. Dist stays green until Kessler says otherwise.",
        },
      },
    ],
  ],
  kessler: [
    [
      {
        name: "say",
        arguments: {
          message: "Using the last green build, not Nova's desk. Timezone offsets still look thin.",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Reject timestamps that overflow JS Date instead of showing Invalid Date." },
      },
    ],
    [
      { name: "read_file", arguments: { path: "backlog.json" } },
      {
        name: "journal",
        arguments: {
          text: "I will file overflow and fractional seconds before I close anything. Shipping a lying clock is worse than shipping late.",
        },
      },
    ],
  ],
};

export function createDryRunDriver() {
  const seen = new Map();
  return {
    complete({ employee }) {
      const n = seen.get(employee.id) || 0;
      seen.set(employee.id, n + 1);
      const sequence = SCRIPTS[employee.id] || SCRIPTS.mira;
      const step = sequence[n % sequence.length].map((call) => ({ ...call }));
      if (n >= sequence.length) {
        step.unshift({
          name: "journal",
          arguments: {
            text: `Dry-run turn ${n + 1}. Still watching Stamp. Not repeating myself.`,
          },
        });
      }
      return {
        toolCalls: step,
        text: "",
        costUsd: 0,
        dryRun: true,
        model: "dry-run",
      };
    },
  };
}
