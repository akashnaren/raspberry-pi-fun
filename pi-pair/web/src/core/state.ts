import { browserStorage, loadSettings } from "./settings";
import { storedId } from "./session";
import type { ModelMode, PageSettings, ThinkLevel } from "./settings";
import type { DocCard } from "../attach/message";
import type { FollowItem } from "../chat/follow-queue";
import type { UtteranceHold } from "../voice/speech";
import type { VoiceOrb } from "../voice/orb";
import type { Turn } from "./types";

interface Ui {
  healthAfterSend: boolean;
  sending: boolean;
  stopAsked: boolean;
  chatEpoch: number;
  requestId: string;
  turnCtrl: AbortController | null;
  pageSettings: PageSettings;
  thinking: ThinkLevel;
  modelMode: ModelMode;
  enterToSend: boolean;
  picturesOn: boolean;
  listening: boolean;
  dictating: boolean;
  dictated: string;
  voiceOn: boolean;
  voiceHold: boolean;
  listenHandle: { stop: () => void } | null;
  cancelUtterance: (() => void) | null;
  pendingDoc: DocCard | null;
  resumedAt: number;
  serviceSig: string;
  resumeSend: (() => void) | null;
  graceTimer: number;
  bargeHandle: { stop: () => void } | null;
  pendingBarge: string;
  speakingLine: string;
  voiceEchoUntil: number;
  heardConfidence: number;
  voiceLeaveTimer: number;
  voiceOrb: VoiceOrb | null;
  micGen: number;
  micStream: MediaStream | null;
  micAudio: AudioContext | null;
  micAnalyser: AnalyserNode | null;
  micBins: Uint8Array<ArrayBuffer> | null;
  syntheticLevel: number;
  attachSerial: number;
  attachXhr: XMLHttpRequest | null;
  uploading: boolean;
  attachError: boolean;
  followQueue: FollowItem[];
  followNextId: number;
  voiceUtterance: UtteranceHold | null;
  chatId: string;
  pinnedInfo: HTMLElement | null;
  compactAt: number;
  compactBusyAt: number;
  ringCompactions: number;
  memShowTimer: number;
  memHideTimer: number;
  memClearTimer: number;
  ringTitleTimer: number;
}

export const TURNS_KEY = "openpi.transcript";
export const CHAT_KEY = "pi-chat";
export const CLIENT_KEY = "pi-client";
export const ATTACH_BYTES = 4 * 1024 * 1024;
export const imageJobs = new Set<AbortController>();
export const imageJob = new WeakMap<Turn, AbortController>();
export const imagePending = new WeakSet<Turn>();

export const ui: Ui = {
  healthAfterSend: false,
  sending: false,
  stopAsked: false,
  chatEpoch: 0,
  requestId: "",
  turnCtrl: null,
  pageSettings: loadSettings(browserStorage("local")),
  thinking: "medium",
  modelMode: "auto",
  enterToSend: false,
  picturesOn: false,
  listening: false,
  dictating: false,
  dictated: "",
  voiceOn: false,
  voiceHold: false,
  listenHandle: null,
  cancelUtterance: null,
  pendingDoc: null,
  resumedAt: 0,
  serviceSig: "",
  resumeSend: null,
  graceTimer: 0,
  bargeHandle: null,
  pendingBarge: "",
  speakingLine: "",
  voiceEchoUntil: 0,
  heardConfidence: 1,
  voiceLeaveTimer: 0,
  voiceOrb: null,
  micGen: 0,
  micStream: null,
  micAudio: null,
  micAnalyser: null,
  micBins: null,
  syntheticLevel: 0,
  attachSerial: 0,
  attachXhr: null,
  uploading: false,
  attachError: false,
  followQueue: [],
  followNextId: 1,
  voiceUtterance: null,
  chatId: storedId("session", CHAT_KEY),
  pinnedInfo: null,
  compactAt: 0.7,
  compactBusyAt: 0.5,
  ringCompactions: 0,
  memShowTimer: 0,
  memHideTimer: 0,
  memClearTimer: 0,
  ringTitleTimer: 0,
};
ui.thinking = ui.pageSettings.thinking;
ui.modelMode = ui.pageSettings.mode;
ui.enterToSend = ui.pageSettings.enterToSend;
ui.picturesOn = ui.pageSettings.pictures !== false;
