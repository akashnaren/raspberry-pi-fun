const SCRIPTS = {
  mira: [
    [
      {
        name: "say",
        arguments: {
          message: "One tool. Paste a time, see five cities. If it grows a settings page I cut it.",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Saved-city list stays at five. No add-city form this week." },
      },
    ],
    [
      {
        name: "journal",
        arguments: {
          text: "Nova will ship tonight. Kessler will reject a loose green. I will keep the backlog to one screen.",
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
          text: "Seed converts. Next: clearer empty-city copy. Working beats pretty.",
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
      {
        name: "request",
        arguments: { item: "standing_desk", reason: "I ship faster on my feet." },
      },
    ],
  ],
  kessler: [
    [
      {
        name: "say",
        arguments: {
          message: "Using the last green build, not Nova's desk. Unknown cities still fail quietly.",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Unknown city must say so. Do not invent an offset." },
      },
    ],
    [
      { name: "read_file", arguments: { path: "backlog.json" } },
      {
        name: "journal",
        arguments: {
          text: "I will file empty-city and bad-offset before I close anything. A lying clock is worse than late.",
        },
      },
    ],
  ],
  jules: [
    [
      { name: "read_file", arguments: { path: "requests.json" } },
      {
        name: "say",
        arguments: {
          message: "Two furniture credits. A standing desk waits. I can offer a plant.",
        },
      },
    ],
    [
      {
        name: "journal",
        arguments: {
          text: "Nova will ask again. Budget stays two until something ships.",
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
            text: `Dry-run turn ${n + 1}. Still watching Timezone Buddy. Changing the note.`,
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
