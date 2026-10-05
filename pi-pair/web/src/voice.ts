interface SpeechResult {
  isFinal: boolean;
  0?: { transcript?: string };
}

interface SpeechEvent extends Event {
  results: ArrayLike<SpeechResult>;
  resultIndex?: number;
}

interface SpeechError extends Event {
  error?: string;
}

interface SpeechRec {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((event: SpeechEvent) => void) | null;
  onerror: ((event: SpeechError) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  abort: () => void;
}

type SpeechCtor = new () => SpeechRec;

function speechWindow(): Window & {
  SpeechRecognition?: SpeechCtor;
  webkitSpeechRecognition?: SpeechCtor;
} {
  return window;
}

export function recognitionCtor(): SpeechCtor | null {
  const host = speechWindow();
  if (typeof host.SpeechRecognition === "function") return host.SpeechRecognition;
  if (typeof host.webkitSpeechRecognition === "function") return host.webkitSpeechRecognition;
  return null;
}

export function speechReady(): boolean {
  return recognitionCtor() !== null && "speechSynthesis" in window;
}

export function plainSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1")
    .replace(/[*_#>]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Read the assistant message. A stage label is not a reply. */
export function spokenAnswer(assistantText: string, stageLabel = ""): string {
  const say = plainSpeech(assistantText);
  if (!say) return "";
  if (stageLabel && say === plainSpeech(stageLabel)) return say;
  return say;
}

/** A finished recognition result becomes the next user turn, or nothing if it was blank. */
export function turnFromRecognition(transcript: string): { role: "user"; content: string } | null {
  const content = transcript.trim();
  if (!content) return null;
  return { role: "user", content };
}

export function speechKey(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
}

/** True when the mic mostly heard the line that is playing. */
export function echoOfSpeech(heard: string, spoken: string): boolean {
  const left = speechKey(heard);
  const right = speechKey(spoken);
  if (!left || !right) return false;
  if (right.includes(left)) return left.length >= 3;
  const words = left.split(" ").filter((word) => word.length > 2);
  if (!words.length) return false;
  const hit = words.filter((word) => right.includes(word)).length;
  return hit / words.length >= 0.6;
}

/** A distinct phrase while the reply is playing. Echo and tiny noises stay put. */
export function shouldBargeIn(heard: string, spoken: string, assistantSpeaking: boolean): boolean {
  const text = heard.trim();
  if (!assistantSpeaking || text.length < 2) return false;
  if (isSoloStop(text)) return true;
  if (text.length < 3) return false;
  return !echoOfSpeech(text, spoken);
}

/** The word stop, alone, ends a spoken session. */
export function isSoloStop(transcript: string): boolean {
  return transcript.trim().toLowerCase().replace(/[^a-z]/g, "") === "stop";
}

/** Quiet gap that commits an interim phrase for wake-to-listen. Not the auto-send pause. */
export const ENDPOINT_MS = 450;

/**
 * Silence after the last new words before voice mode auto-sends the turn.
 *
 * ENDPOINT_MS (450) only closes a phrase, so wake-to-listen does not sit on
 * Chrome's longer recognizer pause. Auto-send used to follow that same ~450ms
 * cut and interrupted a breath. ChatGPT-like voice waits about 1.0–1.5s.
 * This default is 1200ms, inside that window, measured from the last words
 * that changed the phrase. adaptiveEndOfUtterance shortens it after a
 * finished sentence and lengthens it when the phrase is still open. The same
 * finalized text does not restart it. More speech does. Set another wait with
 * setEndOfUtteranceSilence: 0 sends on the next commit, and values outside
 * 0–10000 are ignored. Dictation does not auto-send and does not use this pause.
 */
export const END_OF_UTTERANCE_SILENCE_MS = 1200;

const END_OF_UTTERANCE_SILENCE_MAX_MS = 10000;
let utteranceSilenceMs = END_OF_UTTERANCE_SILENCE_MS;

export function endOfUtteranceSilence(): number {
  return utteranceSilenceMs;
}

export function setEndOfUtteranceSilence(ms: number): void {
  if (!Number.isFinite(ms)) return;
  const next = Math.round(ms);
  if (next < 0 || next > END_OF_UTTERANCE_SILENCE_MAX_MS) return;
  utteranceSilenceMs = next;
}

export interface SilenceClock {
  set(fn: () => void, ms: number): number;
  clear(id: number): void;
  now?: () => number;
}

const OPEN_TAIL = /^(?:and|but|or|so|because|if|when|then|with|for|to|of|the|a|an)$/i;

/**
 * Base silence is the middle. A finished sentence commits sooner, a dangling
 * word waits longer, a quick run of words waits a little longer, and a pause
 * that already happened commits sooner.
 */
export function adaptiveEndOfUtterance(phrase: string, baseMs: number, gapMs = 0): number {
  if (!Number.isFinite(baseMs)) return 0;
  const base = Math.min(END_OF_UTTERANCE_SILENCE_MAX_MS, Math.max(0, Math.round(baseMs)));
  if (base === 0) return 0;
  const text = phrase.trim();
  if (!text) return base;
  const words = text.split(/\s+/).filter(Boolean);
  const last = (words[words.length - 1] || "").replace(/[^A-Za-z]/g, "");
  let scale = 1;
  if (/[.!?…]['")\]]*$/.test(text) && words.length >= 3) scale = 0.7;
  else if (/[,:;]['")\]]*$/.test(text) || OPEN_TAIL.test(last)) scale = 1.35;
  const gap = Number.isFinite(gapMs) ? gapMs : 0;
  if (gap >= 80 && gap < 450) scale *= 1.15;
  else if (gap >= 900) scale *= 0.85;
  if (scale === 1) return base;
  const next = Math.round(base * scale);
  return Math.min(END_OF_UTTERANCE_SILENCE_MAX_MS, Math.max(1, next));
}

export interface UtteranceHold {
  interim(text: string): string;
  final(text: string): string;
  cancel(): void;
  text(): string;
}

function joinPhrase(head: string, tail: string): string {
  const left = head.trim();
  const right = tail.trim();
  if (left && right) return left + " " + right;
  return left || right;
}

function phraseKey(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9\s]/g, "").replace(/\s+/g, " ").trim();
}

function commitPiece(stable: string, live: string, piece: string): { stable: string; live: string } {
  const liveKey = phraseKey(live);
  const pieceKey = phraseKey(piece);
  const stableKey = phraseKey(stable);
  if (liveKey && (pieceKey === liveKey || pieceKey.startsWith(liveKey) || liveKey.startsWith(pieceKey))) {
    return { stable: joinPhrase(stable, piece), live: "" };
  }
  if (pieceKey && pieceKey === stableKey) return { stable, live: "" };
  if (stableKey && pieceKey.startsWith(stableKey)) return { stable: piece, live: "" };
  return { stable: joinPhrase(stable, piece), live: "" };
}

/** Hold a spoken phrase until endOfUtteranceSilence, then hand it off once. */
export function createUtteranceHold(
  deliver: (text: string) => void,
  silenceMs: number = endOfUtteranceSilence(),
  clock: SilenceClock = {
    set: (fn, ms) => window.setTimeout(fn, ms),
    clear: (id) => window.clearTimeout(id),
  },
): UtteranceHold {
  let stable = "";
  let live = "";
  let timer: number | null = null;
  let open = true;
  let changedAt = 0;

  const preview = () => joinPhrase(stable, live);

  const clearTimer = () => {
    if (timer == null) return;
    clock.clear(timer);
    timer = null;
  };

  const gapSinceChange = () => {
    const now = typeof clock.now === "function" ? clock.now() : Date.now();
    const gap = changedAt > 0 ? Math.max(0, now - changedAt) : 0;
    changedAt = now;
    return gap;
  };

  const arm = () => {
    clearTimer();
    const phrase = preview();
    if (!open || !phrase) return;
    const gapMs = gapSinceChange();
    if (silenceMs <= 0) {
      stable = "";
      live = "";
      open = false;
      deliver(phrase);
      return;
    }
    const wait = adaptiveEndOfUtterance(phrase, silenceMs, gapMs);
    const id = clock.set(() => {
      if (!open || timer !== id) return;
      timer = null;
      open = false;
      const said = preview();
      stable = "";
      live = "";
      if (said) deliver(said);
    }, wait);
    timer = id;
  };

  return {
    interim(text: string) {
      if (!open) return preview();
      const before = preview();
      live = text.trim();
      const after = preview();
      if (silenceMs > 0 && after && phraseKey(after) !== phraseKey(before)) arm();
      return after;
    },
    final(text: string) {
      if (!open) return preview();
      const piece = text.trim();
      if (!piece) return preview();
      const before = preview();
      const next = commitPiece(stable, live, piece);
      stable = next.stable;
      live = next.live;
      const after = preview();
      const grew = phraseKey(after) !== phraseKey(before);
      if (after && (grew || timer == null)) arm();
      return after;
    },
    cancel() {
      open = false;
      clearTimer();
      stable = "";
      live = "";
    },
    text: preview,
  };
}

let beforeSpeech: ((text: string) => void) | null = null;
let afterSpeech: (() => void) | null = null;
let duringSpeech: (() => void) | null = null;
let queuedSay = "";
let liveUtterances = 0;

/** True while a reply is queued or playing. Voice mode may still listen to barge in. */
export function speechPending(): boolean {
  return liveUtterances > 0;
}

/** The first finished sentence, so voice can start before the rest of the reply arrives. */
export function firstSpokenSentence(text: string): string | null {
  const say = plainSpeech(text);
  if (!say) return null;
  const sentence = say.match(/^[\s\S]*?[.!?…](?=\s|$)/);
  if (!sentence) return null;
  const line = sentence[0].trim();
  return line.length >= 2 ? line : null;
}

/** Speak the first sentence as soon as it is in the stream. Later text is queued, not cancelled. */
export function noteSpokenDelta(accum: string): boolean {
  const lead = firstSpokenSentence(accum);
  if (!lead) return false;
  if (queuedSay && (lead === queuedSay || queuedSay.startsWith(lead))) return true;
  return speakText(lead);
}

export function whenSpeechStarts(fn: (text: string) => void): void {
  beforeSpeech = fn;
}

export function whenSpeechPulses(fn: () => void): void {
  duringSpeech = fn;
}

export function whenSpeechEnds(fn: () => void): void {
  afterSpeech = fn;
}

function pickSpeaker(synth: SpeechSynthesis): SpeechSynthesisVoice | null {
  const voices = typeof synth.getVoices === "function" ? synth.getVoices() : [];
  return voices.find((voice) => voice.default) || voices.find((voice) => voice.localService) || null;
}

function playUtterance(synth: SpeechSynthesis, say: string, replace: boolean): boolean {
  const utter = new SpeechSynthesisUtterance(say);
  utter.rate = 1;
  utter.volume = 1;
  const started = beforeSpeech;
  const pulse = duringSpeech;
  const done = afterSpeech;
  let settled = false;
  const finish = () => {
    if (settled) return;
    settled = true;
    done?.();
  };
  utter.onstart = () => started?.(say);
  utter.onboundary = () => pulse?.();
  utter.onend = () => {
    liveUtterances = Math.max(0, liveUtterances - 1);
    finish();
  };
  utter.onerror = () => {
    liveUtterances = Math.max(0, liveUtterances - 1);
    finish();
  };
  let played = false;
  const play = () => {
    if (played) return;
    played = true;
    liveUtterances += 1;
    const speaker = pickSpeaker(synth);
    if (speaker) utter.voice = speaker;
    if (typeof synth.resume === "function") synth.resume();
    synth.speak(utter);
  };
  // cancel() in the same turn as speak() drops the utterance on Chrome, so the reply stays silent.
  // A later sentence is queued behind the one already playing.
  if (replace && (synth.speaking || synth.pending)) {
    synth.cancel();
    setTimeout(play, 60);
  } else if (replace && !pickSpeaker(synth) && typeof synth.addEventListener === "function") {
    const onVoices = () => {
      synth.removeEventListener("voiceschanged", onVoices);
      play();
    };
    synth.addEventListener("voiceschanged", onVoices);
    setTimeout(play, 200);
  } else {
    play();
  }
  return true;
}

export function speakText(text: string): boolean {
  if (!("speechSynthesis" in window)) return false;
  const say = spokenAnswer(text);
  if (!say) return false;
  const synth = window.speechSynthesis;
  if (queuedSay && say.startsWith(queuedSay)) {
    const rest = say.slice(queuedSay.length).trim();
    if (!rest) return true;
    queuedSay = say;
    return playUtterance(synth, rest, false);
  }
  queuedSay = say;
  return playUtterance(synth, say, true);
}

export function stopSpeaking(): void {
  queuedSay = "";
  liveUtterances = 0;
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}

export interface ListenHandlers {
  onInterim: (text: string) => void;
  onFinal: (text: string) => void;
  onEnd: () => void;
  onError: () => void;
}

export function startListening(handlers: ListenHandlers): { stop: () => void } | null {
  const Ctor = recognitionCtor();
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.lang = "en-US";
  rec.interimResults = true;
  rec.continuous = true;
  let pending = "";
  let stopped = false;
  let echo = "";
  let echoAt = 0;
  let quietTimer: ReturnType<typeof setTimeout> | null = null;
  const clearQuiet = () => {
    if (quietTimer == null) return;
    clearTimeout(quietTimer);
    quietTimer = null;
  };
  const deliver = (text: string) => {
    const said = text.trim();
    pending = "";
    clearQuiet();
    if (!said) return;
    const now = Date.now();
    if (said === echo && now - echoAt < 800) return;
    echo = said;
    echoAt = now;
    handlers.onFinal(said);
  };
  const armQuiet = () => {
    clearQuiet();
    quietTimer = setTimeout(() => {
      quietTimer = null;
      if (!stopped && pending) deliver(pending);
    }, ENDPOINT_MS);
  };
  rec.onresult = (event: SpeechEvent) => {
    const start = event.resultIndex ?? 0;
    let interim = "";
    for (let i = start; i < event.results.length; i += 1) {
      const said = event.results[i][0]?.transcript ?? "";
      if (event.results[i].isFinal) deliver(said);
      else interim += said;
    }
    pending = interim.trim();
    if (pending) {
      handlers.onInterim(pending);
      armQuiet();
    }
  };
  rec.onerror = (event: SpeechError) => {
    const code = event.error || "";
    // Chrome reports no-speech when an utterance simply ends. That is not a failed turn.
    if (code === "no-speech") {
      if (pending) deliver(pending);
      return;
    }
    if (code === "aborted" || stopped) return;
    if (pending) deliver(pending);
    else handlers.onError();
  };
  rec.onend = () => {
    if (!stopped && pending) deliver(pending);
    handlers.onEnd();
  };
  try {
    rec.start();
  } catch {
    return null;
  }
  return {
    stop: () => {
      stopped = true;
      pending = "";
      clearQuiet();
      try {
        rec.abort();
      } catch {
        /* already stopped */
      }
    },
  };
}
