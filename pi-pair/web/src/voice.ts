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

/** The word stop, alone, ends a spoken session. */
export function isSoloStop(transcript: string): boolean {
  return transcript.trim().toLowerCase().replace(/[^a-z]/g, "") === "stop";
}

/** Quiet gap after the last heard word before a turn is sent. Chrome's own silence is much longer. */
export const ENDPOINT_MS = 450;

let beforeSpeech: ((text: string) => void) | null = null;
let afterSpeech: (() => void) | null = null;
let duringSpeech: (() => void) | null = null;
let queuedSay = "";
let liveUtterances = 0;

/** True while a reply is queued or playing, so the mic stays closed. */
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
