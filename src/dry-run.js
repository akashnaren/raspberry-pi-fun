/** Scripted day. $0. Docs print, paste-csv, pair walks, and furniture still hit the event log. */

import { applyNovaDocsTweak } from "./docs-tweak.js";

const SCRIPTS = {
  mira: [
    [
      {
        name: "say",
        arguments: {
          message: "Headings first. Kessler, walk the board with me.",
          to: "kessler",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Docs: H1, lists, find, print. No formulas. No AI write." },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "No formulas. No AI. Coffee, then I cut the rest.",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Paste-to-CSV stays a catalogue tool. Not in the rail." },
      },
      {
        name: "journal",
        arguments: {
          text: "Cut: formulas, AI write, slide animation. Keep print and tidy CSV.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Take a break on the couch. Scope stays cut.",
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
          message: "Headings and lists ship. Mira, print the page.",
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
          message: "Need caffeine. Print has to hide the rail.",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "H1, lists, find, print. Working beats AI.",
        },
      },
    ],
  ],
  kessler: [
    [
      {
        name: "say",
        arguments: {
          message: "Using the last green. Mira, find skipped the second heading at the table.",
          to: "mira",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Bug: find must hit every heading, then print." },
      },
      {
        name: "journal",
        arguments: {
          text: "Bug: find misses H2. Print still dumps the rail. Paste-csv must tidy spaces.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "I'll try paste-csv after I print. Board stays honest.",
        },
      },
      { name: "read_file", arguments: { path: "backlog.json" } },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Coffee with Jules. Find has to hit every heading.",
          to: "jules",
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
          message: "Board is the plan. Docs print and paste-csv.",
        },
      },
      {
        name: "edit_office",
        arguments: {
          whiteboard: "SHIP: Docs print · paste-csv",
          deskItem: { owner: "mira", item: "sticky_notes" },
        },
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
          whiteboard: "SHIP: Docs print · paste-csv",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Tidied. Board says print and paste-csv. Standing desk still no.",
        },
      },
    ],
  ],
};

const WRAP_BEATS = [
  { name: "say", arguments: { message: "Coffee. Checking the floor again." } },
  { name: "say", arguments: { message: "Back to the whiteboard. Print still first." } },
  {
    name: "journal",
    arguments: { text: "Dry-run turn. Still watching Docs print. Changing the note." },
  },
];

function novaShipCalls(html) {
  const step = SCRIPTS.nova[0].map((call) => ({
    name: call.name,
    arguments: { ...(call.arguments || {}) },
  }));
  if (html && String(html).includes("<html")) {
    step.push({
      name: "write_file",
      arguments: {
        path: "product/index.html",
        contents: applyNovaDocsTweak(html),
      },
    });
  }
  return step;
}

export function dryRunCalls(employeeId, turnIndex, ctx = {}) {
  const n = Math.max(0, Number(turnIndex) || 0);
  const sequence = SCRIPTS[employeeId] || SCRIPTS.mira;
  let step;
  if (employeeId === "nova" && n % sequence.length === 0) {
    step = novaShipCalls(ctx.productHtml || ctx.docsHtml);
  } else {
    step = sequence[n % sequence.length].map((call) => ({
      name: call.name,
      arguments: { ...(call.arguments || {}) },
    }));
  }
  if (n >= sequence.length) {
    const extra = WRAP_BEATS[n % WRAP_BEATS.length];
    step.unshift({
      name: extra.name,
      arguments: {
        ...extra.arguments,
        ...(extra.name === "journal"
          ? { text: `Dry-run turn ${n + 1}. Still watching Docs print. Changing the note.` }
          : {}),
      },
    });
  }
  return step;
}

export function createDryRunDriver({ readProduct, readSheets } = {}) {
  const seen = new Map();
  return {
    complete({ employee }) {
      const n = seen.get(employee.id) || 0;
      seen.set(employee.id, n + 1);
      let productHtml = "";
      let sheetsHtml = "";
      if (typeof readSheets === "function") {
        try {
          sheetsHtml = readSheets() || "";
        } catch {
          sheetsHtml = "";
        }
      }
      if (typeof readProduct === "function") {
        try {
          productHtml = readProduct() || "";
        } catch {
          productHtml = "";
        }
      }
      return {
        toolCalls: dryRunCalls(employee.id, n, { productHtml, sheetsHtml }),
        text: "",
        costUsd: 0,
        dryRun: true,
        model: "dry-run",
      };
    },
  };
}
