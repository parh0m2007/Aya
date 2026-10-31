import "./styles.css";
import {
  initAvatar,
  setMood,
  setThinking,
  setListening,
  startMouth,
  stopMouth,
  setPanelOpen,
  attachLipSync,
  playReaction,
  applyAvatarSettings,
  getAvatarScale,
  setAvatarScale,
  headPoint,
  setHidden,
  poke,
  setWalking,
  setDragHover,
} from "./avatar";
import { initDesktopWalk } from "./desktop-walk";
import { initHoverPass } from "./hover-pass";
import { cancelSpeechPlan, connectCore, speechPieceUrl, transcribe, speakUrl, type PendingAction } from "./core-client";
import { CORE_URL, stateLabel } from "./persona";
import { initVoice, currentVoiceMode, applyVoiceModeValue, hotkeyAction, HoldGate, type VoiceMode } from "./voice-ctl";
import { accelLabel, rebind } from "./hotkey";
import { startUpdateChecks } from "./updater";
import { dropWhenHidden, Endpointer, matchWake, speechLevel, wakeNames } from "./voice-logic";
import { gameVoiceMode, listensAlways, rendersNow } from "./game-mode";
import { clampFollowupSec, FollowUpWindow, shouldArmFollowUp, shouldArmWakeWindow } from "./followup";
import { shouldToast, toastBody } from "./notify-decision";
import { initiativeDelivery, initiativeQuery } from "./initiative-logic";
import { previewArgs } from "./action-preview";
import { notifyAya } from "./notify";
import { setTrayCheck, onTrayToggle, type TrayCheck } from "./tray";
import { initWindowState, isAyaHidden } from "./window-state";
import { clampScale } from "./avatar-logic";
import { PcmChunker, nextStart, parsePcmType, pcmToWav } from "./pcm-stream";
import { AyaMusic } from "./music-player";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

const bubble = $("bubble");
const bubbleText = $("bubble-text");
const bubbleName = $("bubble-name");
const panel = $("panel");
const dock = $("dock");
const openBtn = $("open-panel");
const closeBtn = $("panel-close");
const messages = $("messages");
const form = $<HTMLFormElement>("chat-form");
const input = $<HTMLTextAreaElement>("chat-input");
const chip = $("state-chip");
const whoName = $("who-name");
const whoMark = $("who-mark");
const viewChat = $("view-chat");
const viewQuick = $("view-quick");
const quickToggle = $("quick-toggle");
const menu = $("menu");
const toastEl = $("toast");

let busy = false;
let lastMood = "joyful";
let panelOpen = false;
let followupOn = false;
let followupSec = 6;
let followupTimer: ReturnType<typeof setInterval> | null = null;
let followupVisibilityTimer: ReturnType<typeof setInterval> | null = null;
let followupRecording = false;
let recStartedAt = 0;
let recTurn = 0;
let discardNextRecording = false;
let turnSeq = 0;
const followupWindow = new FollowUpWindow();

let ayaName = "Ая";
let ttsEnabled = true;
let reactionsOn = true;
let historyLoaded = false;
let lastSent = "";

/* ═════════════════════════ панель ═════════════════════════ */

function openPanel(view: "chat" | "quick" = "chat") {
  panel.classList.remove("hidden");
  dock.classList.add("hidden");
  hideBubble(true);
  panelOpen = true;
  setPanelOpen(true);
  showView(view);
  if (!historyLoaded) void loadHistory();
  if (view === "chat") window.setTimeout(() => input.focus(), 30);
}

function closePanel() {
  panel.classList.add("hidden");
  dock.classList.remove("hidden");
  panelOpen = false;
  turnSeq++;
  cancelFollowup(true);
  setPanelOpen(false);
  input.blur();
}

function showView(v: "chat" | "quick") {
  viewChat.classList.toggle("hidden", v !== "chat");
  viewQuick.classList.toggle("hidden", v !== "quick");
  quickToggle.classList.toggle("on", v === "quick");
  if (v === "quick") void syncQuick();
}

openBtn.addEventListener("click", () => openPanel());
closeBtn.addEventListener("click", closePanel);
quickToggle.addEventListener("click", () => showView(viewQuick.classList.contains("hidden") ? "quick" : "chat"));

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!menu.classList.contains("hidden")) hideMenu();
    else if (panelOpen) closePanel();
  }
});

// док держим рядом с аватаром (справа, на уровне пояса)
window.addEventListener("aya-anchor", (ev) => {
  const d = (ev as CustomEvent).detail as { x: number; y: number; h: number; top: number; w: number };
  if (!d.w) return;
  const left = Math.min(window.innerWidth - 56, d.x + d.w * 0.32);
  const top = Math.min(window.innerHeight - 160, Math.max(80, d.top + (d.y - d.top) * 0.42));
  dock.style.left = `${Math.round(left)}px`;
  dock.style.top = `${Math.round(top)}px`;
  dock.style.right = "auto";
  dock.style.bottom = "auto";
});

/* ═════════════════════════ сообщения ═════════════════════════ */

function addMsg(text: string, cls: string): HTMLElement {
  const el = document.createElement("div");
  el.className = "msg " + cls;
  el.textContent = text;
  messages.appendChild(el);
  messages.scrollTop = messages.scrollHeight;
  return el;
}

async function loadHistory() {
  historyLoaded = true;
  try {
    const rows = (await (await fetch(`${CORE_URL}/api/history?limit=24`)).json()) as { role: string; text: string }[];
    if (!rows.length) {
      addMsg(`Это ${ayaName}. Напиши что-нибудь или зажми микрофон.`, "note");
      return;
    }
    const frag = document.createDocumentFragment();
    const sep = document.createElement("div");
    sep.className = "day-sep";
    sep.textContent = "раньше";
    frag.appendChild(sep);
    for (const r of rows.slice(-16)) {
      if (!r.text || r.text.startsWith("initiative:")) continue;
      const el = document.createElement("div");
      el.className = `msg history ${r.role === "user" ? "user" : "aya"}`;
      el.textContent = r.text;
      frag.appendChild(el);
    }
    messages.prepend(frag);
    messages.scrollTop = messages.scrollHeight;
  } catch {
    historyLoaded = false; // ядро ещё не поднялось — попробуем при следующем открытии
  }
}

/* ═════════════════════════ пузырь реплики ═════════════════════════ */

let bubbleTimer = 0;

function placeBubble() {
  const h = headPoint();
  const w = bubble.offsetWidth || 240;
  const half = w / 2;
  const cx = Math.max(12 + half, Math.min(window.innerWidth - 12 - half, h.x));
  const top = Math.max(10, h.y - 70 - bubble.offsetHeight);
  bubble.style.left = `${cx}px`;
  bubble.style.top = `${top}px`;
  bubble.style.setProperty("--tail", `${Math.max(18, Math.min(w - 18, h.x - (cx - half)))}px`);
}

function showBubble(text: string, opts: { thinking?: boolean; holdMs?: number } = {}) {
  if (panelOpen) return; // в чате и так видно
  bubbleName.textContent = ayaName;
  bubbleText.textContent = text;
  bubble.classList.toggle("thinking", !!opts.thinking);
  bubble.classList.add("show");
  placeBubble();
  window.clearTimeout(bubbleTimer);
  const hold = opts.holdMs ?? Math.max(4000, Math.min(16000, text.length * 70));
  if (hold > 0) bubbleTimer = window.setTimeout(() => hideBubble(), hold);
}

function hideBubble(now = false) {
  window.clearTimeout(bubbleTimer);
  if (now || !speakPlaying) bubble.classList.remove("show");
  else bubbleTimer = window.setTimeout(() => hideBubble(), 1500); // говорит — подождём
}

/* ═════════════════════════ ввод ═════════════════════════ */

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(120, input.scrollHeight)}px`;
}

input.addEventListener("input", () => {
  autosize();
  poke();
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    form.requestSubmit();
  } else if (e.key === "ArrowUp" && !input.value && lastSent) {
    e.preventDefault();
    input.value = lastSent;
    autosize();
  }
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || busy) return;
  cancelFollowup(true);
  busy = true;
  cancelPending();
  lastSent = text;
  input.value = "";
  autosize();
  addMsg(text, "user");
  await sendToAya(text);
});

/* ═════════════════════════ подтверждение действий ═════════════════════════ */

const TOOL_LABELS: Record<string, string> = {
  open_app: "Открыть приложение",
  set_volume: "Изменить громкость",
  create_note: "Создать заметку",
  search_allowed_folder: "Искать в рабочей папке",
  get_task_status: "Проверить задачи",
  write_allowed_file: "Записать файл в рабочей папке",
  run_cmd: "Выполнить команду",
  ssh_run: "Команда на сервере",
  tg_send: "Отправить сообщение в Telegram",
  remember_fact: "Запомнить",
};

let pendingId: string | null = null;

function cancelPending() {
  if (!pendingId) return;
  const pid = pendingId;
  pendingId = null;
  fetch(`${CORE_URL}/api/action/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: pid, approve: false }),
  }).catch(() => {});
}

