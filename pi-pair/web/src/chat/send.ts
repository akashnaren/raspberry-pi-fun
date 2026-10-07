import type { DocCard } from "../attach/message";
import { cancelAttach } from "../attach/upload";
import { byId } from "../core/dom";
import { shouldPollHealth, shouldSoftRetry, softRetryDelay } from "../core/presence";
import { sessionHeaders } from "../core/session";
import { ui } from "../core/state";
import type { SearchInfo, SourceLink, StageName } from "../core/types";
import { turns } from "../main";
import { refreshMemoryRing } from "../memory/ring";
import { paintBrand } from "../ui/brand";
import { primaryKind, primaryLabel } from "../ui/primary-action";
import { releaseVoice, voiceNote } from "../voice/mode";
import { echoOfSpeech, noteSpokenDelta, speakText, speechKey, speechPending, stopSpeaking } from "../voice/speech";
import { flushFollowQueue, queueDraft } from "./follow-queue-view";
import { armResumeSend, delay, flushDeferredHealth, quietNetwork, refresh } from "./health";
import { chatBody } from "./history";
import { addLiveBot, keepPartial } from "./live-reply";
import { freshRequestId, modeModel, searchFrom, shownError, visibleReply, withoutThinkTags } from "./text";
import { lastAssistantText, paint, retryButton, userBeforeCurrent } from "./transcript";

