/** Scripted day. $0. Walks, Sheets ship, backlog, and furniture still hit the event log. */

import { applyNovaSheetsTweak } from "./sheets-tweak.js";

const SCRIPTS = {
  mira: [
    [
      {
        name: "say",
        arguments: {
          message: "Sheets first. Kessler, walk the board with me.",
          to: "kessler",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Paste-to-grid ships. Formulas wait. A one-cell paste is a fail." },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Backlog is set. Invoice waits. Coffee, then I cut formulas.",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Invoice print page only after a stranger can paste a column." },
      },
      {
        name: "journal",
        arguments: {
          text: "Backlog: paste-to-grid, then invoice. Formulas wait. Nova ships the grid. Kessler greens a real paste.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Take a break on the couch. Slides stay a short deck.",
        },
      },
    ],
  ],
  nova: [
    [
      { name: "read_file", arguments: { path: "product/sheets.html" } },
      {
        name: "say",
        arguments: {
          message: "I shipped the grid. Mira, paste a column.",
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
          message: "Need caffeine. Paste has to fill more than one cell.",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Sheets writes. Paste fills from the selected cell. Working beats formulas.",
        },
      },
    ],
  ],
  kessler: [
    [
      {
        name: "say",
        arguments: {
          message: "Using the last green build. Mira, paste only filled one cell at the table.",
          to: "mira",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Bug: paste must fill a block from the selected cell, not one square." },
      },
      {
        name: "journal",
        arguments: {
          text: "Bug note: a one-cell paste is a fail. Quoted commas have to stay in one cell.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Board stays a grid you can paste into. I'll download the csv before I close it.",
        },
      },
      { name: "read_file", arguments: { path: "backlog.json" } },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Coffee with Jules. A one-cell paste is a fail.",
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
          message: "Board is the plan. Tidied Mira's stickies. Coffee stays. Two furniture credits.",
        },
      },
      {
        name: "edit_office",
        arguments: {
          whiteboard: "SHIP: Docs · Sheets. Paste a grid.",
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
          whiteboard: "SHIP: Meridian Office — Docs · Sheets",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Tidied. Nova will ask again. Budget stays two until the grid ships.",
        },
      },
    ],
  ],
};

const WRAP_BEATS = [
  { name: "say", arguments: { message: "Coffee. Checking the floor again." } },
  { name: "say", arguments: { message: "Back to the whiteboard. Sheets still first." } },
  {
    name: "journal",
    arguments: { text: "Dry-run turn. Still watching the grid. Changing the note." },
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
        path: "product/sheets.html",
        contents: applyNovaSheetsTweak(html),
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
    step = novaShipCalls(ctx.sheetsHtml || ctx.productHtml);
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
          ? { text: `Dry-run turn ${n + 1}. Still watching the grid. Changing the note.` }
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