function actionCard(p: PendingAction) {
  pendingId = p.id;
  if (!panelOpen) openPanel();
  const el = document.createElement("div");
  el.className = "msg aya action-card";
  const title = document.createElement("div");
  title.className = "ac-title";
  title.textContent = (TOOL_LABELS[p.tool] || p.tool) + " — разрешить?";
  const preview = document.createElement("pre");
  preview.className = "ac-preview";
  preview.textContent = previewArgs(p.args);
  const btns = document.createElement("div");
  btns.className = "ac-btns";
  const ok = document.createElement("button");
  ok.className = "ac-ok";
  ok.textContent = "Выполнить";
  const no = document.createElement("button");
  no.className = "ac-no";
  no.textContent = "Отменить";
  btns.append(ok, no);
  el.append(title, preview, btns);
  messages.appendChild(el);
  messages.scrollTop = messages.scrollHeight;
  const done = (text: string) => {
    el.classList.add("done");
    el.classList.remove("action-card");
    title.textContent = text;
    preview.remove();
    btns.remove();
  };
  const decide = async (approve: boolean) => {
    ok.disabled = no.disabled = true;
    try {
      const res = await (
        await fetch(`${CORE_URL}/api/action/confirm`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: p.id, approve }),
        })
      ).json();
      pendingId = null;
      done((res.ok ? (res.applied ? "✓ Выполнено" : "Отменено") : "Истекло") + ` — ${String(res.result || "").slice(0, 160)}`);
      if (res.ok && res.applied && res.result) void speakOnce(String(res.result), lastMood);
    } catch {
      done("Не получилось связаться с ядром");
    }
  };
  ok.addEventListener("click", () => decide(true));
  no.addEventListener("click", () => decide(false));
}

/* ═════════════════════════ ход разговора ═════════════════════════ */

let currentPlan = "";
let lastReplyText = "";
let lastSpeechEndedAt = 0;

async function fetchAudio(url: string): Promise<ArrayBuffer> {
  const ctl = new AbortController();
  const t = window.setTimeout(() => ctl.abort(), 180000);
  try {
    const r = await fetch(url, { signal: ctl.signal });
    if (!r.ok) throw new Error(`speak ${r.status}`);
    return await r.arrayBuffer();
  } finally {
    window.clearTimeout(t);
  }
}

/** Облачный кусок: запрос уходит сразу, тело читаем потоком при проигрывании.
 *  Браузер сам копит пришедшие байты, пока до куска не дошла очередь. */
function streamingFetch(url: string): { res: Promise<Response>; ctl: AbortController } {
  const ctl = new AbortController();
  return { res: fetch(url, { signal: ctl.signal }), ctl };
}

type TurnItem =
  | { kind: "buf"; data: Promise<ArrayBuffer> }
  | { kind: "stream"; res: Promise<Response>; ctl: AbortController };

/** Проигрывает куски озвучки хода по порядку. Синтез идёт в ядре заранее,
 *  поэтому каждый кусок запрашиваем сразу, как только ядро о нём сообщило. */
class TurnPlayer {
  private queue: TurnItem[] = [];
  private running = false;
  played = false;
  added = 0;

  add(url: string, stream = false) {
    this.added++;
    // ядро ещё синтезирует — запрос просто подождёт готовности
    this.queue.push(stream ? { kind: "stream", ...streamingFetch(url) } : { kind: "buf", data: fetchAudio(url) });
    if (!this.running) void this.run();
  }

  private drop() {
    for (const it of this.queue) if (it.kind === "stream") it.ctl.abort();
    this.queue.length = 0;
  }

  get idle(): boolean {
    return !this.running && this.queue.length === 0;
  }

  private async run() {
    this.running = true;
    try {
      while (this.queue.length) {
        if (speakAbort) {
          this.drop();
          break;
        }
        const next = this.queue.shift()!;
        try {
          if (next.kind === "stream") {
            const ok = await playTtsStream(next.res, next.ctl);
            if (ok && !speakAbort) this.played = true;
            continue;
          }
          const data = await next.data;
          if (speakAbort) break;
          startMouth();
          await playTtsBuffer(data);
          if (!speakAbort) this.played = true;
        } catch (e) {
          diag("piece.fail", { err: String(e instanceof Error ? e.message : e) });
        }
      }
      if (speakAbort) this.drop();
    } finally {
      stopMouth();
      this.running = false;
      lastSpeechEndedAt = Date.now();
    }
  }
}

async function sendToAya(text: string, voice = false, attachments: string[] = []) {
  const myTurn = ++turnSeq;
  busy = true;
  speakAbort = false;
  ensureGainCtx();
  const typing = addMsg("", "aya typing");
  setThinking(true);
  showBubble(attachments.length ? "Смотрю…" : "", { thinking: true, holdMs: 0 });
  stopMouth();
  let full = "";
  let resReply = "";
  let hadPending = false;
  let gotFirst = false;
  const player = new TurnPlayer();
  currentPlan = "";

  try {
    const res = await connectCore(
      CORE_URL,
      text,
      (delta) => {
        full += delta;
        if (!gotFirst) {
          gotFirst = true;
          setThinking(false);
          typing.classList.remove("typing");
        }
        typing.textContent += delta;
        messages.scrollTop = messages.scrollHeight;
        showBubble(full, { holdMs: 0 });
      },
      {
        speak: ttsEnabled,
        attachments,
        onSpeech: (p) => {
          currentPlan = p.plan;
          if (myTurn === turnSeq && !speakAbort) player.add(speechPieceUrl(CORE_URL, p.id), !!p.stream);
        },
      },
    );
    resReply = res.reply;
    hadPending = !!res.pending;
    lastReplyText = res.reply || "";
    typing.remove();
    addMsg(res.reply, "aya");
    if (res.pending) actionCard(res.pending);
    showBubble(res.reply);
    setMood(res.mood);
    lastMood = res.mood;
    setChip(stateLabel(res, ayaName), false);
    if (res.music) ayaMusic.apply(res.music);
    // ядро не озвучило (подтверждение действия, озвучка выключена в ядре) — скажем итог сами
    if (ttsEnabled && player.added === 0 && (res.reply || "").trim() && myTurn === turnSeq) {
      void speakOnce(res.reply, res.mood);
    }
  } catch (err) {
    typing.remove();
    addMsg(`Не получилось: ${err instanceof Error ? err.message : err}`, "offline");
    hideBubble(true);
    setMood("sad", 6000);
  } finally {
    setThinking(false);
    busy = false;
  }

  if (!voice || myTurn !== turnSeq || speakAbort || hadPending || !resReply.trim()) return;

  // ждём, пока доиграет речь, и только потом решаем про «свободные руки»
  const drained = await (async () => {
    const deadline = Date.now() + 180_000;
    while (Date.now() < deadline) {
      if (myTurn !== turnSeq) return false;
      if (player.idle && !speakPlaying) return true;
      await new Promise((resolve) => window.setTimeout(resolve, 60));
    }
    return false;
  })();

  const spoke = player.played;
  if (!drained || myTurn !== turnSeq || speakAbort || busy || pendingId || !spoke || speakPlaying) return;
  const hidden = await isAyaHidden();
  await loadFollowupConfig();
  // За время await могла начаться инициатива, новый ход или настройку выключили.
  if (myTurn !== turnSeq || speakAbort || busy || pendingId || speakPlaying) return;
  // после ответа на «Ая, …» можно продолжить без имени — если включены «свободные руки»
  if (shouldArmWakeWindow({ wakeOn, followupOn, mode: voiceMode })) armWakeWindow(7000);
  if (
    !shouldArmFollowUp({
      enabled: followupOn,
      voiceTurn: true,
      mode: voiceMode,
      panelOpen,
      hidden,
      busy,
      pending: !!pendingId,
      reply: resReply,
      spoke,
      aborted: speakAbort,
    })
  )
    return;
  startFollowup();
}

/** Короткая фраза голосом вне хода (реакция, итог подтверждения). */
async function speakOnce(text: string, mood: string): Promise<void> {
  if (!ttsEnabled || !text.trim() || speakPlaying) return;
  ensureGainCtx();
  try {
    const r = await fetch(speakUrl(CORE_URL, text.slice(0, 300), mood));
    if (!r.ok) return;
    const buf = await r.arrayBuffer();
    if (busy || (rec && rec.state === "recording")) return;
    speakAbort = false;
    startMouth();
    await playTtsBuffer(buf);
  } catch {
    /* озвучка недоступна — пузыря достаточно */
  } finally {
    stopMouth();
    lastSpeechEndedAt = Date.now();
  }
}

/* ═════════════════════════ музыка ═════════════════════════ */

// Яндекс Музыка по токену играет прямо здесь; YouTube — в браузере.
const ayaMusic = new AyaMusic({
  coreUrl: CORE_URL,
  onTrack: (line) => {
    if (!busy && !speakPlaying) showBubble(line, { holdMs: 3500 });
  },
});

/* Пока Ая говорит или слушает — музыка тише (своя и в других приложениях).
   Следим за состоянием, а не ставим хуки в каждое место: между фразами речь
   на мгновение замолкает, поэтому возвращаем громкость с задержкой. */
let voiceDucked = false;
let voiceIdleSince = 0;
setInterval(() => {
  const listening = !!rec && rec.state === "recording" && recOrigin !== "wake";
  const active = speakPlaying || listening;
  const now = Date.now();
  if (active) voiceIdleSince = 0;
  else if (!voiceIdleSince) voiceIdleSince = now;
  const want = active || (voiceDucked && now - voiceIdleSince < 700);
  if (want === voiceDucked) return;
  voiceDucked = want;
  ayaMusic.duck(want);
  void fetch(`${CORE_URL}/api/music/duck`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ on: want, reason: "aya" }),
  }).catch(() => {});
}, 150);

