import { queueDraft } from "../chat/follow-queue-view";
import { autoGrow, sendText, syncSend } from "../chat/send";
import { lastAssistantText } from "../chat/transcript";
import { byId } from "../core/dom";
import { ui } from "../core/state";
import { VOICE_CLASSES } from "../main";
import { paintMicButton } from "./mic-button";
import { createOrb, type VoiceOrb, type VoiceOrbState } from "./orb";
import { POST_TTS_DEAF_MS, POST_TTS_ECHO_MS, createUtteranceHold, currentSpeech, dropPostSpeechEcho, endOfUtteranceSilence, isSoloStop, shouldBargeIn, speechPending, speechReady, startListening, stopSpeaking, turnFromRecognition } from "./speech";

export function voiceNote(text: string): void {
  const note = byId("voiceNote");
  if (!text) {
    note.hidden = true;
    note.textContent = "";
    return;
  }
  note.hidden = false;
  note.textContent = text;
}

export function paintVoice(): void {
  const mic = byId("btnVoice");
  paintMicButton(mic, ui.dictating);
  mic.setAttribute("aria-pressed", ui.voiceOn || ui.dictating ? "true" : "false");
  mic.setAttribute("aria-label", "Voice");
  if (ui.voiceLeaveTimer && !ui.voiceOn) document.body.classList.add("voice-session");
  else document.body.classList.toggle("voice-session", ui.voiceOn);
  const stageOn = ui.voiceOn || ui.voiceLeaveTimer !== 0;
  byId("voiceStage").setAttribute("aria-hidden", stageOn ? "false" : "true");
  const tap = document.getElementById("voiceSend");
  if (tap) tap.hidden = !ui.voiceOn;
  const end = document.getElementById("voiceEnd");
  if (end) end.hidden = !ui.voiceOn;
}

export function sampleMic(): number {
  if (ui.micAnalyser && ui.micBins) {
    ui.micAnalyser.getByteTimeDomainData(ui.micBins);
    let sum = 0;
    for (let i = 0; i < ui.micBins.length; i += 1) {
      const sample = (ui.micBins[i] - 128) / 128;
      sum += sample * sample;
    }
    return Math.min(1, Math.sqrt(sum / ui.micBins.length) * 3.2);
  }
  return ui.syntheticLevel;
}

export function noteInterimLevel(text: string): void {
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  ui.syntheticLevel = words ? Math.min(1, words / 6) : 0;
}

export function ensureOrb(): VoiceOrb | null {
  if (ui.voiceOrb) return ui.voiceOrb;
  const canvas = document.getElementById("voiceOrb");
  if (!canvas || canvas.tagName !== "CANVAS") return null;
  ui.voiceOrb = createOrb(canvas as HTMLCanvasElement, { sampleLevel: sampleMic });
  return ui.voiceOrb;
}

export function setVoiceState(state: VoiceOrbState, caption?: string): void {
  ensureOrb();
  const stage = byId("voiceStage");
  for (const name of VOICE_CLASSES) stage.classList.toggle(name, name === state);
  stage.setAttribute("aria-label", state);
  const orb = ensureOrb();
  orb?.set(state);
  if (caption !== undefined) voiceCaption(caption);
  else if (state === "idle") voiceCaption("");
}

