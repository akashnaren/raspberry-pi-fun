/** Scripted evening. $0. Kanban, units, sit/pair, Jules still edits the room. */

import { applyNovaUnitsTweak } from "./units-tweak.js";

const SCRIPTS = {
  mira: [
    [
      {
        name: "say",
        arguments: {
          message: "Friday ship is three columns. Nova, walk the board.",
          to: "nova",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Kanban for Friday. Backlog, Doing, Done. No accounts." },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Coffee with Jules. No accounts on the board.",
          to: "jules",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Units stay in the catalogue. Demo rates, not live FX." },
      },
      {
        name: "journal",
        arguments: {
          text: "Cut accounts and live rates. Kanban and units stay on this machine.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Take a break on the couch. Friday still ships.",
        },
      },
    ],
  ],
  nova: [
    [
      { name: "read_file", arguments: { path: "product/units.html" } },
      {
        name: "say",
        arguments: {
          message: "Moved the units card to Doing. Degree labels ship with it.",
          to: "mira",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Back at the desk. I'll sit and finish the units page.",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Moved the units card. Labels say °F, not deg.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Coffee. The demo rates stay labeled.",
        },
      },
    ],
  ],
  kessler: [
    [
      {
        name: "say",
        arguments: {
          message: "Reject the label deg. Say °F. I'm at the table.",
          to: "nova",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Bug: unit label deg is fuzzy. Say °F." },
      },
      {
        name: "journal",
        arguments: {
          text: "Fuzzy labels fail the table. °F, °C, km, mi.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "I'll try the kanban after the table. Three columns, or it fails.",
        },
      },
      { name: "read_file", arguments: { path: "backlog.json" } },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Coffee with Jules. The deg label still fails.",
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
          message: "Plant was crowding the coffee.",
          to: "nova",
        },
      },
      {
        name: "edit_office",
        arguments: { move: { kind: "plant", from: { x: 7, y: 13 }, x: 1, y: 15 } },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Board is this evening. Kanban and units.",
        },
      },
      {
        name: "edit_office",
        arguments: {
          whiteboard: "SHIP: kanban · units",
          deskItem: { owner: "mira", item: "sticky_notes" },
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Beanbag off the coffee aisle. Kessler, the machine is clear.",
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
          message: "Coffee stays put. The board already says the ship.",
        },
      },
      {
        name: "edit_office",
        arguments: {
          whiteboard: "SHIP: kanban · units",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Tidied the coffee aisle. Board says kanban and units.",
        },
      },
    ],
  ],
};

const WRAP_BEATS = [
  { name: "say", arguments: { message: "Coffee. Checking the unit labels again." } },
  { name: "say", arguments: { message: "Back at the desk. I'll sit and move the next card." } },
  {
    name: "journal",
    arguments: { text: "Dry-run evening. Still watching kanban and units." },
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
        path: "product/units.html",
        contents: applyNovaUnitsTweak(html),
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
    step = novaShipCalls(ctx.unitsHtml);
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
          ? { text: `Dry-run evening ${n + 1}. Still watching kanban and units.` }
          : {}),
      },
    });
  }
  return step;
}

export function createDryRunDriver({ readProduct, readSheets, readInvoice, readUnits } = {}) {
  const seen = new Map();
  return {
    complete({ employee }) {
      const n = seen.get(employee.id) || 0;
      seen.set(employee.id, n + 1);
      let productHtml = "";
      let sheetsHtml = "";
      let invoiceHtml = "";
      let unitsHtml = "";
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
      if (typeof readInvoice === "function") {
        try {
          invoiceHtml = readInvoice() || "";
        } catch {
          invoiceHtml = "";
        }
      }
      if (typeof readUnits === "function") {
        try {
          unitsHtml = readUnits() || "";
        } catch {
          unitsHtml = "";
        }
      }
      return {
        toolCalls: dryRunCalls(employee.id, n, { productHtml, sheetsHtml, invoiceHtml, unitsHtml }),
        text: "",
        costUsd: 0,
        dryRun: true,
        model: "dry-run",
      };
    },
  };
}
