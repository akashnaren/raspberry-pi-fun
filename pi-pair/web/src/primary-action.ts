/** Rightmost composer button: voice waveform, send, or stop. */

export type PrimaryKind = "voice" | "send" | "stop";

export function primaryKind(sending: boolean, hasDraft: boolean): PrimaryKind {
  if (sending) return "stop";
  if (hasDraft) return "send";
  return "voice";
}

export function primaryLabel(kind: PrimaryKind): string {
  if (kind === "stop") return "Stop";
  if (kind === "send") return "Send";
  return "Voice mode";
}