export async function openMicLevel(): Promise<void> {
  const gen = ++ui.micGen;
  const media = globalThis.navigator?.mediaDevices;
  if (!media || typeof media.getUserMedia !== "function") return;
  try {
    const stream = await media.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (gen !== ui.micGen || !ui.voiceOn) {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    const Ctx = window.AudioContext;
    if (typeof Ctx !== "function") {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    const audio = new Ctx();
    const source = audio.createMediaStreamSource(stream);
    const analyser = audio.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    ui.micStream = stream;
    ui.micAudio = audio;
    ui.micAnalyser = analyser;
    ui.micBins = new Uint8Array(analyser.fftSize);
  } catch {
    ui.syntheticLevel = 0;
  }
}

export function closeMicLevel(): void {
  ui.micGen += 1;
  if (ui.micStream) {
    for (const track of ui.micStream.getTracks()) track.stop();
  }
  ui.micStream = null;
  ui.micAnalyser = null;
  ui.micBins = null;
  const audio = ui.micAudio;
  ui.micAudio = null;
  if (audio && audio.state !== "closed") void audio.close();
  ui.syntheticLevel = 0;
}

export function voiceCaption(text: string): void {
  const line = byId("voiceLive");
  line.textContent = text;
  line.classList.toggle("idle", !text || text === "Listening");
}

export function endListening(): void {
  ui.listening = false;
  ui.listenHandle = null;
  paintVoice();
}

export function stopCapture(): void {
  ui.listenHandle?.stop();
  ui.listenHandle = null;
  ui.listening = false;
}

export function toggleVoice(): void {
  if (ui.voiceOn || ui.sending) return;
  if (ui.dictating) {
    ui.dictating = false;
    stopCapture();
    voiceNote("");
    paintVoice();
    return;
  }
  ui.dictated = byId<HTMLTextAreaElement>("q").value.trim();
  ui.dictating = true;
  voiceNote("");
  beginDictation();
}

export function beginDictation(): void {
  if (!ui.dictating || ui.listening || ui.voiceOn) return;
  const handle = startListening({
    onInterim(text) {
      const box = byId<HTMLTextAreaElement>("q");
      const lead = ui.dictated.trim();
      const more = text.trim();
      box.value = lead && more ? lead + " " + more : (more || lead);
      autoGrow(box);
      syncSend();
    },
    onFinal(text) {
      const turn = turnFromRecognition(text);
      if (!turn) return;
      ui.dictated = ui.dictated ? ui.dictated + " " + turn.content : turn.content;
      const box = byId<HTMLTextAreaElement>("q");
      box.value = ui.dictated;
      autoGrow(box);
      syncSend();
    },
    onError() {
      ui.dictating = false;
      endListening();
      voiceNote("Voice needs the microphone in this browser.");
    },
    onEnd() {
      const again = ui.dictating && !ui.voiceOn;
      endListening();
      if (again) beginDictation();
    },
  });
  if (!handle) {
    ui.dictating = false;
    voiceNote("Voice needs Chrome's built-in speech recognition.");
    paintVoice();
    return;
  }
  ui.listening = true;
  ui.listenHandle = handle;
  paintVoice();
}

export function stopBarge(): void {
  ui.bargeHandle?.stop();
  ui.bargeHandle = null;
}

export function takeBarge(text: string): void {
  const said = text.trim();
  if (!said || ui.pendingBarge) return;
  ui.pendingBarge = said;
  stopBarge();
  stopSpeaking();
  noteInterimLevel(said);
  setVoiceState("heard", said);
  releaseVoice();
}

export function armBarge(): void {
  if (ui.bargeHandle || !ui.voiceOn || ui.listening) return;
  let heardAt = 0;
  ui.bargeHandle = startListening({
    onInterim(text) {
      if (!heardAt) heardAt = Date.now();
      const held = Date.now() - heardAt;
      if (shouldBargeIn(text, currentSpeech(), speechPending(), held)) takeBarge(text);
    },
    onFinal(text) {
      const held = heardAt ? Date.now() - heardAt : 0;
      if (shouldBargeIn(text, currentSpeech(), speechPending(), held)) takeBarge(text);
    },
    onError() {
      stopBarge();
    },
    onEnd() {
      const again = ui.voiceOn && !ui.pendingBarge && speechPending();
      ui.bargeHandle = null;
      if (again) armBarge();
    },
  });
}

export function endVoiceMode(): void {
  const wasOn = ui.voiceOn;
  ui.voiceOn = false;
  ui.voiceHold = false;
  ui.pendingBarge = "";
  ui.voiceUtterance = null;
  stopBarge();
  ui.cancelUtterance?.();
  ui.cancelUtterance = null;
  stopCapture();
  stopSpeaking();
  closeMicLevel();
  setVoiceState("idle");
  voiceNote("");
  window.clearTimeout(ui.voiceLeaveTimer);
  ui.voiceLeaveTimer = 0;
  if (wasOn) {
    document.body.classList.add("voice-leave");
    ui.voiceLeaveTimer = window.setTimeout(() => {
      ui.voiceLeaveTimer = 0;
      if (!ui.voiceOn) document.body.classList.remove("voice-session", "voice-leave");
    }, 240);
  }
  paintVoice();
  const box = document.getElementById("q");
  if (box instanceof HTMLElement) box.focus();
}

export function toggleVoiceMode(): void {
  if (ui.voiceOn) {
    endVoiceMode();
    return;
  }
  if (ui.sending) return;
  if (ui.dictating) {
    ui.dictating = false;
    stopCapture();
  }
  window.clearTimeout(ui.voiceLeaveTimer);
  ui.voiceLeaveTimer = 0;
  document.body.classList.remove("voice-leave");
  if (!speechReady()) {
    ui.voiceOn = true;
    paintVoice();
    setVoiceState("error", "Voice needs Chrome's built-in speech recognition.");
    voiceNote("Voice needs Chrome's built-in speech recognition.");
    return;
  }
  ui.voiceOn = true;
  stopSpeaking();
  voiceNote("");
  void openMicLevel();
  paintVoice();
  beginVoice();
}

export function beginVoice(existing?: ReturnType<typeof createUtteranceHold>): void {
  if (!ui.voiceOn || ui.listening || ui.voiceHold || ui.sending) return;
  ui.heardConfidence = 1;
  const hold = existing ?? createUtteranceHold((text) => {
    if (isSoloStop(text)) {
      endVoiceMode();
      return;
    }
    const turn = turnFromRecognition(text, {
      confidence: ui.heardConfidence,
      lastAssistant: lastAssistantText(),
    });
    ui.voiceHold = Boolean(turn);
    const active = ui.listenHandle;
    ui.listenHandle = null;
    ui.listening = false;
    active?.stop();
    paintVoice();
    if (!turn) {
      ui.voiceHold = false;
      voiceNote("Didn't catch that");
      if (ui.voiceOn) beginVoice();
      setVoiceState("listening", "Didn't catch that");
      window.setTimeout(() => {
        const note = document.getElementById("voiceNote");
        if (note && note.textContent === "Didn't catch that") voiceNote("");
        const live = document.getElementById("voiceLive");
        if (ui.voiceOn && live && live.textContent === "Didn't catch that") {
          setVoiceState("listening", "Listening");
        }
      }, 1200);
      return;
    }
    if (ui.sending) {
      queueDraft(turn.content, "", null);
      ui.voiceHold = false;
      return;
    }
    voiceCaption("Thinking");
    setVoiceState("thinking", "Thinking");
    void sendText(turn.content, false, true);
  }, endOfUtteranceSilence());
  ui.voiceUtterance = hold;
  ui.cancelUtterance = () => hold.cancel();
  const handle = startListening({
    onInterim(text) {
      const line = hold.interim(text);
      noteInterimLevel(line);
      if (line) setVoiceState("heard", line);
      else setVoiceState("listening", "Listening");
    },
    onFinal(text, confidence) {
      if (typeof confidence === "number") ui.heardConfidence = confidence;
      const echoAge = ui.voiceEchoUntil
        ? POST_TTS_ECHO_MS - (ui.voiceEchoUntil - Date.now())
        : POST_TTS_ECHO_MS;
      if (dropPostSpeechEcho(text, lastAssistantText(), echoAge)) return;
      const line = hold.final(text);
      if (isSoloStop(line)) {
        hold.cancel();
        endVoiceMode();
        return;
      }
      noteInterimLevel(line);
      if (line) setVoiceState("heard", line);
      else setVoiceState("listening", "Listening");
    },
    onError() {
      hold.cancel();
      ui.voiceHold = false;
      endListening();
      setVoiceState("error", "Voice needs the microphone in this browser.");
      voiceNote("Voice needs the microphone in this browser.");
    },
    onEnd() {
      const pendingLine = hold.text();
      const again = ui.voiceOn && !ui.voiceHold && !ui.sending;
      endListening();
      if (!again) return;
      if (pendingLine) beginVoice(hold);
      else beginVoice();
    },
  }, { restart: true });
  if (!handle) {
    hold.cancel();
    ui.cancelUtterance = null;
    setVoiceState("error", "Voice needs Chrome's built-in speech recognition.");
    voiceNote("Voice needs Chrome's built-in speech recognition.");
    paintVoice();
    return;
  }
  ui.listening = true;
  ui.listenHandle = handle;
  const continued = hold.text();
  noteInterimLevel(continued);
  if (continued) setVoiceState("heard", continued);
  else setVoiceState("listening", "Listening");
  paintVoice();
}

export function releaseVoice(): void {
  stopBarge();
  if (speechPending()) return;
  const said = ui.pendingBarge.trim();
  if (ui.sending) {
    if (said) {
      ui.stopAsked = true;
      ui.turnCtrl?.abort();
    }
    return;
  }
  ui.pendingBarge = "";
  ui.voiceHold = false;
  if (said && isSoloStop(said)) {
    endVoiceMode();
    return;
  }
  if (said) {
    setVoiceState("thinking", "Thinking");
    void sendText(said, false, true);
    return;
  }
  if (!ui.voiceOn || ui.listening) return;
  setVoiceState("listening", "Listening");
  window.setTimeout(() => {
    if (!ui.voiceOn || ui.listening || ui.sending || ui.voiceHold) return;
    ui.voiceEchoUntil = Date.now() + POST_TTS_ECHO_MS;
    beginVoice();
  }, POST_TTS_DEAF_MS);
}
