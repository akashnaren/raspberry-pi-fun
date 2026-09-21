/** Paste meeting notes → checklist of actions and owners. Local only. */

const PEOPLE = [
  { id: "nova", name: "Nova", re: /\bnova\b/i },
  { id: "kessler", name: "Kessler", re: /\bkessler\b/i },
  { id: "mira", name: "Mira", re: /\bmira\b/i },
  { id: "jules", name: "Jules", re: /\bjules\b/i },
];

export function ownerIn(text) {
  const src = String(text ?? "");
  for (const person of PEOPLE) {
    if (person.re.test(src)) return person;
  }
  return null;
}

function cleanLine(raw) {
  return String(raw ?? "")
    .trim()
    .replace(/^[-*•]\s+/, "")
    .replace(/^\[[\sxX]\]\s+/, "");
}

export function extractActions(text) {
  const actions = [];
  const seen = new Set();
  const lines = String(text ?? "").replace(/\r\n/g, "\n").split("\n");
  for (const raw of lines) {
    const line = cleanLine(raw);
    if (!line) continue;
    let body = line;
    let forced = false;
    const tagged = /^(?:action|todo|task)\s*[:—-]\s*(.+)$/i.exec(line);
    if (tagged) {
      body = tagged[1].trim();
      forced = true;
    }
    const will = /^(\w+)\s+(?:will|should|needs to|to)\s+(.+)$/i.exec(body);
    const said = /^(\w+)\s*:\s+(.+)$/i.exec(body);
    let owner = ownerIn(body);
    if (will && ownerIn(will[1])) {
      owner = ownerIn(will[1]);
      body = will[2].trim();
      forced = true;
    } else if (
      said &&
      ownerIn(said[1]) &&
      /^(cut|ship|file|check|rewrite|move|print|fix|keep|write|add|remove|pair)\b/i.test(said[2])
    ) {
      owner = ownerIn(said[1]);
      body = said[2].trim();
      forced = true;
    }
    const looksAction =
      forced ||
      /^(ship|file|cut|check|rewrite|move|print|fix|keep|write|add|remove|pair)\b/i.test(body) ||
      /\b(will|should|needs to|todo|action)\b/i.test(line);
    if (!looksAction) continue;
    body = body.replace(/[.]+$/, "").trim();
    if (!body) continue;
    const key = `${owner?.id || ""}:${body.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    actions.push({
      text: body,
      owner: owner?.name || "",
      ownerId: owner?.id || "",
    });
  }
  return actions;
}

export function actionsMarkdown(actions, title = "Action items") {
  const heading = String(title || "Action items").trim() || "Action items";
  const lines = [`# ${heading}`, ""];
  const list = actions || [];
  if (!list.length) lines.push("- [ ] (none yet)");
  for (const item of list) {
    const who = item.owner ? ` (${item.owner})` : "";
    lines.push(`- [ ] ${item.text}${who}`);
  }
  return `${lines.join("\n")}\n`;
}