/* ═════════════════════════ кнопка «Стоп» ═════════════════════════ */

const stopBtn = $<HTMLButtonElement>("stop-all");
function setStopVisible() {
  stopBtn.classList.toggle("hidden", !busy && !pendingId && !speakPlaying && followupTimer == null);
}
setInterval(setStopVisible, 500);

function stopEverything() {
  turnSeq++;
  cancelFollowup(true);
  stopAyaSpeech();
  if (currentPlan) cancelSpeechPlan(CORE_URL, currentPlan); // ядро перестаёт синтезировать хвост
  cancelPending();
  stopMouth();
}
stopBtn.addEventListener("click", stopEverything);

/* ═════════════════════════ микрофон ═════════════════════════ */

const micBtn = $<HTMLButtonElement>("mic");
const dockMic = $<HTMLButtonElement>("dock-mic");
const micButtons = [micBtn, dockMic];
const micClass = (cls: string, on: boolean) => micButtons.forEach((b) => b.classList.toggle(cls, on));

/** Откуда началась запись: кнопка/хоткей, свободные руки, режим «по голосу»,
 *  кандидат на «Ая, …» (тихо, пока не услышали имя) или окно после имени. */
type RecOrigin = "hold" | "followup" | "auto" | "wake" | "wake-armed" | "barge";

let voiceMode: VoiceMode = currentVoiceMode();
let rec: MediaRecorder | null = null;
let recOrigin: RecOrigin = "hold";
let chunks: Blob[] = [];
let liveStream: MediaStream | null = null;
let analyser: AnalyserNode | null = null;
let micCtx: AudioContext | null = null;
let vadTimer: ReturnType<typeof setInterval> | null = null;
const ep = new Endpointer();
const holdGate = new HoldGate(); // гейт «нажал-держим»: отменяет старт записи, если уже отпустили

// идёт полноэкранная игра (флаг из ядра): только клавиша, без отрисовки
let gameOn = false;

// «Ая, …»
let wakeOn = false;
let wakeExtra = "";
let wakeArmedUntil = 0;
let wakeArmTimer = 0;

function armWakeWindow(ms: number) {
  wakeArmedUntil = Date.now() + ms;
  micClass("followup", true);
  window.clearTimeout(wakeArmTimer);
  wakeArmTimer = window.setTimeout(() => {
    if (!(rec && rec.state === "recording") && followupTimer == null) micClass("followup", false);
  }, ms);
}

async function acquireStream(): Promise<MediaStream> {
  if (liveStream && liveStream.active) return liveStream;
  let micId = "";
  try {
    micId = localStorage.getItem("aya.micId") || ""; // выбран в мастере настройки
  } catch {}
  const audio: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, ...(micId ? { deviceId: { ideal: micId } } : {}) };
  liveStream = await navigator.mediaDevices.getUserMedia({ audio });
  // один контекст микрофона на всё время — раньше каждый новый поток создавал ещё один AudioContext
  if (!micCtx) micCtx = new AudioContext();
  if (micCtx.state === "suspended") void micCtx.resume().catch(() => {});
  const src = micCtx.createMediaStreamSource(liveStream);
  analyser = micCtx.createAnalyser();
  analyser.fftSize = 512;
  analyser.smoothingTimeConstant = 0.2;
  src.connect(analyser);
  ep.recalibrate();
  return liveStream;
}

async function startRec(origin: RecOrigin = "hold") {
  if (rec && rec.state === "recording") return;
  const quiet = origin === "wake"; // кандидат на имя: не показываем «Слушаю…», пока не ясно, что звали
  if (!quiet) stopAyaSpeech();
  if (!liveStream || !liveStream.active) await acquireStream();
  chunks = [];
  rec = new MediaRecorder(liveStream!);
  recOrigin = origin;
  rec.ondataavailable = (ev) => {
    if (ev.data.size) chunks.push(ev.data);
  };
  rec.onstop = onRecStop;
  rec.start();
  if (!quiet) turnSeq++;
  recTurn = turnSeq;
  recStartedAt = Date.now();
  followupRecording = followupTimer != null;
  if (followupRecording) {
    // Окно ожидания израсходовано: запись уже идёт. Таймер оставляем —
    // он теперь следит за тишиной и пределом 30 с, а не за окном.
    followupWindow.cancel();
    micClass("followup", false);
  }
  if (origin !== "hold") ep.begin();
  if (quiet) return;
  micClass("recording", true);
  setListening(true);
  showBubble("Слушаю…", { holdMs: 0 });
}

function stopRecNow() {
  holdGate.end(); // любой стоп отменяет ещё не начавшийся старт записи (гонка Pressed/Released)
  ep.reset();
  micClass("recording", false);
  micClass("listening", false);
  micClass("followup", false);
  setListening(false);
  clearFollowupTimers();
  followupWindow.cancel();
  if (rec && rec.state !== "inactive") rec.stop();
}

function restoreVoiceHint() {
  if (voiceMode === "hotkey") voiceHintSet(holdHint());
  else if (wakeOn && voiceMode !== "auto") voiceHintSet(`скажи «${ayaName}, …» — я услышу`);
  else voiceHintSet("");
}

function clearFollowupTimers() {
  if (followupTimer != null) {
    clearInterval(followupTimer);
    followupTimer = null;
  }
  if (followupVisibilityTimer != null) {
    clearInterval(followupVisibilityTimer);
    followupVisibilityTimer = null;
  }
}

async function loadFollowupConfig(): Promise<void> {
  try {
    const cfg = (await (await fetch(`${CORE_URL}/api/config`)).json()) as {
      followup?: boolean;
      followup_sec?: number;
    };
    if (typeof cfg.followup === "boolean") {
      followupOn = cfg.followup;
      if (!followupOn && followupTimer != null) cancelFollowup(true);
    }
    if (typeof cfg.followup_sec === "number") followupSec = clampFollowupSec(cfg.followup_sec);
  } catch {}
}

function startFollowup() {
  if (!followupOn || voiceMode === "auto" || busy || !panelOpen) return;
  if (rec && rec.state === "recording") return;
  clearFollowupTimers();
  followupRecording = false;
  followupWindow.arm(followupSec);
  micClass("followup", true);
  void acquireStream().catch(() => {});
  followupTimer = setInterval(followupTick, 30);
  followupVisibilityTimer = setInterval(() => {
    void (async () => {
      if (await isAyaHidden()) {
        cancelFollowup(true);
        return;
      }
      // Настройку могли выключить в консоли — не ждём общего опроса.
      await loadFollowupConfig();
    })();
  }, 1000);
  voiceHintSet(`свободные руки: слушаю ${followupWindow.remaining()} с…`);
}

function followupTick() {
  // Началась запись — окно уже выполнило свою роль. Дальше конец фразы
  // ловит детектор: ~0,65 с тишины или жёсткий предел 30 с.
  if (rec && rec.state === "recording") {
    if (busy) {
      cancelFollowup(true);
      return;
    }
    voiceHintSet("свободные руки: слушаю…");
    vadTick();
    return;
  }
  if (busy || !followupWindow.active()) {
    followupWindow.expire();
    cancelFollowup();
    return;
  }
  voiceHintSet(`свободные руки: слушаю ${followupWindow.remaining()} с…`);
  vadTick();
}

function cancelFollowup(discard = false) {
  // Останавливаем только запись, начатую свободными руками; ручную не трогаем.
  const wasFollowupRecording = followupRecording && !!(rec && rec.state === "recording");
  followupWindow.cancel();
  clearFollowupTimers();
  micClass("followup", false);
  if (wasFollowupRecording) {
    discardNextRecording = discard;
    stopRecNow();
  }
  restoreVoiceHint();
}

// Зажатие микрофона (кнопка или Ctrl+Space). Поток открывается асинхронно: если
// отпустили раньше, чем он открылся, — запись не стартует, токен успевает устареть.
async function beginHold() {
  if (busy) return;
  if (rec && rec.state === "recording") {
    if (recOrigin !== "wake") return;
    // шла тихая запись-кандидат на имя — кнопка важнее: выбрасываем её
    discardNextRecording = true;
    rec.stop();
  }
  cancelFollowup();
  const token = holdGate.begin();
  try {
    await acquireStream();
  } catch (err) {
    holdGate.end();
    addMsg(`Микрофон недоступен: ${err instanceof Error ? err.message : err}`, "offline");
    return;
  }
  if (!holdGate.isCurrent(token)) return;
  await startRec("hold").catch(() => {});
}

/* ═════════════════════════ воспроизведение речи ═════════════════════════ */

let ayaVolume = 1.0;
let ayaCtx: AudioContext | null = null;
let ayaGain: GainNode | null = null;
let ayaLip: AnalyserNode | null = null;
let speakAbort = false;
let killCurrent: (() => void) | null = null;
let speakPlaying = false;

function stopAyaSpeech() {
  speakAbort = true;
  killCurrent?.();
}

