import fs from "fs";
import { speakText, spokenAnswer, startListening, turnFromRecognition } from "./src/voice.ts";

const assistant = "The hall bench is by the east window.";
const labels = ["Thinking", "Searching", "Searched", "Search failed", "Answering"];

for (const label of labels) {
  const said = spokenAnswer(assistant, label);
  if (said !== assistant) {
    throw new Error("spoken reply was " + JSON.stringify(said) + " instead of the assistant message");
  }
  if (said === label) {
    throw new Error("spoken reply used the status label " + label);
  }
}

const plain = spokenAnswer("See [the note](https://example.com/a) for **details** today.", "Searching");
if (plain !== "See the note for details today.") {
  throw new Error("spoken reply did not use the assistant message text: " + JSON.stringify(plain));
}

const heard = [];
const recognizers = [];
globalThis.SpeechSynthesisUtterance = class {
  constructor(text) {
    this.text = text;
    this.rate = 1;
  }
};
globalThis.window = {
  SpeechRecognition: class {
    constructor() {
      this.lang = "";
      this.interimResults = false;
      this.continuous = false;
      this.onresult = null;
      this.onerror = null;
      this.onend = null;
      recognizers.push(this);
    }
    start() {}
    abort() {}
  },
  speechSynthesis: {
    cancel() {},
    speak(utter) {
      heard.push(utter.text);
    },
  },
};

speakText(assistant);
if (heard.length !== 1 || heard[0] !== assistant) {
  throw new Error("speakText did not speak the assistant message: " + JSON.stringify(heard));
}
for (const label of labels) {
  if (heard[0] === label) throw new Error("speakText spoke a status label");
}

const finals = [];
const handle = startListening({
  onInterim() {},
  onFinal(text) {
    finals.push(text);
  },
  onEnd() {},
  onError() {},
});
if (!handle) throw new Error("recognition did not start");
const rec = recognizers[0];
rec.onresult({
  results: [{ isFinal: true, 0: { transcript: "  where is the bench?  " } }],
});
const turn = turnFromRecognition(finals[0] || "");
if (!turn || turn.role !== "user" || turn.content !== "where is the bench?") {
  throw new Error("finished recognition was not sent as a user turn: " + JSON.stringify(turn));
}
if (turnFromRecognition("   ") !== null) {
  throw new Error("blank recognition must not become a turn");
}

// Chrome raises no-speech when a spoken utterance ends. That error used to
// show "Voice did not catch that. Try again." and never send the words.
const blocked = [];
const rescued = [];
const second = startListening({
  onInterim() {},
  onFinal(text) {
    rescued.push(text);
  },
  onEnd() {},
  onError() {
    blocked.push("Voice did not catch that. Try again.");
  },
});
if (!second) throw new Error("second recognition did not start");
const noisy = recognizers[1];
noisy.onresult({
  results: [{ isFinal: false, 0: { transcript: "where is the bench?" } }],
});
noisy.onerror({ error: "no-speech" });
const rescuedTurn = turnFromRecognition(rescued[0] || "");
if (blocked.length || !rescuedTurn || rescuedTurn.content !== "where is the bench?") {
  throw new Error(
    "no-speech blocked the spoken turn: " + JSON.stringify({ blocked, rescued }),
  );
}

const main = fs.readFileSync(new URL("./src/main.ts", import.meta.url), "utf8");
const voiceFn = main.slice(main.indexOf("function toggleVoice"), main.indexOf('byId("go")'));
if (!voiceFn.includes("turnFromRecognition") || !voiceFn.includes("sendText(turn.content, false, true)")) {
  throw new Error("a finished recognition result is not sent as a chat turn");
}
if (voiceFn.includes("box.value")) {
  throw new Error("voice still writes the composer");
}
if (!main.includes("speakText(answer)") || !main.includes("speakText(textAccum)")) {
  throw new Error("spoken reply path does not use the assistant message");
}
if (main.includes("speakText(stageText") || main.includes('speakText("Thinking"') || main.includes('speakText("Searching"') || main.includes('speakText("Answering"')) {
  throw new Error("spoken reply path uses a status label");
}
if (main.includes("Voice did not catch that")) {
  throw new Error("the blocking voice message is still in the page");
}

console.log("ok");
