/** Scripted turns. $0. Walks and furniture edits still hit the event log. */

const SCRIPTS = {
  mira: [
    [
      {
        name: "say",
        arguments: {
          message: "Docs first. Kessler, walk the board with me.",
          to: "kessler",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Print from Docs has to work. No extra chrome." },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Coffee. Then I cut anything that looks like a spreadsheet.",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Nova will ship tonight. Kessler will reject a loose green. Backlog stays Docs.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Take a break on the couch. Sheets wait.",
        },
      },
    ],
  ],
  nova: [
    [
      { name: "read_file", arguments: { path: "product/index.html" } },
      {
        name: "say",
        arguments: {
          message: "Working copy is on my desk. Mira, Docs still ships first.",
          to: "mira",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Jules, I still want the standing desk. I ship faster on my feet.",
          to: "jules",
        },
      },
      {
        name: "request",
        arguments: { item: "standing_desk", reason: "I ship faster on my feet." },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Need caffeine. Download has to give you a file.",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Docs writes. Next: make download obvious. Working beats pretty.",
        },
      },
    ],
  ],
  kessler: [
    [
      {
        name: "say",
        arguments: {
          message: "Using the last green build. Mira, I'll review Docs at the table.",
          to: "mira",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Download must give you a file. A dead button is a fail." },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Board stays Docs first. I'll click download before I close it.",
        },
      },
      { name: "read_file", arguments: { path: "backlog.json" } },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Coffee with Jules. A lying export is worse than late.",
          to: "jules",
        },
      },
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
          message: "Plant was crowding the aisle. Nova, it's by the clock now.",
          to: "nova",
        },
      },
      {
        name: "edit_office",
        arguments: { move: { kind: "plant", from: { x: 8, y: 9 }, x: 19, y: 10 } },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Board is the plan. Coffee stays. Two furniture credits.",
        },
      },
      {
        name: "edit_office",
        arguments: { whiteboard: "SHIP: Docs. Print. Download." },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Beanbag off the meeting door. Kessler, you can still use it.",
          to: "kessler",
        },
      },
      {
        name: "edit_office",
        arguments: { move: { kind: "beanbag", from: { x: 10, y: 13 }, x: 12, y: 13 } },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Put the plant back. I'm not buying a standing desk.",
        },
      },
      {
        name: "edit_office",
        arguments: {
          move: { kind: "plant", from: { x: 19, y: 10 }, x: 8, y: 9 },
          whiteboard: "SHIP: Meridian Office — Docs first",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Nova will ask again. Budget stays two until something ships.",
        },
      },
    ],
  ],
};

const WRAP_BEATS = [
  { name: "say", arguments: { message: "Coffee. Checking the floor again." } },
  { name: "say", arguments: { message: "Back to the whiteboard. Docs still first." } },
  {
    name: "journal",
    arguments: { text: "Dry-run turn. Still watching Docs. Changing the note." },
  },
];

export function dryRunCalls(employeeId, turnIndex) {
  const n = Math.max(0, Number(turnIndex) || 0);
  const sequence = SCRIPTS[employeeId] || SCRIPTS.mira;
  const step = sequence[n % sequence.length].map((call) => ({
    name: call.name,
    arguments: { ...(call.arguments || {}) },
  }));
  if (n >= sequence.length) {
    const extra = WRAP_BEATS[n % WRAP_BEATS.length];
    step.unshift({
      name: extra.name,
      arguments: { ...extra.arguments, ...(extra.name === "journal" ? { text: `Dry-run turn ${n + 1}. Still watching Docs. Changing the note.` } : {}) },
    });
  }
  return step;
}

export function createDryRunDriver() {
  const seen = new Map();
  return {
    complete({ employee }) {
      const n = seen.get(employee.id) || 0;
      seen.set(employee.id, n + 1);
      return {
        toolCalls: dryRunCalls(employee.id, n),
        text: "",
        costUsd: 0,
        dryRun: true,
        model: "dry-run",
      };
    },
  };
}
