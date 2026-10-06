import fs from "fs";
import { register } from "node:module";

await register("./ts-resolve.mjs", import.meta.url);
const { chatBody } = await import("./src/history.ts");

const options = {
  model: "flash",
  effort: "medium",
  mode: "auto",
  sys: "Be brief.",
};

const turns = [
  { role: "user", content: "hello", hidden: "" },
  { role: "assistant", content: "Hello there." },
  { role: "assistant", content: "Hello there.", stopped: true },
  { role: "user", content: "hello there", echo: true },
  { role: "assistant", content: "Hello there." },
  { role: "assistant", content: "Hello there." },
  { role: "user", content: "Where is the bench?", hidden: "page 2" },
];

const spoken = chatBody(turns, { ...options, spoken: true });
const typed = chatBody(turns, { ...options, spoken: false });
if (JSON.stringify(spoken) !== JSON.stringify(typed)) {
  throw new Error("spoken changed the chat body");
}
const roles = spoken.messages.map((row) => row.role + ":" + row.content);
if (roles[0] !== "system:Be brief.") throw new Error("system row missing: " + roles.join(" | "));
const assistants = roles.filter((row) => row.startsWith("assistant:"));
if (assistants.length !== 1 || assistants[0] !== "assistant:Hello there.") {
  throw new Error("assistant hygiene failed: " + roles.join(" | "));
}
if (!roles.includes("user:Where is the bench?\n\n---\npage 2")) {
  throw new Error("the real question was dropped: " + roles.join(" | "));
}
if (spoken.messages.some((row) => row.content === "hello there")) {
  throw new Error("echo user stayed in the body");
}

const fresh = chatBody(turns, { ...options, fresh: true });
const freshRoles = fresh.messages.map((row) => row.role);
if (freshRoles.join(",") !== "system,user") {
  throw new Error("fresh body was " + freshRoles.join(","));
}
if (!fresh.messages[1].content.startsWith("Where is the bench?")) {
  throw new Error("fresh body lost the last user");
}

const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/voice-greeting-loop.json", import.meta.url), "utf8"));
const replay = chatBody(fixture.turns, { model: "flash", effort: "medium", mode: "auto" });
const greeting = "I'm here to help with anything you need. How can I assist you today?";
const copies = replay.messages.filter((row) => row.content === greeting);
if (copies.length !== 1) throw new Error("greeting duplicates left in the replay: " + copies.length);
if (replay.messages.some((row) => row.content === "I'm here to help with anything you need.")) {
  throw new Error("a stopped partial was sent");
}
if (replay.messages.some((row) => row.content === "anything you need")) {
  throw new Error("an echo user was sent");
}
const last = replay.messages[replay.messages.length - 1];
if (!last || last.role !== "user" || last.content !== "What is the top electric car to buy") {
  throw new Error("replay did not end on the real question: " + JSON.stringify(last));
}

console.log("ok");
