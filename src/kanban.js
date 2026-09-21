/** Local kanban. Three columns. No accounts. */

export const COLUMNS = ["backlog", "doing", "done"];

const HEADS = { backlog: "Backlog", doing: "Doing", done: "Done" };

export function emptyBoard() {
  return { seq: 0, columns: { backlog: [], doing: [], done: [] } };
}

export function addCard(board, column, title) {
  const text = String(title ?? "").trim();
  if (!text || !COLUMNS.includes(column)) return board;
  const seq = (Number(board.seq) || 0) + 1;
  const columns = {};
  for (const name of COLUMNS) columns[name] = board.columns[name].slice();
  columns[column].push({ id: "c" + seq, title: text });
  return { seq, columns };
}

export function renameCard(board, id, title) {
  const text = String(title ?? "").trim();
  if (!text) return board;
  const columns = {};
  let found = false;
  for (const name of COLUMNS) {
    columns[name] = board.columns[name].map((card) => {
      if (card.id !== id) return card;
      found = true;
      return { ...card, title: text };
    });
  }
  if (!found) return board;
  return { ...board, columns };
}

export function moveCard(board, id, toColumn, index) {
  if (!COLUMNS.includes(toColumn)) return board;
  let card = null;
  const columns = {};
  for (const name of COLUMNS) {
    columns[name] = [];
    for (const item of board.columns[name]) {
      if (item.id === id) card = item;
      else columns[name].push(item);
    }
  }
  if (!card) return board;
  const dest = columns[toColumn];
  let at = dest.length;
  if (index != null && Number.isFinite(Number(index))) {
    at = Math.max(0, Math.min(dest.length, Number(index)));
  }
  dest.splice(at, 0, card);
  return { ...board, columns };
}

export function boardMarkdown(board) {
  const lines = ["# Kanban", ""];
  for (const name of COLUMNS) {
    lines.push("## " + HEADS[name]);
    const cards = board.columns[name];
    if (!cards.length) lines.push("- (empty)");
    else for (const card of cards) lines.push("- " + card.title);
    lines.push("");
  }
  return lines.join("\n").trim() + "\n";
}

export function boardJson(board) {
  return JSON.stringify({ columns: board.columns }, null, 2) + "\n";
}
