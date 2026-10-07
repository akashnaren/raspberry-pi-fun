import type { DocCard } from "../attach/message";
import type { HealthSnapshot } from "./presence";
import type { ModelMode } from "./settings";
import type { ImageCard } from "../render/images";

export type Role = "user" | "assistant";

export type StageName = "loading" | "waiting" | "thinking" | "searching" | "answering";

export interface SourceLink {
  title: string;
  url: string;
}

export type ModelChoice = ModelMode;

export interface SearchInfo {
  status: string;
  sources: SourceLink[];
}

export interface Turn {
  role: Role;
  content: string;
  hidden?: string;
  attachment?: DocCard | null;
  effort?: string;
  search?: SearchInfo | null;
  stages?: StageName[];
  mode?: string;
  route?: string;
  thought?: string;
  thoughtSeconds?: number;
  images?: ImageCard[];
  stopped?: boolean;
  echo?: boolean;
}

export interface HealthBody extends HealthSnapshot {
  peers?: { models?: string[] }[];
  modes?: { flash?: string; pro?: string };
}

export interface LiveTurn {
  root: HTMLElement;
  body: HTMLElement;
  stagesEl: HTMLElement;
  seen: StageName[];
  search: SearchInfo | null;
  pushStatus: (
    name: StageName,
    search: SearchInfo | null,
    queue?: { position?: number; eta_s?: number } | null,
  ) => void;
  setText: (text: string) => void;
  setThought: (text: string, live: boolean, seconds: number) => void;
  clearThought: () => void;
  finish: (text: string, failed: boolean, prompt: string, search: SearchInfo | null, stages: StageName[]) => void;
  markErr: () => void;
  armPro: () => void;
}

export interface AttachmentResult {
  text?: string;
  route?: string;
  truncated?: boolean;
  error?: string;
}
