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

let beforeSpeech: ((text: string) => void) | null = null;
let afterSpeech: (() => void) | null = null;

export function whenSpeechStarts(fn: (text: string) => void): void {
  beforeSpeech = fn;
}

export function whenSpeechEnds(fn: () => void): void {
  afterSpeech = fn;
}

export function speakText(text: string): boolean {
  if (!("speechSynthesis" in window)) return false;
  const say = spokenAnswer(text);
  if (!say) return false;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(say);
  utter.rate = 1;
  const started = beforeSpeech;
  const done = afterSpeech;
  utter.onend = () => done?.();
  utter.onerror = () => done?.();
  window.speechSynthesis.speak(utter);
  started?.(say);
  return true;
}

export function stopSpeaking(): void {
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
  const deliver = (text: string) => {
    const said = text.trim();
    pending = "";
    if (!said) return;
    handlers.onFinal(said);
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
    if (pending) handlers.onInterim(pending);
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
      try {
        rec.abort();
      } catch {
        /* already stopped */
      }
    },
  };
}
