const LINES = {
  nova: "Ceiling's hit. Working copy stays on my desk until tomorrow.",
  kessler: "Studio sleeping. Dist stays the last green. I will not rubber-stamp a night write.",
  mira: "We are over budget. Backlog freezes. One tool, still.",
  jules: "Lights down. Furniture budget waits with the card. No standing desk tonight.",
};

const NOTES = {
  nova: "Over budget. Tomorrow: smallest change that still lets someone download a page.",
  kessler: "Over budget. Recheck download and the empty Docs page before the next green.",
  mira: "Over budget. Do not invent formulas while the studio sleeps.",
  jules: "Over budget. Two furniture credits stay two.",
};

export function stubCall(employee, kind = "say") {
  const id = employee?.id || "mira";
  if (kind === "journal") {
    return {
      name: "journal",
      arguments: { text: NOTES[id] || NOTES.mira },
    };
  }
  return {
    name: "say",
    arguments: { message: LINES[id] || LINES.mira },
  };
}

export function stubLine(employee) {
  return stubCall(employee, "say").arguments.message;
}