export async function sendText(
  text: string,
  isRetry: boolean,
  spoken = false,
  extra?: { hidden?: string; attachment?: DocCard | null },
  attempt = 0,
  fresh = false,
): Promise<void> {
  if (ui.sending) return;
  let freshNext = false;
  const epoch = ui.chatEpoch;
  const mine = () => epoch === ui.chatEpoch;
  if (!isRetry || !ui.requestId) ui.requestId = freshRequestId();
  const hidden = (extra?.hidden || "").trim();
  if (!isRetry && !text.trim() && !hidden) return;
  ui.sending = true;
  ui.stopAsked = false;
  stopSpeaking();
  syncSend();
  if (!isRetry) {
    turns.push({
      role: "user",
      content: text,
      hidden,
      attachment: extra?.attachment || null,
    });
    paint();
  }
  let voiced = false;
  let followUp: "retry" | "resume" | "" = "";
  const model = modeModel();
  const effort = ui.thinking || "medium";
  const live = addLiveBot(ui.modelMode === "pro");
  let textAccum = "";
  let thoughtAccum = "";
  let thoughtStarted = 0;
  let thoughtMs = 0;
  const thoughtSeconds = () => Math.max(1, Math.round((thoughtMs || 0) / 1000));
  const closeThought = () => {
    if (!thoughtAccum.trim()) {
      thoughtAccum = "";
      live.clearThought();
      return;
    }
    if (!thoughtMs) thoughtMs = Date.now() - (thoughtStarted || Date.now());
    live.setThought(thoughtAccum.trim(), false, thoughtSeconds());
  };
  let searchStatus = "";
  let searchSources: SourceLink[] = [];
  const stages: StageName[] = [];
  const showTurnError = (msg: string): void => {
    live.setText(msg);
    live.markErr();
    live.finish(msg, true, "", null, stages);
    live.root.appendChild(retryButton(() => {
      live.root.remove();
      void sendText(text, true);
    }));
  };
  const missOrRetry = (): void => {
    if (shouldSoftRetry(attempt)) {
      live.root.remove();
      followUp = "retry";
      return;
    }
    showTurnError(shownError(""));
  };
  void refresh();
  try {
    const sys = (byId<HTMLTextAreaElement>("sys").value || "").trim();
    const body = chatBody(turns, {
      model,
      effort,
      mode: ui.modelMode,
      sys,
      fresh,
      spoken,
    });

    let response: Response | null = null;
    let lastErr: unknown = null;
    for (let i = 0; i < 2; i += 1) {
      if (!mine() || ui.stopAsked || !shouldPollHealth(document.hidden)) break;
      const attemptCtrl = new AbortController();
      ui.turnCtrl = attemptCtrl;
      const timer = window.setTimeout(() => attemptCtrl.abort(), 180000);
      try {
        response = await fetch("/v1/chat/completions", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "X-Pi-Target": "auto",
            "X-Pi-Mesh": "on",
            "X-Pi-Mode": ui.modelMode,
            "X-Pi-Request-Id": ui.requestId,
            ...sessionHeaders(),
          },
          body: JSON.stringify(body),
          signal: attemptCtrl.signal,
          cache: "no-store",
        });
        lastErr = null;
        break;
      } catch (err) {
        if (ui.stopAsked || !mine()) throw err;
        lastErr = err;
        const beforeResponse = err instanceof TypeError;
        if (!beforeResponse || i === 1 || !shouldPollHealth(document.hidden)) break;
        await delay(softRetryDelay(i));
      } finally {
        window.clearTimeout(timer);
      }
    }
    const searchNow = (): SearchInfo | null => (
      searchStatus ? { status: searchStatus, sources: searchSources } : null
    );
    if (!mine()) return;
    if (ui.stopAsked) {
      keepPartial(live, textAccum, text, effort, searchNow(), stages, ui.modelMode, "");
      return;
    }
    if (lastErr || !response) throw lastErr || new Error("no response");

    let streamedEffort = response.headers.get("X-Pi-Think") || effort;
    let streamedMode = response.headers.get("X-Pi-Mode") || ui.modelMode;
    let streamedRoute = response.headers.get("X-Pi-Route") || "";
    if (streamedRoute === "pro") live.armPro();
    searchStatus = response.headers.get("X-Pi-Search") || "";
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    if (!contentType.includes("event-stream")) {
      const textBody = await response.text();
      let payload: {
        error?: string;
        pi_think?: string;
        pi_mode?: string;
        pi_route?: string;
        pi_search?: string;
        pi_sources?: SourceLink[];
        pi_stages?: StageName[];
        choices?: { message?: { content?: string } }[];
      } = {};
      let unreadable = false;
      try {
        payload = textBody ? JSON.parse(textBody) : {};
      } catch {
        unreadable = true;
        payload = {};
      }
      if (!response.ok) {
        showTurnError(shownError(payload.error || "HTTP " + response.status));
        return;
      }
      const answer = payload.choices?.[0]?.message?.content || "";
      const search = searchFrom(payload, searchNow());
      const doneStages = Array.isArray(payload.pi_stages) ? payload.pi_stages : stages;
      if (!mine()) return;
      if (unreadable || !visibleReply(answer)) {
        missOrRetry();
      } else {
        const nonStreamThought = String(
          (payload.choices?.[0]?.message as { reasoning_content?: string } | undefined)?.reasoning_content || "",
        ).trim();
        turns.push({
          role: "assistant",
          content: withoutThinkTags(answer),
          effort: payload.pi_think || streamedEffort,
          search,
          stages: doneStages,
          mode: payload.pi_mode || streamedMode,
          route: payload.pi_route || streamedRoute,
          thought: nonStreamThought,
          thoughtSeconds: nonStreamThought ? 1 : 0,
        });
        paint();
        if (spoken && speakText(answer)) voiced = true;
      }
    } else {

    const reader = response.body?.getReader();
    if (!reader) throw new Error("no stream");
    const decoder = new TextDecoder();
    let buf = "";
    let streamDone = false;
    let streamErr = "";
    while (!streamDone) {
      if (!mine()) return;
      if (ui.stopAsked) break;
      const chunk = await reader.read();
      if (!mine()) return;
      if (chunk.done) break;
      buf += decoder.decode(chunk.value, { stream: true });
      let nl = buf.indexOf("\n");
      while (nl >= 0) {
        let line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (line.endsWith("\r")) line = line.slice(0, -1);
        const trimmed = line.trim();
        nl = buf.indexOf("\n");
        if (!trimmed || trimmed.startsWith(":")) continue;
        if (!trimmed.startsWith("data:")) continue;
        const payloadText = trimmed.slice(5).trim();
        if (payloadText === "[DONE]") {
          streamDone = true;
          break;
        }
        let payload: {
          error?: string;
          pi_status?: StageName;
          pi_think?: string;
          pi_mode?: string;
          pi_route?: string;
          pi_search?: string;
          pi_sources?: SourceLink[];
          pi_stages?: StageName[];
          pi_replace?: boolean;
          pi_reasoning_clear?: boolean;
          pi_queue?: { position?: number; eta_s?: number };
          choices?: { delta?: { content?: string; reasoning_content?: string } }[];
        };
        try {
          payload = JSON.parse(payloadText);
        } catch {
          continue;
        }
        if (payload.pi_search) {
          searchStatus = payload.pi_search;
          if (Array.isArray(payload.pi_sources)) searchSources = payload.pi_sources;
        }
        if (payload.pi_status) {
          live.pushStatus(payload.pi_status, searchNow(), payload.pi_queue || null);
          if (!stages.includes(payload.pi_status)) stages.push(payload.pi_status);
        }
        if (Array.isArray(payload.pi_stages)) {
          payload.pi_stages.forEach((name) => {
            if (!stages.includes(name)) stages.push(name);
          });
        }
        if (payload.error) {
          streamErr = String(payload.error);
          streamDone = true;
          break;
        }
        if (payload.pi_reasoning_clear) {
          thoughtAccum = "";
          thoughtStarted = 0;
          thoughtMs = 0;
          live.clearThought();
        }
        const reasoned = payload.choices?.[0]?.delta?.reasoning_content;
        if (reasoned) {
          if (!thoughtStarted) thoughtStarted = Date.now();
          thoughtAccum += reasoned;
          live.setThought(thoughtAccum, true, thoughtSeconds());
        }
        const delta = payload.choices?.[0]?.delta?.content;
        if (delta) {
          if (thoughtAccum && !thoughtMs) closeThought();
          textAccum = payload.pi_replace ? delta : textAccum + delta;
          const visible = withoutThinkTags(textAccum);
          live.setText(visible);
          if (spoken && !voiced && noteSpokenDelta(textAccum, lastAssistantText())) voiced = true;
        }
        if (payload.pi_think) streamedEffort = payload.pi_think;
        if (payload.pi_mode) streamedMode = payload.pi_mode;
        if (payload.pi_route) {
          streamedRoute = payload.pi_route;
          if (streamedRoute === "pro") live.armPro();
        }
      }
    }
    if (!mine()) return;
    if (ui.stopAsked) {
      closeThought();
      keepPartial(
        live,
        textAccum,
        text,
        streamedEffort || effort,
        searchNow(),
        stages,
        streamedMode,
        streamedRoute,
        thoughtAccum,
        thoughtSeconds(),
      );
      return;
    }
    if (streamErr || (!response.ok && !visibleReply(textAccum))) {
      showTurnError(shownError(streamErr || "HTTP " + response.status));
      return;
    }
    if (!mine()) return;
    if (!visibleReply(textAccum)) {
      missOrRetry();
    } else {
      closeThought();
      const search = searchNow();
      const reply = withoutThinkTags(textAccum);
      const previousReply = lastAssistantText();
      const repeated = Boolean(speechKey(reply))
        && speechKey(reply) === speechKey(previousReply)
        && speechKey(text) !== speechKey(userBeforeCurrent());
      const acceptRepeat = !repeated || fresh || !shouldSoftRetry(attempt);
      if (repeated && !fresh) {
        const last = turns[turns.length - 1];
        if (last && last.role === "user" && echoOfSpeech(text, previousReply)) last.echo = true;
      }
      if (!acceptRepeat) {
        stopSpeaking();
        live.root.remove();
        followUp = "retry";
        freshNext = true;
      } else {
        turns.push({
          role: "assistant",
          content: reply,
          effort: streamedEffort,
          search,
          stages,
          mode: streamedMode,
          route: streamedRoute,
          thought: thoughtAccum.trim(),
          thoughtSeconds: thoughtAccum.trim() ? thoughtSeconds() : 0,
        });
        paint();
        if (spoken && speakText(textAccum)) voiced = true;
      }
    }
    }
  } catch (err) {
    if (!mine()) return;
    if (ui.stopAsked) {
      keepPartial(live, textAccum, text, effort, searchStatus ? { status: searchStatus, sources: searchSources } : null, stages, ui.modelMode, "");
      return;
    }
    if (await quietNetwork()) {
      if (!mine()) return;
      if (visibleReply(textAccum)) {
        keepPartial(live, textAccum, text, effort, searchStatus ? { status: searchStatus, sources: searchSources } : null, stages, ui.modelMode, "");
      } else {
        live.root.remove();
        followUp = "resume";
      }
    } else if (!visibleReply(textAccum) && shouldSoftRetry(attempt)) {
      live.root.remove();
      followUp = "retry";
    } else if (visibleReply(textAccum)) {
      keepPartial(live, textAccum, text, effort, searchStatus ? { status: searchStatus, sources: searchSources } : null, stages, ui.modelMode, "");
    } else {
      showTurnError(shownError(err));
    }
  } finally {
    if (mine()) {
      ui.sending = false;
      ui.stopAsked = false;
      ui.turnCtrl = null;
      syncSend();
      if (followUp !== "retry") byId<HTMLTextAreaElement>("q").focus();
      if (followUp !== "retry" && ui.voiceOn && !speechPending()) releaseVoice();
    }
    if (followUp !== "retry") flushDeferredHealth();
    if (mine() && followUp !== "retry" && followUp !== "resume") flushFollowQueue();
    if (mine() && followUp === "") void refreshMemoryRing();
  }
  if (!mine()) return;
  if (followUp === "retry") {
    await delay(softRetryDelay(attempt));
    if (!mine()) return;
    if (!shouldPollHealth(document.hidden)) {
      armResumeSend(text, spoken);
      return;
    }
    await sendText(text, true, spoken, extra, attempt + 1, freshNext);
    return;
  }
  if (followUp === "resume") armResumeSend(text, spoken);
}

