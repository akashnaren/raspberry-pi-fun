const SCRIPTS = {
  mira: [
    [
      {
        name: "say",
        arguments: {
          message: "Docs first. If it grows a spreadsheet this week I cut it.",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Print from Docs has to work. No extra chrome." },
      },
    ],
    [
      {
        name: "journal",
        arguments: {
          text: "Nova will ship tonight. Kessler will reject a loose green. Backlog stays Docs.",
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
          text: "Docs writes. Next: make download obvious. Working beats pretty.",
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
          message: "Using the last green build, not Nova's desk. I'll review Docs at the table.",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Download must give you a file. A dead button is a fail." },
      },
    ],
    [
      { name: "read_file", arguments: { path: "backlog.json" } },
      {
        name: "journal",
        arguments: {
          text: "I will click download before I close anything. A lying export is worse than late.",
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
          message: "Two furniture credits. Coffee stays. I can offer a plant, not a standing desk.",
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
            text: `Dry-run turn ${n + 1}. Still watching Docs. Changing the note.`,
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
