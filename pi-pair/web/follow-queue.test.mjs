import { parseHTML } from "linkedom";
import { dropFollow, enqueueFollow, FOLLOW_LIMIT, renderFollowQueue, takeFollow } from "./src/follow-queue.ts";

let queue = [];
let nextId = 1;
function add(text) {
  const result = enqueueFollow(queue, text, nextId);
  queue = result.items;
  nextId = result.nextId;
  return result.added;
}

if (!add("  first  ") || queue[0].text !== "first") throw new Error("trim failed");
if (!add("second") || !add("third")) throw new Error("fifo fill failed");
if (queue.length !== FOLLOW_LIMIT) throw new Error("cap drifted");
if (add("fourth")) throw new Error("a fourth message was accepted");
if (add("   ")) throw new Error("blank message was accepted");
queue = dropFollow(queue, queue[1].id);
if (queue.map((item) => item.text).join(",") !== "first,third") {
  throw new Error("drop removed the wrong item");
}
const taken = takeFollow(queue);
if (!taken.next || taken.next.text !== "first" || taken.rest.length !== 1) {
  throw new Error("take was not fifo");
}

const { document } = parseHTML("<div id='host' hidden></div>");
globalThis.document = document;
const host = document.getElementById("host");
let removed = 0;
renderFollowQueue(host, taken.rest, (id) => {
  removed = id;
});
const chip = host.querySelector(".follow-chip");
if (host.hidden || !chip || !chip.textContent.includes("Queued") || !chip.textContent.includes("third")) {
  throw new Error("chip did not render");
}
const button = chip.querySelector("button");
if (!button || button.getAttribute("aria-label") !== "Remove queued message") {
  throw new Error("remove control missing");
}
button.click();
if (removed !== taken.rest[0].id) throw new Error("remove did not report the id");
renderFollowQueue(host, [], () => {});
if (!host.hidden || host.childNodes.length) throw new Error("empty queue stayed visible");

console.log("ok");