function ensureGainCtx() {
  try {
    if (!ayaCtx) ayaCtx = new AudioContext();
    if (!ayaGain) {
      ayaGain = ayaCtx.createGain();
      ayaGain.connect(ayaCtx.destination);
    }
    if (!ayaLip) {
      // анализатор между источником и громкостью: губы двигаются по реальному звуку
      ayaLip = ayaCtx.createAnalyser();
      ayaLip.fftSize = 1024;
      ayaLip.smoothingTimeConstant = 0.4;
      ayaLip.connect(ayaGain);
    }
    ayaGain.gain.value = ayaVolume;
    if (ayaCtx.state === "suspended") void ayaCtx.resume().catch(() => {});
  } catch {}
}

async function playTtsBuffer(data: ArrayBuffer): Promise<void> {
  speakPlaying = true;
  try {
    const ctx = ayaCtx;
    if (ctx) {
      if (ctx.state === "suspended") await ctx.resume().catch(() => {});
      if (ctx.state === "running") {
        try {
          const decoded = await ctx.decodeAudioData(data);
          if (speakAbort) return;
          const src = ctx.createBufferSource();
          src.buffer = decoded;
          src.connect(ayaLip ?? ayaGain ?? ctx.destination);
          attachLipSync(ayaLip);
          await new Promise<void>((resolve) => {
            let done = false;
            const finish = () => {
              killCurrent = killCurrent === stopNow ? null : killCurrent;
              if (done) return;
              done = true;
              window.clearTimeout(cap);
              try {
                src.stop();
              } catch {}
              resolve();
            };
            const stopNow = finish;
            killCurrent = stopNow;
            const cap = window.setTimeout(finish, decoded.duration * 1000 + 4000);
            src.onended = finish;
            void src.start();
          });
          return;
        } catch (e) {
          diag("play.decodeFail", { err: String(e instanceof Error ? e.message : e) });
        }
      } else {
        diag("play.ctxNotRunning", { state: ctx.state });
      }
    }
    // запасной путь через <audio>: губы — синусоидой
    attachLipSync(null);
    const blobUrl = URL.createObjectURL(new Blob([data]));
    await new Promise<void>((resolve) => {
      const a = new Audio();
      a.volume = Math.min(1, Math.max(0, ayaVolume));
      a.src = blobUrl;
      let done = false;
      const finish = () => {
        if (killCurrent === stopNow) killCurrent = null;
        if (done) return;
        done = true;
        window.clearTimeout(guard);
        URL.revokeObjectURL(blobUrl);
        resolve();
      };
      const stopNow = () => {
        try {
          a.pause();
        } catch {}
        finish();
      };
      killCurrent = stopNow;
      const guard = window.setTimeout(finish, 60000);
      a.onended = finish;
      a.onerror = finish;
      void a.play().catch((e) => diag("play.fallbackFail", { err: String(e instanceof Error ? e.message : e) }));
    });
  } finally {
    speakPlaying = false;
  }
}

/** Играет облачный кусок потоком: сырой PCM из ядра режется на маленькие
 *  AudioBuffer и ставится в очередь WebAudio встык — звук идёт с первого
 *  куска. Если ядро отдало готовый файл (кэш, запасной голос) — обычный путь.
 *  true — кусок прозвучал. */
async function playTtsStream(resP: Promise<Response>, ctl: AbortController): Promise<boolean> {
  let res: Response;
  try {
    res = await resP;
  } catch (e) {
    if (!speakAbort) diag("play.streamFetch", { err: String(e instanceof Error ? e.message : e) });
    return false;
  }
  if (speakAbort) {
    ctl.abort();
    return false;
  }
  if (!res.ok) return false;
  const pcm = parsePcmType(res.headers.get("content-type") || "");
  const ctx = ayaCtx;
  if (ctx && ctx.state === "suspended") await ctx.resume().catch(() => {});
  if (!pcm || !res.body || !ctx || ctx.state !== "running") {
    // готовый файл или WebAudio недоступен: дочитываем целиком
    let data: ArrayBuffer = await res.arrayBuffer();
    if (pcm) data = pcmToWav(new Uint8Array(data), pcm.rate);
    if (speakAbort) return false;
    startMouth();
    await playTtsBuffer(data);
    return !speakAbort;
  }
  speakPlaying = true;
  startMouth();
  attachLipSync(ayaLip);
  const out = ayaLip ?? ayaGain ?? ctx.destination;
  const reader = res.body.getReader();
  const chunker = new PcmChunker(Math.round(pcm.rate * 0.06)); // ~60 мс: не дробим на крошки
  const sources: AudioBufferSourceNode[] = [];
  let at = 0; // время ctx, когда закончится уже поставленный звук
  let started = false;
  let killed = false;
  const play = (frame: Float32Array | null) => {
    if (!frame || !frame.length) return;
    const buf = ctx.createBuffer(1, frame.length, pcm.rate);
    buf.getChannelData(0).set(frame);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(out);
    at = nextStart(at, ctx.currentTime, !started);
    src.start(at);
    at += buf.duration;
    started = true;
    sources.push(src);
    if (sources.length > 64) sources.splice(0, sources.length - 64); // старые уже доиграли
  };
  const stopNow = () => {
    killed = true;
    ctl.abort();
    for (const s of sources) {
      try {
        s.stop();
      } catch {}
    }
  };
  killCurrent = stopNow;
  try {
    for (;;) {
      let timer = 0;
      const stall = new Promise<null>((ok) => (timer = window.setTimeout(() => ok(null), 20000)));
      const r = await Promise.race([reader.read(), stall]);
      window.clearTimeout(timer);
      if (killed || speakAbort) break;
      if (r === null) {
        diag("play.streamStall", {}); // облако замолчало — доигрываем, что есть
        ctl.abort();
        break;
      }
      if (r.done) break;
      if (r.value) play(chunker.push(r.value));
    }
    if (!killed && !speakAbort) play(chunker.flush());
    while (!killed && !speakAbort && ctx.currentTime < at) {
      await new Promise((ok) => window.setTimeout(ok, Math.min(200, Math.max(20, (at - ctx.currentTime) * 1000))));
    }
    return started && !killed && !speakAbort;
  } catch (e) {
    if (!killed && !speakAbort) diag("play.streamFail", { err: String(e instanceof Error ? e.message : e) });
    return started;
  } finally {
    if (killed || speakAbort) stopNow();
    if (killCurrent === stopNow) killCurrent = null;
    speakPlaying = false;
  }
}

/* Диагностика: в памяти (window.__ayaDiag). В ядро пишется только при
   localStorage aya.debug=1 — раньше каждый кусок речи слал лишний POST,
   а отладочная строка висела поверх аватара. */
const DEBUG = (() => {
  try {
    return localStorage.getItem("aya.debug") === "1";
  } catch {
    return false;
  }
})();

function diag(tag: string, data: Record<string, unknown> = {}) {
  const rec: Record<string, unknown> = { tag, ts: Date.now(), ...data };
  const dbg = window as unknown as Record<string, unknown>;
  const arr = (dbg.__ayaDiag as unknown[]) ?? [];
  arr.push(rec);
  dbg.__ayaDiag = arr.slice(-60);
  if (!DEBUG) return;
  void fetch(`${CORE_URL}/api/_diag`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rec),
  }).catch(() => {});
}

window.addEventListener("pointerdown", ensureGainCtx, { capture: true });
window.addEventListener("keydown", ensureGainCtx, { capture: true });

function applyAyaVolume(pct: number) {
  ayaVolume = Math.max(0, Math.min(3, pct / 100));
  if (ayaGain) ayaGain.gain.value = ayaVolume;
}

try {
  const ls = Number(localStorage.getItem("ayaTtsVolume") ?? "");
  if (!Number.isNaN(ls) && ls > 0) applyAyaVolume(ls);
} catch {}

window.addEventListener("aya-tts-volume", (e) => applyAyaVolume(Number((e as CustomEvent).detail || 100)));

function looksLikeEcho(text: string): boolean {
  // микрофон услышал её саму из колонок — не отвечаем сами себе
  const n = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const t = n(text);
  return t.length >= 6 && Date.now() - lastSpeechEndedAt < 8000 && n(lastReplyText).includes(t);
}

async function onRecStop() {
  // Токен записи, а не текущий момент: если за время записи начался текст или
  // другой ход, эта запись уже устарела и не должна отправляться.
  const myTurn = recTurn;
  const origin = recOrigin;
  const wasFollowup = followupRecording;
  followupRecording = false;
  restoreVoiceHint();
  const blob = new Blob(chunks, { type: rec?.mimeType || "audio/webm" });
  chunks = [];
  if (discardNextRecording) {
    discardNextRecording = false;
    if (origin !== "wake") hideBubble(true);
    return;
  }
  if (!blob.size || busy) {
    if (origin !== "wake") hideBubble(true);
    return;
  }
  const quiet = origin === "wake";
  if (!quiet) {
    micClass("listening", true);
    showBubble("Расшифровываю…", { thinking: true, holdMs: 0 });
  }
  try {
    let text = await transcribe(CORE_URL, blob);
    micClass("listening", false);
    // Пока шла расшифровка, мог начаться текст/новый ход, сработать «Стоп»,
    // закрыться панель или окно — тогда старую запись уже нельзя отправлять.
    if (myTurn !== turnSeq || busy) return;
    if (origin === "wake" || origin === "wake-armed") {
      if (!text || looksLikeEcho(text)) return;
      const m = matchWake(text, wakeNames(ayaName, wakeExtra));
      if (origin === "wake" && !m.matched) return; // говорили не с ней — тихо забываем
      if (m.matched) text = m.rest;
      await showSelf(); // позвали, пока пряталась в трее, — показываемся
      if (!text) {
        // просто «Ая!» — откликаемся и ждём продолжения без имени
        turnSeq++;
        playReaction("hello", "joyful");
        showBubble("Да? Слушаю!", { holdMs: 4000 });
        armWakeWindow(8000);
        void speakOnce("Да?", "joyful");
        return;
      }
      turnSeq++;
    } else if (dropWhenHidden(origin, await isAyaHidden())) return; // свёрнута: по клавише отвечаем голосом
    if (text) {
      addMsg(text, "user");
      await sendToAya(text, true);
    } else {
      hideBubble(true);
      if (voiceMode === "button" && !wasFollowup) {
        addMsg("Не расслышала… повтори громче?", "aya");
        showBubble("Не расслышала… повтори громче?");
      }
    }
  } catch (err) {
    micClass("listening", false);
    if (quiet) return;
    hideBubble(true);
    addMsg(`Распознавание не сработало: ${err instanceof Error ? err.message : err}`, "offline");
  }
}