export function stopCurrent(): void {
  if (!ui.sending) return;
  ui.stopAsked = true;
  stopSpeaking();
  ui.turnCtrl?.abort();
}

export async function send(): Promise<void> {
  voiceNote("");
  const box = byId<HTMLTextAreaElement>("q");
  const text = box.value.trim();
  const hidden = box.dataset.attachText || "";
  const attachment = ui.pendingDoc;
  if (!text && !hidden.trim()) return;
  if (ui.sending) {
    queueDraft(text, hidden, attachment);
    return;
  }
  if (hidden || attachment) cancelAttach();
  box.value = "";
  autoGrow(box);
  paintBrand();
  await sendText(text, false, false, { hidden, attachment });
}

export function composerHasDraft(): boolean {
  const box = byId<HTMLTextAreaElement>("q");
  return Boolean((box.value || "").trim() || box.dataset.attachText);
}

export function syncSend(): void {
  const go = byId<HTMLButtonElement>("go");
  const stop = byId<HTMLButtonElement>("stop");
  const kind = primaryKind(ui.sending, composerHasDraft());
  go.classList.remove("voice", "send", "stop");
  go.classList.add(kind);
  go.disabled = ui.uploading || ui.attachError;
  go.setAttribute("aria-label", ui.uploading ? "Uploading" : primaryLabel(kind));
  stop.hidden = !(ui.sending && kind === "send");
  stop.disabled = ui.uploading || ui.attachError;
}

export function autoGrow(box: HTMLTextAreaElement): void {
  box.style.height = "auto";
  box.style.height = Math.min(180, box.scrollHeight) + "px";
}
