/** Scripted afternoon. $0. Invoice, notes, sit/pair, Jules still edits the room. */

import { applyNovaInvoiceTweak } from "./invoice-tweak.js";

const SCRIPTS = {
  mira: [
    [
      {
        name: "say",
        arguments: {
          message: "Invoice first. Kessler, pair at the table with me.",
          to: "kessler",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Invoice prints cream. Notes make a list. No payments. No AI write." },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "No payments. No AI. Coffee, then I cut the rest.",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Notes stay a catalogue tool. Not in the rail." },
      },
      {
        name: "journal",
        arguments: {
          text: "Cut: payments, AI write, slide animation. Keep invoice print and notes.",
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
      { name: "read_file", arguments: { path: "product/invoice.html" } },
      {
        name: "say",
        arguments: {
          message: "Invoice prints cream. Mira, walk the board with me.",
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
          message: "Need caffeine. Invoice has to keep the totals.",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Invoice, notes, print margins. Working beats AI.",
        },
      },
    ],
  ],
  kessler: [
    [
      {
        name: "say",
        arguments: {
          message: "Print margins clip the total at the table. Mira, look.",
          to: "mira",
        },
      },
      {
        name: "add_task",
        arguments: { text: "Bug: invoice print margins clip the total." },
      },
      {
        name: "journal",
        arguments: {
          text: "Bug: print margins eat the total. Notes must keep owners.",
        },
      },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "I'll try notes after I print. Board stays honest.",
        },
      },
      { name: "read_file", arguments: { path: "backlog.json" } },
    ],
    [
      {
        name: "say",
        arguments: {
          message: "Coffee with Jules. Margins still clip the total.",
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
          message: "Board is this afternoon. Invoice and notes.",
        },
      },
      {
        name: "edit_office",
        arguments: {
          whiteboard: "SHIP: invoice · notes",
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
          whiteboard: "SHIP: invoice · notes",
        },
      },
      {
        name: "journal",
        arguments: {
          text: "Tidied. Board says invoice and notes. Standing desk still no.",
        },
      },
    ],
  ],
};

const WRAP_BEATS = [
  { name: "say", arguments: { message: "Coffee. Checking the invoice again." } },
  { name: "say", arguments: { message: "Back at the desk. I'll sit and finish the invoice." } },
  {
    name: "journal",
    arguments: { text: "Dry-run afternoon. Still watching invoice print. Changing the note." },
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
        path: "product/invoice.html",
        contents: applyNovaInvoiceTweak(html),
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
    step = novaShipCalls(ctx.invoiceHtml || ctx.productHtml || ctx.docsHtml);
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
          ? { text: `Dry-run afternoon ${n + 1}. Still watching invoice print. Changing the note.` }
          : {}),
      },
    });
  }
  return step;
}

export function createDryRunDriver({ readProduct, readSheets, readInvoice } = {}) {
  const seen = new Map();
  return {
    complete({ employee }) {
      const n = seen.get(employee.id) || 0;
      seen.set(employee.id, n + 1);
      let productHtml = "";
      let sheetsHtml = "";
      let invoiceHtml = "";
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
      return {
        toolCalls: dryRunCalls(employee.id, n, { productHtml, sheetsHtml, invoiceHtml }),
        text: "",
        costUsd: 0,
        dryRun: true,
        model: "dry-run",
      };
    },
  };
}
