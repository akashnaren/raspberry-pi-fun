interface SpeechResult {
  isFinal: boolean;
  0?: { transcript?: string };
}

interface SpeechEvent extends Event {
  results: ArrayLike<SpeechResult>;
}

interface SpeechRec {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((event: SpeechEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
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

export function speakText(text: string): void {
  if (!("speechSynthesis" in window)) return;
  const say = plainSpeech(text);
  if (!say) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(say);
  utter.rate = 1;
  window.speechSynthesis.speak(utter);
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
  rec.continuous = false;
  let finished = false;
  let settled = false;
  rec.onresult = (event: SpeechEvent) => {
    let text = "";
    let isFinal = false;
    for (let i = 0; i < event.results.length; i += 1) {
      text += event.results[i][0]?.transcript ?? "";
      if (event.results[i].isFinal) isFinal = true;
    }
    const spoken = text.trim();
    if (isFinal) {
      if (!finished) {
        finished = true;
        handlers.onFinal(spoken);
      }
      return;
    }
    handlers.onInterim(spoken);
  };
  rec.onerror = () => {
    if (finished || settled) return;
    settled = true;
    handlers.onError();
  };
  rec.onend = () => {
    settled = true;
    handlers.onEnd();
  };
  try {
    rec.start();
  } catch {
    return null;
  }
  return {
    stop: () => {
      try {
        rec.abort();
      } catch {
        /* already stopped */
      }
    },
  };
}