async function showSelf() {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const w = getCurrentWindow();
    if (!(await w.isVisible())) await w.show();
    setHidden(false);
  } catch {}
}

for (const b of micButtons) {
  b.addEventListener("pointerdown", () => void beginHold());
  b.addEventListener("pointerup", stopRecNow);
  b.addEventListener("pointerleave", () => {
    if (rec && rec.state === "recording" && recOrigin === "hold") stopRecNow();
    else holdGate.end();
  });
}

/* ═════════════════════════ перебивание и детектор речи ═════════════════════════ */

let bargeMs = 0;

function bargeCheck(level: number, stepMs: number): boolean {
  if (!speakPlaying || voiceMode === "button") {
    bargeMs = 0;
    return false;
  }
  if (level > Math.max(0.16, ep.threshold() * 3)) {
    bargeMs += stepMs;
    if (bargeMs >= 300) {
      bargeMs = 0;
      diag("barge.in", { level: Number(level.toFixed(3)) });
      stopAyaSpeech();
      if (currentPlan) cancelSpeechPlan(CORE_URL, currentPlan);
      cancelPending();
      stopMouth();
      if (!(rec && rec.state === "recording")) startRec("barge").catch(() => {});
      return true;
    }
  } else {
    bargeMs = 0;
  }
  return false;
}

const vadBuf = new Uint8Array(256);
let lastVadAt = 0;

function vadTick() {
  if (!analyser || !micCtx) return;
  const now = performance.now();
  const dt = now - lastVadAt;
  if (dt < 20) return; // таймеры свободных рук и фонового слушания могут совпасть
  lastVadAt = now;
  const step = Math.min(dt, 120);
  analyser.getByteFrequencyData(vadBuf);
  const level = speechLevel(vadBuf, micCtx.sampleRate, analyser.fftSize);
  if (bargeCheck(level, step)) return;
  if (rec && rec.state === "recording") {
    if (recOrigin === "hold") return; // кнопка: конец — по отпусканию
    if (Date.now() - recStartedAt > 30_000 || ep.feed(level, step) === "end") stopRecNow();
    return;
  }
  if (ep.feed(level, step) !== "start") return;
  // кто-то заговорил — решаем, нужно ли это записывать
  const blocked = busy || speakPlaying || !!pendingId || Date.now() - lastSpeechEndedAt < 600;
  let origin: RecOrigin | null = null;
  if (followupTimer != null) origin = "followup";
  else if (voiceMode === "auto") origin = "auto";
  else if (wakeOn) origin = Date.now() < wakeArmedUntil ? "wake-armed" : "wake";
  if (blocked || !origin) {
    ep.reset();
    return;
  }
  startRec(origin).catch(() => {});
}

/** Микрофон слушает постоянно в режиме «по голосу» и при «Ая, …» — но не в игре. */
function needsListening(): boolean {
  return listensAlways(voiceMode, wakeOn, gameOn);
}

/* ═════════════════════════ игровой режим ═════════════════════════ */

/** Ядро заметило полноэкранную игру (или она закончилась). */
async function applyGame(game: { on?: boolean } | undefined) {
  const on = !!game?.on;
  if (on === gameOn) return;
  gameOn = on;
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    // не «поверх всех»: игра закрывает Аю, и Windows выводит кадры игры напрямую (без рывков);
    // после игры Ая снова сверху — без перехвата фокуса у той программы, где ты сейчас
    await getCurrentWindow().setAlwaysOnTop(!on);
  } catch {}
  if (on && followupTimer != null) cancelFollowup(true);
  await applyVoiceMode(); // в игре — только клавиша, микрофон отпускаем
  await syncRender();
}

/** Рисовать аватар, только когда окно видно и игры нет. */
async function syncRender() {
  setHidden(!rendersNow(gameOn, await isAyaHidden()));
}

async function applyVoiceMode() {
  turnSeq++;
  if (followupTimer != null) cancelFollowup(true);
  const gs = await import("@tauri-apps/plugin-global-shortcut").catch(() => null);
  if (needsListening()) {
    if (vadTimer == null) {
      await acquireStream().catch(() => {});
      vadTimer = setInterval(vadTick, 30);
    }
  } else if (vadTimer != null) {
    clearInterval(vadTimer);
    vadTimer = null;
    if (rec && rec.state === "recording" && recOrigin !== "hold") stopRecNow();
  }
  if (!needsListening() && liveStream && !(rec && rec.state === "recording")) {
    // микрофон не нужен постоянно — отпускаем устройство (значок записи в Windows гаснет)
    liveStream.getTracks().forEach((t) => t.stop());
    liveStream = null;
  }
  const mode = gameVoiceMode(voiceMode, gameOn);
  if (mode === "hotkey") {
    if (gs && hotkeyAction(mode, hotkeyBound) === "register") {
      await bindHotkey(gs);
    } else if (hotkeyBound) {
      voiceHintSet(holdHint());
    }
  } else {
    if (gs && hotkeyBound) {
      try {
        await gs.unregister(talkAccel);
      } catch {}
      hotkeyBound = false;
    }
    if (rec && rec.state === "recording" && recOrigin === "hold") stopRecNow();
    restoreVoiceHint();
  }
  syncVoiceSeg();
}

function setWake(on: boolean) {
  if (on === wakeOn) return;
  wakeOn = on;
  void applyVoiceMode();
}

type GsApi = typeof import("@tauri-apps/plugin-global-shortcut");

// Клавиша «говорить» (по умолчанию Ctrl+Space, своя — из настроек). Регистрируем,
// только когда живой привязки нет (hotkeyBound=false) — повторные вызовы applyVoiceMode её не трогают.
let talkAccel = "Control+Space";
let toggleAccel = ""; // «показать/скрыть Аю»; пусто — ещё не привязана

const onTalkKey = ({ state }: { state: "Pressed" | "Released" }) => {
  if (state === "Pressed") void beginHold();
  else stopRecNow();
};

function holdHint() {
  return `${accelLabel(talkAccel)}: зажми в любом окне и говори`;
}

async function bindHotkey(gs: GsApi) {
  try {
    await gs.register(talkAccel, onTalkKey);
  } catch {
    // Регистрация отказала — возможно, осталась привязка от прошлой загрузки
    // страницы (HMR/перезагрузка окна). Снимаем её и пробуем ещё раз.
    try {
      await gs.unregister(talkAccel);
      await gs.register(talkAccel, onTalkKey);
    } catch (e) {
      hotkeyBound = false;
      voiceHintSet(e instanceof Error ? `Хоткей: ${e.message}` : String(e));
      return;
    }
  }
  hotkeyBound = true;
  voiceHintSet(holdHint());
}

async function onToggleKey({ state }: { state: "Pressed" | "Released" }) {
  if (state !== "Pressed") return;
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const w = getCurrentWindow();
    if ((await w.isVisible()) && !(await w.isMinimized())) await w.hide();
    else {
      await w.unminimize().catch(() => {});
      await w.show();
      await w.setFocus().catch(() => {});
      setHidden(false);
    }
  } catch {}
}

type HotkeyKey = "hotkey_talk" | "hotkey_toggle";

/** Поменять сочетание. Новое регистрируем первым — не вышло, старое остаётся. */
async function changeHotkey(key: HotkeyKey, accel: string): Promise<{ ok: boolean; error?: string }> {
  const gs = await import("@tauri-apps/plugin-global-shortcut").catch(() => null);
  if (!gs) return { ok: false, error: "busy" };
  if (key === "hotkey_toggle") {
    const r = await rebind(gs, toggleAccel, accel, (e) => void onToggleKey(e));
    if (r.ok) toggleAccel = accel;
    return r;
  }
  if (hotkeyBound) {
    const r = await rebind(gs, talkAccel, accel, onTalkKey);
    if (r.ok) {
      talkAccel = accel;
      voiceHintSet(holdHint());
    }
    return r;
  }
  // режим «клавишей» выключен — только проверяем, что сочетание свободно
  try {
    await gs.register(accel, () => {});
    await gs.unregister(accel);
  } catch {
    return { ok: false, error: "busy" };
  }
  talkAccel = accel;
  return { ok: true };
}

