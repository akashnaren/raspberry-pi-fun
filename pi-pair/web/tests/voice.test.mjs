import fs from "fs";
import { END_OF_UTTERANCE_SILENCE_MS, ENDPOINT_MS, adaptiveEndOfUtterance, createUtteranceHold, currentSpeech, dropPostSpeechEcho, echoOfSpeech, endOfUtteranceSilence, firstSpokenSentence, isSoloStop, noteSpokenDelta, setEndOfUtteranceSilence, shouldBargeIn, speakText, spokenAnswer, startListening, stopSpeaking, turnFromRecognition, whenSpeechPulses, whenSpeechStarts } from "../src/voice/speech.ts";

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

const main = fs.readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const html = fs.readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const scss = fs.readFileSync(new URL("../src/styles/main.scss", import.meta.url), "utf8");

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
if (html.includes('id="btnVoiceMode"')) {
  throw new Error("a second voice-mode button sits next to dictation");
}
if (!main.includes("toggleVoiceMode") || !main.includes("shouldBargeIn")) {
  throw new Error("voice mode lost its turn or barge-in");
}
if (!html.includes('id="voiceStage"') || !scss.includes(".voice-stage.heard") || !main.includes('setVoiceState("heard"') || !main.includes('classList.toggle("voice-session"')) {
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

const voiceSrc = fs.readFileSync(new URL("../src/voice/speech.ts", import.meta.url), "utf8");
if (!Number.isFinite(ENDPOINT_MS) || ENDPOINT_MS >= 1000) {
  throw new Error("wake-to-listen still waits on a long silence");
}
const woke = [];
const beforeRec = recognizers.length;
const wakeHandle = startListening({
  onInterim() {},
  onFinal(text) { woke.push(text); },
  onEnd() {},
  onError() { woke.push("error"); },
});
if (!wakeHandle) throw new Error("wake listen did not start");
recognizers[beforeRec].onresult({
  results: [{ isFinal: false, 0: { transcript: "where is the bench?" } }],
});
await new Promise((resolve) => setTimeout(resolve, ENDPOINT_MS + 250));
if (woke[0] !== "where is the bench?") {
  throw new Error("wake-to-listen waited on the long silence: " + JSON.stringify(woke));
}
recognizers[beforeRec].onresult({
  results: [{ isFinal: true, 0: { transcript: "where is the bench?" } }],
});
if (woke.length !== 1) {
  throw new Error("the quiet commit was sent twice: " + JSON.stringify(woke));
}
wakeHandle.stop();

const order = [];
let uttered = null;
let phase = "before";
let pulses = 0;
whenSpeechStarts(() => { phase = "audio"; });
whenSpeechPulses(() => { pulses += 1; });
window.speechSynthesis = {
  speaking: false,
  pending: false,
  getVoices() {
    return [{ default: true, lang: "en-US", localService: true, name: "Default" }];
  },
  cancel() { order.push("cancel"); },
  resume() { order.push("resume"); },
  speak(utter) { order.push("speak"); uttered = utter; },
};
if (!speakText("Hello from the speaker.")) {
  throw new Error("reply was not spoken");
}
const speakAt = order.indexOf("speak");
if (speakAt < 0 || order[speakAt - 1] === "cancel") {
  throw new Error("cancel silenced the speaker: " + order.join(","));
}
if (!uttered || !uttered.voice || uttered.voice.default !== true || uttered.volume !== 1) {
  throw new Error("spoken output is not aimed at the default speaker");
}
if (order[speakAt - 1] !== "resume") {
  throw new Error("speaker was not resumed onto the default output: " + order.join(","));
}
if (phase !== "before" || pulses !== 0) {
  throw new Error("speaking state started before the audio");
}
uttered.onstart();
if (phase !== "audio") {
  throw new Error("speaking state did not follow the reply audio");
}
uttered.onboundary();
if (pulses !== 1) {
  throw new Error("speaking cue did not move with the audio");
}
if (!voiceSrc.includes("utter.onstart") || !voiceSrc.includes("utter.onboundary")) {
  throw new Error("speaking cue is not tied to the reply audio");
}
if (!main.includes('setVoiceState("speaking"') || !main.includes("ensureOrb()?.pulse()")) {
  throw new Error("the UI has no distinct speaking state");
}
if (!scss.includes(".voice-stage.speaking") || !scss.includes("#voiceOrb") || scss.includes(".voice-dots")) {
  throw new Error("speaking state still uses the five dots");
}
if (!html.includes('id="voiceOrb"') || html.includes("voice-dots")) {
  throw new Error("the page still shows the five dots");
}
if (!scss.includes(".voice-stage.speaking .voice-live") || !html.includes('aria-live="polite"')) {
  throw new Error("speaking caption was removed");
}
const earlySpeak = main.indexOf("noteSpokenDelta(textAccum");
const fullSpeak = main.indexOf("speakText(textAccum)");
if (earlySpeak < 0 || fullSpeak < 0 || earlySpeak > fullSpeak) {
  throw new Error("voice waits for the whole reply before speaking");
}

// Stubbed pi4 stream: first token is slow, then a word at a time.
// Before: speakText runs only after the last word. After: the first sentence starts audio.
const FIRST_TOKEN_MS = 800;
const TOKEN_MS = 35;
const words = "The hall bench is by the east window. It sits under the tall window and the rest of this reply keeps going so the old path waits for the whole answer.".split(" ");
let buf = "";
let streamMs = 0;
let firstAudioMs = null;
stopSpeaking();
window.speechSynthesis = {
  speaking: false,
  pending: false,
  getVoices() {
    return [{ default: true, lang: "en-US", localService: true, name: "Default" }];
  },
  cancel() {},
  resume() {},
  speak() {
    if (firstAudioMs == null) firstAudioMs = streamMs;
  },
};
words.forEach((word, index) => {
  streamMs += index === 0 ? FIRST_TOKEN_MS : TOKEN_MS;
  buf += (buf ? " " : "") + word;
  if (firstAudioMs == null) noteSpokenDelta(buf);
});
if (!firstSpokenSentence(buf)) throw new Error("the stub reply has no spoken sentence");
const beforeMs = ENDPOINT_MS + streamMs;
const afterMs = ENDPOINT_MS + firstAudioMs;
console.log("end-of-speech to first audio before " + beforeMs + "ms after " + afterMs + "ms");
if (!(afterMs < beforeMs)) {
  throw new Error("first audio did not move earlier: before " + beforeMs + " after " + afterMs);
}

const silenceAt = voiceSrc.indexOf("export const END_OF_UTTERANCE_SILENCE_MS");
const silenceDoc = voiceSrc.slice(Math.max(0, silenceAt - 800), silenceAt);
if (silenceAt < 0 || !silenceDoc.includes("450") || !silenceDoc.includes("1.0")) {
  throw new Error("silence constant is not documented");
}
if (END_OF_UTTERANCE_SILENCE_MS < 1000 || END_OF_UTTERANCE_SILENCE_MS > 1500) {
  throw new Error("end-of-utterance silence defaults to " + END_OF_UTTERANCE_SILENCE_MS + "ms, want 1.0–1.5s");
}
if (ENDPOINT_MS !== 450 || END_OF_UTTERANCE_SILENCE_MS === ENDPOINT_MS) {
  throw new Error("auto-send silence collapsed into the 450ms endpoint");
}
if (!begin.includes("createUtteranceHold") || !begin.includes("endOfUtteranceSilence()")) {
  throw new Error("voice mode auto-sends on the 450ms endpoint");
}
if (dictation.includes("createUtteranceHold") || dictation.includes("endOfUtteranceSilence")) {
  throw new Error("dictation waits to auto-send");
}

function fakeClock() {
  const timers = [];
  let next = 1;
  return {
    timers,
    clock: {
      set(fn, ms) {
        const id = next;
        next += 1;
        timers.push({ id, fn, ms, cleared: false });
        return id;
      },
      clear(id) {
        const row = timers.find((item) => item.id === id);
        if (row) row.cleared = true;
      },
    },
  };
}

const sent = [];
const paced = fakeClock();
const hold = createUtteranceHold((text) => sent.push(text), END_OF_UTTERANCE_SILENCE_MS, paced.clock);
if (hold.interim("where is") !== "where is") {
  throw new Error("interim phrase was dropped");
}
if (sent.length !== 0 || paced.timers.length !== 1 || paced.timers[0].ms !== END_OF_UTTERANCE_SILENCE_MS) {
  throw new Error("auto-send did not wait for the long silence: " + JSON.stringify(paced.timers));
}
const firstQuiet = paced.timers[0];
if (hold.final("where is") !== "where is" || firstQuiet.cleared || sent.length !== 0) {
  throw new Error("the same finalized phrase restarted the silence");
}
if (hold.interim("the bench") !== "where is the bench" || !firstQuiet.cleared) {
  throw new Error("new words did not restart the end-of-utterance silence");
}
const liveQuiet = paced.timers.filter((item) => !item.cleared);
if (liveQuiet.length !== 1 || liveQuiet[0].ms !== END_OF_UTTERANCE_SILENCE_MS) {
  throw new Error("restarted silence was not the documented wait");
}
firstQuiet.fn();
if (sent.length !== 0) {
  throw new Error("the 450ms-scale timer still sent the short phrase");
}
liveQuiet[0].fn();
if (sent.length !== 1 || sent[0] !== "where is the bench") {
  throw new Error("silence did not send one joined utterance: " + JSON.stringify(sent));
}
liveQuiet[0].fn();
if (sent.length !== 1) {
  throw new Error("the utterance was sent twice");
}

const punctuated = [];
const marked = fakeClock();
const markedHold = createUtteranceHold((text) => punctuated.push(text), 1200, marked.clock);
markedHold.interim("where is");
const markedQuiet = marked.timers[0];
if (markedHold.final("Where is?") !== "Where is?" || markedQuiet.cleared) {
  throw new Error("punctuation on the same phrase restarted the silence: " + markedHold.text());
}
markedHold.interim("the bench");
if (!markedQuiet.cleared || markedHold.text() !== "Where is? the bench") {
  throw new Error("new words were not joined after the finalized phrase: " + markedHold.text());
}

const dropped = [];
const cancelled = fakeClock();
const abandoned = createUtteranceHold((text) => dropped.push(text), 1200, cancelled.clock);
abandoned.final("hello");
abandoned.cancel();
const stale = cancelled.timers.find((item) => !item.cleared);
if (stale) stale.fn();
cancelled.timers.forEach((item) => {
  item.fn();
});
if (dropped.length || abandoned.text()) {
  throw new Error("cancelled speech still auto-sent: " + JSON.stringify(dropped));
}

const immediate = [];
const eager = createUtteranceHold((text) => immediate.push(text), 0, fakeClock().clock);
if (eager.interim("where is") !== "where is" || immediate.length !== 0) {
  throw new Error("a zero wait sent on the interim");
}
if (eager.final("where is") !== "where is" || immediate[0] !== "where is") {
  throw new Error("a zero wait did not send on the commit: " + JSON.stringify(immediate));
}

setEndOfUtteranceSilence(1500);
if (endOfUtteranceSilence() !== 1500) {
  throw new Error("end-of-utterance silence is not configurable");
}
const configured = [];
const custom = fakeClock();
const customHold = createUtteranceHold((text) => configured.push(text), endOfUtteranceSilence(), custom.clock);
customHold.final("hello");
if (configured.length !== 0 || custom.timers[0]?.ms !== 1500) {
  throw new Error("voice mode ignored the configured silence");
}
setEndOfUtteranceSilence(-5);
setEndOfUtteranceSilence(Number.NaN);
setEndOfUtteranceSilence(20000);
if (endOfUtteranceSilence() !== 1500) {
  throw new Error("an out-of-range silence replaced the configured wait");
}
setEndOfUtteranceSilence(END_OF_UTTERANCE_SILENCE_MS);
if (endOfUtteranceSilence() !== END_OF_UTTERANCE_SILENCE_MS) {
  throw new Error("silence did not return to the default");
}

const playing = "The hall bench is by the east window.";
if (!echoOfSpeech("the hall bench", playing)) throw new Error("speaker echo was treated as a new turn");
if (shouldBargeIn("the hall bench", playing, true)) throw new Error("echo barged in");
if (!shouldBargeIn("what about the trains", playing, true)) throw new Error("a new phrase did not barge in");
if (shouldBargeIn("what about the trains", playing, false)) throw new Error("barge-in fired while silent");
if (!shouldBargeIn("stop", playing, true)) throw new Error("stop did not barge in");

const baseWait = END_OF_UTTERANCE_SILENCE_MS;
if (adaptiveEndOfUtterance("where is the bench", baseWait) !== baseWait) {
  throw new Error("an open phrase without a dangling word left the base pause");
}
const finishedWait = adaptiveEndOfUtterance("Where is the bench?", baseWait);
if (!(finishedWait < baseWait && finishedWait >= 700)) {
  throw new Error("a finished sentence did not commit sooner: " + finishedWait);
}
const openWait = adaptiveEndOfUtterance("tickets for the trains and", baseWait);
if (!(openWait > baseWait && openWait <= 2200)) {
  throw new Error("a dangling word did not wait longer: " + openWait);
}
if (adaptiveEndOfUtterance("hello", 1500) !== 1500) {
  throw new Error("a short phrase replaced the configured pause");
}
if (adaptiveEndOfUtterance("Where is the bench?", 0) !== 0) {
  throw new Error("a zero pause grew a wait");
}
const quickWait = adaptiveEndOfUtterance("where is the bench", baseWait, 200);
const pausedWait = adaptiveEndOfUtterance("where is the bench", baseWait, 1100);
if (!(quickWait > baseWait) || !(pausedWait < baseWait)) {
  throw new Error("the pause did not follow the pace: " + quickWait + " / " + pausedWait);
}

const asked = [];
let spokenAt = 5000;
const pacedQuestion = fakeClock();
pacedQuestion.clock.now = () => spokenAt;
const questionHold = createUtteranceHold((text) => asked.push(text), baseWait, pacedQuestion.clock);
questionHold.final("Where is the bench?");
if (asked.length !== 0 || pacedQuestion.timers[0]?.ms !== finishedWait) {
  throw new Error("voice mode ignored the shorter pause after a sentence: " + pacedQuestion.timers[0]?.ms);
}
spokenAt += 200;
questionHold.interim("and the trains");
const liveQuestion = pacedQuestion.timers.filter((item) => !item.cleared);
if (liveQuestion.length !== 1 || !(liveQuestion[0].ms > finishedWait)) {
  throw new Error("new words after a sentence did not adapt again: " + liveQuestion[0]?.ms);
}

const release = main.slice(main.indexOf("function releaseVoice"), main.indexOf('byId("go")'));
if (!release.includes("if (speechPending()) return")) {
  throw new Error("releaseVoice does not wait until playback ends");
}
if (/speechPending\(\)\s*\|\|\s*sending/.test(release)) {
  throw new Error("releaseVoice is gated on the request");
}
if (!release.includes("turnCtrl?.abort()")) {
  throw new Error("a barge during the reply does not cut it off");
}
const barge = main.slice(main.indexOf("function takeBarge"), main.indexOf("function armBarge"));
if (barge.includes("sending")) throw new Error("barge-in waits for the request to finish");
if (!barge.includes("stopSpeaking()")) throw new Error("barge-in leaves the reply playing");
if (!main.includes("shouldBargeIn(text, currentSpeech(), speechPending()")) {
  throw new Error("barge-in is not tied to playback");
}

const greeting = "I'm here to help with anything you need. How can I assist you today?";
stopSpeaking();
speakText(greeting);
const lateEcho = "anything you need";
if (!shouldBargeIn(lateEcho, "How can I assist you today?", true, 400)) {
  throw new Error("the old chunk check no longer misses a late echo");
}
if (shouldBargeIn(lateEcho, currentSpeech(), true, 400)) {
  throw new Error("late echo of sentence 1 barged while the full reply was queued");
}
if (shouldBargeIn("what about", currentSpeech(), true, 349)) {
  throw new Error("two words barged before 350ms");
}
if (!shouldBargeIn("what about", currentSpeech(), true, 350)) {
  throw new Error("two distinct words held 350ms did not barge");
}
if (!dropPostSpeechEcho(lateEcho, greeting, 200)) {
  throw new Error("the deaf window kept an echo final");
}
if (dropPostSpeechEcho(lateEcho, greeting, 1500)) {
  throw new Error("an echo final was dropped after the window");
}
if (dropPostSpeechEcho("what about the trains", greeting, 200)) {
  throw new Error("a new question was dropped in the deaf window");
}
if (turnFromRecognition("   ")) throw new Error("a blank transcript became a turn");
if (turnFromRecognition("hi there", { confidence: 0.2 })) {
  throw new Error("a short low-confidence transcript was kept");
}
if (!turnFromRecognition("hi there", { confidence: 0.9 })) {
  throw new Error("a confident short transcript was dropped");
}
if (!turnFromRecognition("where is the bench", { confidence: 0.1 })) {
  throw new Error("a longer phrase was dropped for confidence");
}
if (turnFromRecognition("how can I assist", { confidence: 0.99, lastAssistant: greeting })) {
  throw new Error("an echo transcript became a user turn");
}
stopSpeaking();
if (noteSpokenDelta(greeting + " The bench is next.", greeting)) {
  throw new Error("a repeated first sentence was spoken early");
}
if (currentSpeech()) throw new Error("the repeated sentence was queued");

const beginVoice = sliceFn(main, "beginVoice", "releaseVoice");
const releaseVoice = main.slice(main.indexOf("function releaseVoice"), main.indexOf('byId("go")'));
const takeBarge = main.slice(main.indexOf("function takeBarge"), main.indexOf("function armBarge"));
if (!beginVoice.includes('setVoiceState("listening"') || !beginVoice.includes('setVoiceState("heard"') || !beginVoice.includes('setVoiceState("thinking"')) {
  throw new Error("a full turn is not listening → heard → thinking");
}
if (!main.includes('setVoiceState("speaking"') || !releaseVoice.includes('setVoiceState("listening"')) {
  throw new Error("a full turn does not return through speaking to listening");
}
if (!takeBarge.includes('setVoiceState("heard"')) {
  throw new Error("a barge does not move speaking → heard");
}
if (!beginVoice.includes('setVoiceState("error", "Voice needs the microphone in this browser.")')) {
  throw new Error("mic denial does not end in error");
}
if (!main.includes("getUserMedia") || !main.includes("echoCancellation: true")) {
  throw new Error("heard state does not follow the microphone");
}

const queuedFrames = [];
let rafCalls = 0;
globalThis.requestAnimationFrame = (fn) => {
  rafCalls += 1;
  queuedFrames.push(fn);
  return rafCalls;
};
globalThis.cancelAnimationFrame = () => {};
window.matchMedia = () => ({
  matches: false,
  media: "",
  addEventListener() {},
  removeEventListener() {},
});
window.devicePixelRatio = 1;
globalThis.document = {
  visibilityState: "visible",
  documentElement: {},
  addEventListener() {},
  removeEventListener() {},
};
const { createOrb } = await import("../src/voice/orb.ts");
const canvas = {
  width: 220,
  height: 220,
  getContext() {
    return {
      setTransform() {},
      clearRect() {},
      beginPath() {},
      arc() {},
      closePath() {},
      moveTo() {},
      lineTo() {},
      fill() {},
      stroke() {},
      createRadialGradient() {
        return { addColorStop() {} };
      },
    };
  },
};
const orb = createOrb(canvas);
if (rafCalls !== 0) throw new Error("the orb looped while voice was off");
const sequence = ["listening", "heard", "thinking", "speaking", "listening"];
const seen = [];
for (const name of sequence) {
  orb.set(name);
  seen.push(orb.state());
}
if (seen.join(" → ") !== sequence.join(" → ")) {
  throw new Error("a full turn was " + seen.join(" → "));
}
if (orb.state() !== "listening") throw new Error("the turn did not end listening");
orb.set("speaking");
orb.set("heard");
if (orb.state() !== "heard") throw new Error("a barge did not land on heard");
const whileOn = rafCalls;
orb.set("idle");
const parked = rafCalls;
for (const frame of queuedFrames.splice(0)) frame(32);
if (rafCalls !== parked) throw new Error("voice off still scheduled frames: " + rafCalls + " after " + whileOn);
document.visibilityState = "hidden";
orb.set("listening");
if (rafCalls !== parked) throw new Error("a hidden tab started the orb loop");
orb.destroy();

console.log("ok");
