import fs from "fs";
import { isSoloStop, speakText, spokenAnswer, startListening, turnFromRecognition } from "./src/voice.ts";

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

if (!isSoloStop("stop") || !isSoloStop("Stop.") || !isSoloStop("  stop  ")) {
  throw new Error("stop alone must end voice mode");
}
if (isSoloStop("stop please") || isSoloStop("where is the bench?") || isSoloStop("   ")) {
  throw new Error("only the word stop ends voice mode");
}

const main = fs.readFileSync(new URL("./src/main.ts", import.meta.url), "utf8");
const html = fs.readFileSync(new URL("./index.html", import.meta.url), "utf8");
const scss = fs.readFileSync(new URL("./src/styles.scss", import.meta.url), "utf8");

function sliceFn(source, startName, endName) {
  const start = source.indexOf("function " + startName);
  const end = source.indexOf("function " + endName);
  if (start < 0 || end < 0 || end <= start) {
    throw new Error("missing " + startName);
  }
  return source.slice(start, end);
}

const dictation = sliceFn(main, "toggleVoice", "endVoiceMode");
if (!dictation.includes("box.value")) {
  throw new Error("dictation does not write the message box");
}
if (dictation.includes("sendText(") || dictation.includes("void send(") || dictation.includes("speakText(")) {
  throw new Error("dictation sends or speaks");
}
const ending = sliceFn(main, "endVoiceMode", "toggleVoiceMode");
if (ending.includes("sendText(") || ending.includes("speakText(") || ending.includes("void send(")) {
  throw new Error("ending voice mode sends a turn");
}
const goAt = main.indexOf('byId("go")');
const sessionStart = main.indexOf("function endVoiceMode");
if (sessionStart < 0 || goAt < sessionStart) throw new Error("missing endVoiceMode");
const session = main.slice(sessionStart, goAt);
if (!session.includes("sendText(turn.content, false, true)")) {
  throw new Error("voice mode does not send the utterance");
}
if (!session.includes("beginVoice()")) {
  throw new Error("voice mode does not listen again");
}
const begin = session.slice(session.indexOf("function beginVoice"));
const stopAt = begin.indexOf("isSoloStop");
const sendAt = begin.indexOf("sendText(turn.content, false, true)");
if (stopAt < 0 || sendAt < 0 || stopAt > sendAt || !begin.includes("endVoiceMode()")) {
  throw new Error("saying stop is sent as a chat turn");
}
if (!session.includes("if (voiceOn)") || !session.includes("endVoiceMode();")) {
  throw new Error("pressing voice mode again does not end the session");
}
if (!main.includes("whenSpeechEnds(releaseVoice)")) {
  throw new Error("voice mode does not listen again after the reply");
}
if (!html.includes('id="btnVoice"') || !html.includes('aria-label="Voice"')) {
  throw new Error("dictation control is missing");
}
if (!html.includes('id="btnVoiceMode"') || !html.includes('aria-label="Voice mode"')) {
  throw new Error("voice mode control is missing");
}
if (!html.includes('id="voiceStage"') || !scss.includes(".voice-stage.heard") || !main.includes('classList.toggle("heard"') || !main.includes('classList.toggle("voice-session"')) {
  throw new Error("voice mode has no speaking visualization");
}
for (const level of ["low", "medium", "high"]) {
  if (!html.includes('data-think="' + level + '"')) {
    throw new Error("Low / Medium / High missing");
  }
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
