/** One chat body for typed and spoken turns. Spoken never changes the payload. */

import { modelUserContent } from "./attach";
import { speechKey } from "./voice";

export interface HistoryTurn {
  role: string;
  content: string;
  hidden?: string;
  stopped?: boolean;
  echo?: boolean;
}

export interface ChatOptions {
  model: string;
  effort: string;
  mode: string;
  sys?: string;
  fresh?: boolean;
  spoken?: boolean;
}

export interface ChatBody {
  model: string;
  messages: { role: string; content: string }[];
  stream: boolean;
  think: string;
  pi_mode: string;
  pi_target: string;
  pi_mesh: string;
}

function visibleAssistant(text: string): string {
  return String(text || "")
    .replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, "")
    .replace(/<think(?:ing)?>[\s\S]*$/gi, "")
    .replace(/<\/think(?:ing)?>/gi, "");
}

function freshTurns(turns: HistoryTurn[]): HistoryTurn[] {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    if (turns[index].role === "user") return [turns[index]];
  }
  return [];
}

/** Drop stopped partials, echo pairs, and a repeated assistant reply. */
export function hygienicTurns(turns: HistoryTurn[]): HistoryTurn[] {
  const kept: HistoryTurn[] = [];
  const seen = new Set<string>();
  let skipAnswer = false;
  for (const turn of turns) {
    if (turn.role === "user") {
      if (turn.echo) {
        skipAnswer = true;
        continue;
      }
      skipAnswer = false;
      kept.push(turn);
      continue;
    }
    if (turn.role !== "assistant") continue;
    if (turn.stopped) continue;
    if (skipAnswer) {
      skipAnswer = false;
      continue;
    }
    const key = speechKey(visibleAssistant(turn.content));
    if (key && seen.has(key)) continue;
    if (key) seen.add(key);
    kept.push(turn);
  }
  return kept;
}

export function chatBody(turns: HistoryTurn[], options: ChatOptions): ChatBody {
  const messages: { role: string; content: string }[] = [];
  const sys = (options.sys || "").trim();
  if (sys) messages.push({ role: "system", content: sys });
  const chosen = options.fresh ? freshTurns(turns) : hygienicTurns(turns);
  for (const turn of chosen) {
    const content = turn.role === "user"
      ? modelUserContent(turn.content, turn.hidden || "")
      : visibleAssistant(turn.content);
    if (!content.trim()) continue;
    messages.push({ role: turn.role, content });
  }
  return {
    model: options.model,
    messages,
    stream: true,
    think: options.effort,
    pi_mode: options.mode,
    pi_target: "auto",
    pi_mesh: "on",
  };
}