/** Настройки пришли из ядра (на старте или после смены в консоли). */
async function syncHotkeys(cfg: UiState["config"]) {
  const talk = cfg.hotkey_talk || "Control+Space";
  const toggle = cfg.hotkey_toggle || "Alt+Shift+A";
  if (toggle !== toggleAccel) await changeHotkey("hotkey_toggle", toggle);
  if (talk !== talkAccel) await changeHotkey("hotkey_talk", talk);
}

let hotkeyBound = false;

function voiceHintSet(text: string) {
  $("voice-hint").textContent = text;
}

initVoice((mode) => {
  voiceMode = mode;
  void applyVoiceMode();
});
void applyVoiceMode();

/* ═════════════════════════ общий опрос ядра ═════════════════════════ */

interface UiState {
  state: { mood?: string; activity?: string };
  game?: { on: boolean; app?: string; enabled?: boolean };
  config: {
    tts_volume?: number;
    voice_mode?: string;
    initiative?: boolean;
    followup?: boolean;
    followup_sec?: number;
    tts_enabled?: boolean;
    avatar_reactions?: boolean;
    avatar_idle_life?: boolean;
    avatar_scale?: number;
    persona_name?: string;
    wake_word?: boolean;
    wake_names?: string;
    avatar_walk?: boolean;
    hotkey_talk?: string;
    hotkey_toggle?: string;
  };
  focus: boolean;
  pomodoro?: PomoState;
  music?: unknown;
}

let lastUi: UiState | null = null;
let staleWarned = false;

function setChip(text: string, offline: boolean) {
  chip.textContent = text;
  chip.classList.toggle("offline", offline);
}

function setName(name: string) {
  if (!name || name === ayaName) return;
  ayaName = name;
  whoName.textContent = name;
  whoMark.textContent = name.slice(0, 1).toUpperCase();
  bubbleName.textContent = name;
  document.title = name;
}

let coreSeen = false; // ядро уже отвечало в этом запуске

async function refreshState(): Promise<boolean> {
  let ui: UiState;
  try {
    ui = (await (await fetch(`${CORE_URL}/api/ui`)).json()) as UiState;
  } catch {
    // на старте ядро ещё поднимается — это не ошибка
    setChip(coreSeen ? "ядро не отвечает" : "просыпаюсь…", coreSeen);
    return false;
  }
  coreSeen = true;
  lastUi = ui;
  if (ui.pomodoro === undefined && !staleWarned) {
    // ядро запущено старой версией (например, осталось от прошлого запуска) — новые функции не заработают
    staleWarned = true;
    toast("Ядро старой версии — закрой stop-aya.cmd и открой Аю заново");
  }
  const cfg = ui.config || {};
  setName(cfg.persona_name || "Ая");
  setChip(stateLabel(ui.state, ayaName), false);
  if (ui.state.mood) lastMood = ui.state.mood;
  applyVoiceModeValue(cfg.voice_mode);
  if (typeof cfg.tts_volume === "number") applyAyaVolume(cfg.tts_volume);
  if (typeof cfg.initiative === "boolean") initiativeOn = cfg.initiative;
  if (typeof cfg.tts_enabled === "boolean") ttsEnabled = cfg.tts_enabled;
  if (typeof cfg.avatar_reactions === "boolean") reactionsOn = cfg.avatar_reactions;
  if (typeof cfg.followup === "boolean") {
    followupOn = cfg.followup;
    if (!followupOn && followupTimer != null) cancelFollowup(true);
  }
  if (typeof cfg.followup_sec === "number") followupSec = clampFollowupSec(cfg.followup_sec);
  if (!scaleSaving) applyAvatarSettings({ scale: cfg.avatar_scale, reactions: cfg.avatar_reactions, idleLife: cfg.avatar_idle_life });
  focusOn = !!ui.focus;
  wakeExtra = cfg.wake_names || "";
  setWake(!!cfg.wake_word);
  walkOn = !!cfg.avatar_walk;
  void syncHotkeys(cfg);
  if (ui.pomodoro) onPomodoro(ui.pomodoro);
  if (ui.music) ayaMusic.apply(ui.music);
  menuFocusLabel();

  if (lastTrayFollowup !== followupOn) {
    const ok = await setTrayCheck("followup", followupOn);
    if (ok) lastTrayFollowup = followupOn;
  }
  if (lastTrayFocus !== focusOn) {
    const ok = await setTrayCheck("focus", focusOn);
    if (ok) lastTrayFocus = focusOn;
  }
  await applyGame(ui.game);
  // окно спрятано в трей или идёт игра — не тратим GPU на рендер
  await syncRender();
  return true;
}
setInterval(refreshState, 5000);

/** Запуск: спрашиваем ядро часто, пока не ответит, — настройки и музыка подтянутся сразу. */
async function bootState() {
  const until = Date.now() + 60_000;
  while (!(await refreshState()) && Date.now() < until) {
    await new Promise((ok) => setTimeout(ok, 300));
  }
  await firstRunGate();
}

/** Мастер не пройден — Аю не показываем, открываем настройку в консоли. */
async function firstRunGate() {
  let done = true;
  try {
    done = ((await (await fetch(`${CORE_URL}/api/setup`)).json()) as { done: boolean }).done;
  } catch {
    return;
  }
  if (done) return;
  try {
    const { getCurrentWindow, Window } = await import("@tauri-apps/api/window");
    await getCurrentWindow().hide();
    const c = await Window.getByLabel("console");
    await c?.show();
    await c?.setFocus().catch(() => {});
    const { emitTo } = await import("@tauri-apps/api/event");
    await emitTo("console", "aya-nav", "setup");
  } catch {}
}

/* ═════════════════════════ инициатива ═════════════════════════ */

let initiativeOn = true;
let initiativeCool = 0;
let focusOn = false;
let lastTrayFollowup: boolean | null = null;
let lastTrayFocus: boolean | null = null;

/** Напоминание пришло посреди разговора: не перебиваем, но и не теряем. */
function deliverQuiet(text: string) {
  addMsg(text, "aya initiative");
  void notifyAya(toastBody(text));
}

async function pollInitiative() {
  // инициатива выключена — всё равно спрашиваем: напоминания человек просил сам
  if (busy || gameOn) return; // в игре сама не заговаривает
  if (rec && rec.state === "recording") return;
  if (Date.now() < initiativeCool) return;
  const myTurn = turnSeq;
  const recording = () => !!(rec && rec.state === "recording");
  try {
    const d = (await (await fetch(`${CORE_URL}/api/initiative${initiativeQuery(initiativeOn)}`)).json()) as {
      topic?: string;
      reply?: string;
      mood?: string;
    };
    if (!d.topic || !d.reply) return;
    const topic = d.topic;
    const how = initiativeDelivery(topic, initiativeOn, myTurn !== turnSeq || busy || recording());
    if (how === "drop") return;
    if (how === "quiet") return deliverQuiet(d.reply);

    // Инициатива — самостоятельный ход: она инвалидирует ожидание свободных
    // рук после предыдущего голосового ответа и не должна перебивать новый ход.
    const initiativeTurn = ++turnSeq;
    cancelFollowup(true);
    const hidden = await isAyaHidden();
    const howNow = initiativeDelivery(topic, initiativeOn, initiativeTurn !== turnSeq || busy || recording());
    if (howNow === "drop") return;
    if (howNow === "quiet") return deliverQuiet(d.reply);
    if (shouldToast(hidden, focusOn)) {
      void notifyAya(toastBody(d.reply));
    }
    initiativeCool = Date.now();
    addMsg(d.reply, "aya initiative");
    showBubble(d.reply);
    setMood(d.mood || "calm");
    lastMood = d.mood || lastMood;
    setChip(stateLabel(d, ayaName), false);
    if (!ttsEnabled) return;
    try {
      startMouth();
      const buf = await (await fetch(speakUrl(CORE_URL, d.reply, d.mood || "calm"))).arrayBuffer();
      // Пока грузился TTS, мог начаться текст/голосовой ход — не перебиваем его
      // (текст уже в чате и в пузыре).
      if (initiativeDelivery(topic, initiativeOn, initiativeTurn !== turnSeq || busy || recording()) !== "full") return;
      ensureGainCtx();
      speakAbort = false;
      await playTtsBuffer(buf);
    } catch {
    } finally {
      stopMouth();
    }
  } catch {
    /* core недоступен — молчим */
  }
}
setInterval(pollInitiative, 45000);

window.addEventListener("aya-initiative", (e) => {
  initiativeOn = (e as CustomEvent).detail !== false;
});

/* ═════════════════════════ касания аватара ═════════════════════════ */

let reactCool = 0;
let wakeCool = 0;

async function react(kind: "pat" | "poke" | "hello" | "wake") {
  const now = Date.now();
  if (now < reactCool) return;
  reactCool = now + 1800;
  if (!reactionsOn) {
    playReaction(kind); // только движение, без слов
    return;
  }
  // во время разговора — только мимика, без реплики
  if (busy || speakPlaying || (rec && rec.state === "recording")) {
    playReaction(kind);
    return;
  }
  try {
    const r = (await (
      await fetch(`${CORE_URL}/api/react`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind }),
      })
    ).json()) as { text: string; mood: string };
    playReaction(kind, r.mood);
    showBubble(r.text, { holdMs: 3500 });
    void speakOnce(r.text, r.mood);
  } catch {
    playReaction(kind);
  }
}

window.addEventListener("aya-tap", (e) => {
  const d = (e as CustomEvent).detail as { zone: string; long: boolean };
  hideMenu();
  void react(d.zone === "head" ? "pat" : d.long ? "hello" : "poke");
});
window.addEventListener("aya-rub", () => void react("pat"));
window.addEventListener("aya-dblclick", () => (panelOpen ? closePanel() : openPanel()));
window.addEventListener("aya-wake", () => {
  const now = Date.now();
  if (now < wakeCool || focusOn) return;
  wakeCool = now + 30 * 60_000;
  void react("wake");
});

let scaleSaving = false;
let scaleTimer = 0;
function saveScale(v: number) {
  scaleSaving = true;
  toast(`Размер ${Math.round(v * 100)}%`);
  window.clearTimeout(scaleTimer);
  scaleTimer = window.setTimeout(async () => {
    try {
      await postJson("/api/config", { avatar_scale: v });
    } finally {
      scaleSaving = false;
    }
  }, 500);
}
window.addEventListener("aya-scale", (e) => saveScale((e as CustomEvent).detail.scale));

/* ═════════════════════════ контекстное меню ═════════════════════════ */

function showMenu(x: number, y: number) {
  menu.classList.remove("hidden");
  menuFocusLabel();
  const w = menu.offsetWidth;
  const h = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, x))}px`;
  menu.style.top = `${Math.max(8, Math.min(window.innerHeight - h - 8, y))}px`;
}

function hideMenu() {
  menu.classList.add("hidden");
}

function menuFocusLabel() {
  const set = (k: string, t: string) => {
    const l = menu.querySelector<HTMLElement>(`[data-label="${k}"]`);
    if (l) l.textContent = t;
  };
  set("focus", focusOn ? "Снять тишину" : "Тишина");
  set("pomodoro", pomo.phase !== "idle" ? "Остановить помодоро" : "Помодоро 25/5");
  set("walk", walkOn ? "Не гулять" : "Гулять по экрану");
  set("wake", wakeOn ? "Не слушать имя" : `Слушать «${ayaName}, …»`);
}

window.addEventListener("aya-menu", (e) => {
  const d = (e as CustomEvent).detail as { x: number; y: number };
  showMenu(d.x, d.y);
});
$("dock-more").addEventListener("click", (e) => {
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
  showMenu(r.left - 200, r.top - 120);
});
document.addEventListener("pointerdown", (e) => {
  if (!menu.contains(e.target as Node) && !(e.target as HTMLElement).closest?.("#dock-more")) hideMenu();
});
// клик по рабочему столу (сквозь окно) уводит фокус — меню закрываем
window.addEventListener("blur", hideMenu);

menu.addEventListener("click", async (e) => {
  const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-act]");
  if (!btn) return;
  const act = btn.dataset.act;
  if (act !== "smaller" && act !== "bigger") hideMenu();
  if (act === "chat") openPanel();
  else if (act === "pat") void react("pat");
  else if (act === "focus") await setFocus(!focusOn);
  else if (act === "pomodoro") await togglePomodoro();
  else if (act === "game") playGame(btn.dataset.game || "riddle");
  else if (act === "walk") await toggleSetting("avatar_walk", !walkOn);
  else if (act === "wake") await toggleSetting("wake_word", !wakeOn);
  else if (act === "smaller" || act === "bigger") {
    const v = clampScale(getAvatarScale() * (act === "bigger" ? 1.08 : 0.92));
    setAvatarScale(v);
    saveScale(v);
  } else if (act === "console") void openConsole();
  else if (act === "wizard") void openConsole("wizard");
  else if (act === "hide") void hideWindow();
});

async function setFocus(on: boolean) {
  try {
    await postJson("/api/proactive", { focus: on });
    focusOn = on;
    menuFocusLabel();
    toast(on ? "Тишина: не буду отвлекать" : "Снова на связи");
    if (await setTrayCheck("focus", on)) lastTrayFocus = on;
  } catch {
    toast("Ядро не отвечает");
  }
}

async function hideWindow() {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().hide();
  } catch {
    toast("Спрятать можно только в приложении");
  }
}

async function openConsole(section = "") {
  try {
    const { Window } = await import("@tauri-apps/api/window");
    const w = await Window.getByLabel("console");
    if (!w) throw new Error("нет окна");
    await w.unminimize().catch(() => {});
    await w.show();
    await w.setFocus().catch(() => {});
    if (section) {
      const { emitTo } = await import("@tauri-apps/api/event");
      await emitTo("console", "aya-nav", section);
    }
  } catch {
    window.open(`${CORE_URL}/console.html${section ? `#${section}` : ""}`, "_blank");
  }
}

/* ═════════════════════════ быстрые настройки ═════════════════════════ */

async function postJson(path: string, body: unknown) {
  const r = await fetch(`${CORE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(String(r.status));
  return r.json().catch(() => ({}));
}

const clickThroughOn = () => {
  try {
    return localStorage.getItem("aya.clickThrough") !== "0";
  } catch {
    return true;
  }
};

function q<T extends HTMLElement = HTMLInputElement>(key: string) {
  return viewQuick.querySelector<T>(`[data-q="${key}"]`)!;
}

async function syncQuick() {
  await refreshState();
  const ui = lastUi;
  if (!ui) return;
  const c = ui.config;
  q("tts_enabled").checked = c.tts_enabled !== false;
  q("focus").checked = !!ui.focus;
  q("followup").checked = !!c.followup;
  q("avatar_reactions").checked = c.avatar_reactions !== false;
  q("avatar_idle_life").checked = c.avatar_idle_life !== false;
  q("wake_word").checked = !!c.wake_word;
  q("avatar_walk").checked = !!c.avatar_walk;
  q("click_through").checked = clickThroughOn();
  q("tts_volume").value = String(c.tts_volume ?? 100);
  $("q-vol").textContent = `${c.tts_volume ?? 100}%`;
  const sc = Math.round(getAvatarScale() * 100);
  q("avatar_scale").value = String(sc);
  $("q-scale").textContent = `${sc}%`;
  syncVoiceSeg();
}

function syncVoiceSeg() {
  $("q-voice-mode")
    .querySelectorAll<HTMLButtonElement>("button")
    .forEach((b) => b.classList.toggle("active", b.dataset.mode === voiceMode));
}

for (const key of ["tts_enabled", "followup", "avatar_reactions", "avatar_idle_life", "wake_word", "avatar_walk"]) {
  q(key).addEventListener("change", async (e) => {
    const v = (e.target as HTMLInputElement).checked;
    try {
      await postJson("/api/config", { [key]: v });
      if (key === "tts_enabled") ttsEnabled = v;
      if (key === "followup") followupOn = v;
      if (key === "avatar_reactions") reactionsOn = v;
      if (key === "wake_word") {
        setWake(v);
        toast(v ? `Скажи «${ayaName}, …» — я услышу` : "Больше не слушаю имя");
      }
      if (key === "avatar_walk") {
        walkOn = v;
        toast(v ? "Пойду иногда прогуляться по экрану" : "Буду стоять на месте");
      }
      applyAvatarSettings({ reactions: key === "avatar_reactions" ? v : undefined, idleLife: key === "avatar_idle_life" ? v : undefined });
    } catch {
      (e.target as HTMLInputElement).checked = !v;
      toast("Ядро не отвечает");
    }
  });
}
q("focus").addEventListener("change", (e) => void setFocus((e.target as HTMLInputElement).checked));
q("click_through").addEventListener("change", (e) => {
  try {
    localStorage.setItem("aya.clickThrough", (e.target as HTMLInputElement).checked ? "1" : "0");
  } catch {}
});

let volTimer = 0;
q("tts_volume").addEventListener("input", (e) => {
  const v = Number((e.target as HTMLInputElement).value);
  $("q-vol").textContent = `${v}%`;
  applyAyaVolume(v);
  window.clearTimeout(volTimer);
  volTimer = window.setTimeout(() => void postJson("/api/config", { tts_volume: v }).catch(() => {}), 350);
});
q("avatar_scale").addEventListener("input", (e) => {
  const v = clampScale(Number((e.target as HTMLInputElement).value) / 100);
  $("q-scale").textContent = `${Math.round(v * 100)}%`;
  setAvatarScale(v);
  saveScale(v);
});
$("q-voice-mode").addEventListener("click", async (e) => {
  const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-mode]");
  if (!b) return;
  try {
    await postJson("/api/config", { voice_mode: b.dataset.mode });
    applyVoiceModeValue(b.dataset.mode, true);
  } catch {
    toast("Ядро не отвечает");
  }
});
$("q-console").addEventListener("click", () => void openConsole());
$("q-wizard").addEventListener("click", () => void openConsole("wizard"));

let toastTimer = 0;
function toast(text: string) {
  toastEl.textContent = text;
  toastEl.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toastEl.classList.remove("show"), 1800);
}

/* ═════════════════════════ помодоро ═════════════════════════ */

interface PomoState {
  phase: "idle" | "focus" | "break";
  left: number;
  total: number;
  round: number;
  rounds: number;
  focus_min: number;
  break_min: number;
  seq: number;
  event: string;
}

let pomo: PomoState = { phase: "idle", left: 0, total: 0, round: 0, rounds: 0, focus_min: 25, break_min: 5, seq: -1, event: "" };
let pomoSeenSeq = -1;
let pomoTick = 0;
const pomoBtn = $<HTMLButtonElement>("pomo");

function renderPomo() {
  const on = pomo.phase !== "idle";
  pomoBtn.classList.toggle("hidden", !on);
  if (!on) return;
  const m = Math.floor(pomo.left / 60);
  const sec = pomo.left % 60;
  $("pomo-time").textContent = `${m}:${String(sec).padStart(2, "0")}`;
  pomoBtn.classList.toggle("break", pomo.phase === "break");
  pomoBtn.style.setProperty("--p", String(pomo.total ? Math.round((1 - pomo.left / pomo.total) * 100) : 0));
  pomoBtn.title = `${pomo.phase === "focus" ? "Фокус" : "Перерыв"} · раунд ${pomo.round} из ${pomo.rounds} — нажми, чтобы остановить`;
}

const POMO_LINES: Record<string, (p: PomoState) => string> = {
  start: (p) => `Поехали! ${p.focus_min} минут фокуса — я не отвлекаю.`,
  break: (p) => `Перерыв! Встань, потянись, глотни воды. Через ${p.break_min} минут продолжим.`,
  focus: (p) => `Перерыв окончен — снова в фокус. Раунд ${p.round} из ${p.rounds}.`,
  done: () => "Все раунды позади! Ты молодец — теперь отдохни как следует.",
};

function onPomodoro(p: PomoState) {
  const first = pomoSeenSeq === -1;
  pomo = p;
  renderPomo();
  // ведём секундомер локально между опросами
  window.clearInterval(pomoTick);
  if (p.phase !== "idle") {
    pomoTick = window.setInterval(() => {
      if (pomo.left > 0) pomo.left--;
      renderPomo();
    }, 1000);
  }
  if (p.seq !== pomoSeenSeq) {
    pomoSeenSeq = p.seq;
    const line = POMO_LINES[p.event];
    if (!first && line) {
      const text = line(p);
      showBubble(text, { holdMs: 6000 });
      setMood(p.event === "break" || p.event === "done" ? "joyful" : "calm", 6000);
      if (p.event === "break" || p.event === "done") playReaction("hello", "joyful");
      void speakOnce(text, "joyful");
    }
  }
  menuFocusLabel();
}

async function togglePomodoro() {
  try {
    const r = (await postJson("/api/pomodoro", pomo.phase === "idle" ? { action: "start", focus: 25, rest: 5, rounds: 4 } : { action: "stop" })) as PomoState;
    onPomodoro(r);
    if (r.phase === "idle") toast("Помодоро остановлен");
  } catch {
    toast("Ядро не отвечает");
  }
}

pomoBtn.addEventListener("click", () => void togglePomodoro());

/* ═════════════════════════ игры ═════════════════════════ */

const GAMES: Record<string, string> = {
  cities: "Давай сыграем в города! Правила: называем города по последней букве предыдущего. Начинай ты, коротко.",
  riddle: "Загадай мне загадку! Не говори ответ, пока я не сдамся или не угадаю.",
  quiz: "Устрой мне мини-викторину: задавай по одному интересному вопросу с тремя вариантами ответа и считай очки.",
};

function playGame(id: string) {
  const prompt = GAMES[id] ?? GAMES.riddle;
  if (busy) return;
  openPanel();
  addMsg(prompt, "user");
  void sendToAya(prompt);
}

/* ═════════════════════════ настройки из меню ═════════════════════════ */

let walkOn = false;

async function toggleSetting(key: "avatar_walk" | "wake_word", v: boolean) {
  try {
    await postJson("/api/config", { [key]: v });
    if (key === "avatar_walk") {
      walkOn = v;
      toast(v ? "Пойду иногда прогуляться по экрану" : "Буду стоять на месте");
    } else {
      setWake(v);
      toast(v ? `Скажи «${ayaName}, …» — я услышу` : "Больше не слушаю имя");
    }
    menuFocusLabel();
  } catch {
    toast("Ядро не отвечает");
  }
}

/* ═════════════════════════ «кинь файл на Аю» ═════════════════════════ */

const dropHint = $("drop-hint");

async function initDrop() {
  try {
    const { getCurrentWebview } = await import("@tauri-apps/api/webview");
    await getCurrentWebview().onDragDropEvent((e) => {
      const p = e.payload;
      const dpr = window.devicePixelRatio || 1;
      if (p.type === "enter" || p.type === "over") {
        dropHint.classList.remove("hidden");
        setDragHover({ x: p.position.x / dpr, y: p.position.y / dpr });
      } else if (p.type === "leave") {
        dropHint.classList.add("hidden");
      } else if (p.type === "drop") {
        dropHint.classList.add("hidden");
        void onFilesDropped(p.paths);
      }
    });
  } catch {
    /* вне приложения перетаскивание файлов недоступно */
  }
}

async function onFilesDropped(paths: string[]) {
  if (!paths.length) return;
  if (busy) {
    toast("Секунду, я ещё отвечаю");
    return;
  }
  // вопрос можно написать в чат заранее: «переведи», «что тут не так?»
  const question = input.value.trim();
  input.value = "";
  autosize();
  playReaction("hello", "surprise");
  const names = paths.map((x) => x.split(/[\\/]/).pop()).join(", ");
  addMsg(`${question ? question + "\n" : ""}📎 ${names}`, "user files");
  await sendToAya(question, false, paths);
}

/* ═════════════════════════ прогулки ═════════════════════════ */

void initDesktopWalk({
  enabled: () => walkOn,
  canMove: () =>
    !gameOn &&
    !panelOpen &&
    !busy &&
    !speakPlaying &&
    !(rec && rec.state === "recording") &&
    menu.classList.contains("hidden") &&
    !pendingId,
  onWalk: (dir) => setWalking(dir),
  onLand: (kind) => {
    if (kind === "window" && Math.random() < 0.4) playReaction("hello", "joyful");
  },
});

/* ═════════════════════════ трей ═════════════════════════ */

// Клики по чекбоксу в трее могут прийти быстрее, чем завершится POST.
// Очередь на каждый id сохраняет оба клика, а не теряет второй.
const trayQueue: Record<TrayCheck, Promise<void>> = {
  followup: Promise.resolve(),
  focus: Promise.resolve(),
};

function queueTray(id: TrayCheck, task: () => Promise<void>): void {
  trayQueue[id] = trayQueue[id].then(task, task).catch(() => {});
}

void onTrayToggle("followup", () =>
  queueTray("followup", async () => {
    try {
      const cfg = (await (await fetch(`${CORE_URL}/api/config`)).json()) as { followup?: boolean };
      const next = !cfg.followup;
      const res = await fetch(`${CORE_URL}/api/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ followup: next }),
      });
      if (!res.ok) throw new Error(String(res.status));
      followupOn = next;
      if (!next) cancelFollowup(true);
      if (await setTrayCheck("followup", next)) lastTrayFollowup = next;
    } catch {
      await setTrayCheck("followup", followupOn);
    }
  }),
);

void onTrayToggle("focus", () =>
  queueTray("focus", async () => {
    try {
      const p = (await (await fetch(`${CORE_URL}/api/proactive`)).json()) as { focus?: boolean };
      const next = !p.focus;
      const res = await fetch(`${CORE_URL}/api/proactive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ focus: next }),
      });
      if (!res.ok) throw new Error(String(res.status));
      focusOn = next;
      menuFocusLabel();
      if (await setTrayCheck("focus", next)) lastTrayFocus = next;
    } catch {
      await setTrayCheck("focus", focusOn);
    }
  }),
);

/* ═════════════════════════ старт ═════════════════════════ */

// наведение на силуэт подсвечивает док
window.addEventListener("pointermove", (e) => {
  const solid = (e.target as HTMLElement)?.tagName === "CANVAS" && (e.target as HTMLElement).style.cursor !== "default";
  document.body.classList.toggle("hovering", solid);
});

void initWindowState();
void initAvatar($("stage")).then(() => void refreshState());
// показали из трея/хоткеем — сразу снова рисуем, не ждём опроса
void (async () => {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().onFocusChanged(() => void syncRender());
    // консоль сменила модель — перезагружаем окно, чтобы Ая «переоделась»
    const { listen } = await import("@tauri-apps/api/event");
    await listen<{ key: HotkeyKey; accel: string }>("aya-hotkeys", async (ev) => {
      const { key, accel } = ev.payload;
      const r = await changeHotkey(key, accel);
      if (r.ok) await postJson("/api/config", { [key]: accel }).catch(() => {});
      const { emitTo } = await import("@tauri-apps/api/event");
      await emitTo("console", "aya-hotkeys-result", { key, accel, ...r });
    });
    await listen("aya-setup-done", async () => {
      await showSelf();
      await refreshState();
      void speakOnce("Привет! Я Ая. Зови меня, когда захочешь поговорить.", "joyful");
    });
    await listen("aya-reload", () => {
      if (!busy && !speakPlaying) location.reload();
    });
  } catch {}
})();
void initHoverPass(clickThroughOn, () => gameOn);
void initDrop();
void bootState();
startUpdateChecks((line) => showBubble(line, { holdMs: 8000 }));
