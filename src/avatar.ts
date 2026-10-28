import * as PIXI from "pixi.js";
import { Live2DModel, config as live2dConfig } from "pixi-live2d-display/cubism4";
import { cachedModelFile, canReadDisk, diskModelUrl, loadModelConfig, modelUrlsFor, rememberModelFile } from "./model-source";
import { startWindowDrag } from "./window-drag";
import {
  PressGesture,
  RubDetector,
  approach,
  clampScale,
  gazeFromCursor,
  moodParams,
  mouthLevel,
  pickExpression,
  pickMotionGroup,
  zoneOf,
  type Mood,
  type Zone,
} from "./avatar-logic";

Live2DModel.registerTicker(PIXI.Ticker);
// у демо-моделей (Haru) движения «тап» проигрывают встроенные японские фразы —
// они накладывались бы на голос Аи; свой голос у неё и так есть
live2dConfig.sound = false;

/* ───────────────────────── состояние сцены ───────────────────────── */

interface Scene {
  app: PIXI.Application;
  model: Live2DModel | null;
  placeholder: PIXI.Graphics | null;
  t: number;
}

let scene: Scene | null = null;
let panelOpen = false;
let scale = 1;
let reactionsOn = true;
let idleLifeOn = true;

// эмоции и состояние «лица»
let mood: Mood = "calm";
let moodUntil = 0;
const overlay: Record<string, number> = {}; // текущие (сглаженные) добавки параметров
let blush = 0; // румянец после поглаживания
let blushTarget = 0;
let thinking = false;
let listening = false;
let walking = 0; // -1 влево, 1 вправо, 0 стоит
let walkT = 0;

// губы
let analyser: AnalyserNode | null = null;
let analyserBuf: Uint8Array | null = null;
let mouthFallback = false; // аудио без анализатора — синусоида
let mouth = 0;
let mouthT = 0;

// взгляд
let gazeTarget: { x: number; y: number } | null = null;
let gazeUntil = 0;
let lastCursorMove = performance.now();

// «жизнь» в простое
let lastInteraction = performance.now();
let nextIdleAct = performance.now() + 30_000;
let sleepy = 0; // 0 бодрая … 1 дремлет

/* ───────────────────────── модель ───────────────────────── */

async function modelUrls(): Promise<string[]> {
  const cfg = await loadModelConfig();
  if (cfg) rememberModelFile(cfg.live2d_model_file);
  return modelUrlsFor(cfg);
}

/** Модель с диска сразу (без ядра), если знаем, какую показывали в прошлый раз. */
function fastModelUrls(): { urls: string[]; file: string | null } {
  const file = canReadDisk() ? cachedModelFile() : null;
  return { urls: file ? [diskModelUrl(file)] : [], file };
}

/** Ядро поднялось — если модель сменили, пока Ая была закрыта, перезагружаемся. */
function checkModelChanged(shown: string) {
  void loadModelConfig().then((cfg) => {
    if (!cfg) return;
    const want = cfg.live2d_model_file || "";
    rememberModelFile(want);
    if (want && want !== shown) location.reload();
  });
}

function baseX(): number {
  if (!scene) return 0;
  return scene.app.screen.width * (panelOpen ? 0.74 : 0.5);
}

function baseY(): number {
  return scene ? scene.app.screen.height + 10 : 0;
}

function layoutModel() {
  const s = scene;
  if (!s?.model) return;
  const m = s.model;
  const fit = Math.min(
    (s.app.screen.height * 0.94) / (m.height / m.scale.y),
    (s.app.screen.width * 0.95) / (m.width / m.scale.x),
  );
  m.scale.set(fit * scale);
  m.x = baseX();
  notifyAnchor();
}

function notifyAnchor() {
  const s = scene;
  if (!s) return;
  const m = s.model;
  const top = m ? m.y - m.height : s.app.screen.height * 0.4;
  window.dispatchEvent(
    new CustomEvent("aya-anchor", {
      detail: { x: baseX(), y: m?.y ?? 0, h: s.app.screen.height, top: Math.max(0, top), w: m?.width ?? 0 },
    }),
  );
}

function drawPlaceholder(app: PIXI.Application): PIXI.Graphics {
  const g = new PIXI.Graphics();
  const cx = app.screen.width / 2;
  const cy = app.screen.height * 0.62;
  g.beginFill(0x3a2f55);
  g.drawEllipse(cx, cy + 130, 95, 120);
  g.beginFill(0xffe9f0);
  g.drawCircle(cx, cy - 40, 58);
  g.beginFill(0x4a3f6b);
  g.drawEllipse(cx, cy - 88, 66, 42);
  g.beginFill(0x2b2440);
  g.drawCircle(cx - 20, cy - 40, 6);
  g.drawCircle(cx + 20, cy - 40, 6);
  g.lineStyle(3, 0x2b2440);
  g.arc(cx, cy - 28, 14, 0.15, Math.PI - 0.15);
  g.endFill();
  return g;
}

/* ───────────────────────── попадание по модели ───────────────────────── */

const tmpPoint = new PIXI.Point();

/** Точное попадание: по рамкам видимых частей модели, а не по общему
 *  прямоугольнику — чтобы клики мимо силуэта проходили на рабочий стол. */
export function modelHit(x: number, y: number): { zone: Zone; yRatio: number } | null {
  const m = scene?.model as any;
  if (!m) return null;
  const b = m.getBounds();
  if (!b.contains(x, y)) return null;
  const im = m.internalModel;
  const yRatio = (y - b.y) / Math.max(1, b.height);
  try {
    tmpPoint.set(x, y);
    const p = m.toModelPosition(tmpPoint, tmpPoint);
    const core = im.coreModel;
    const n: number = im.getDrawableIDs().length;
    let hit = false;
    for (let i = 0; i < n && !hit; i++) {
      if (core.getDrawableOpacity && core.getDrawableOpacity(i) < 0.15) continue;
      const r = im.getDrawableBounds(i);
      // крупные полупрозрачные фоны/эффекты не считаем силуэтом
      if (r.width > im.originalWidth * 0.9 && r.height > im.originalHeight * 0.9) continue;
      if (p.x >= r.x && p.x <= r.x + r.width && p.y >= r.y && p.y <= r.y + r.height) hit = true;
    }
    if (!hit) return null;
    const names: string[] = m.hitTest(x, y) || [];
    return { zone: zoneOf(yRatio, names), yRatio };
  } catch {
    // нет доступа к drawables — урезанный прямоугольник по центру
    const cx = b.x + b.width / 2;
    if (Math.abs(x - cx) > b.width * 0.36) return null;
    return { zone: zoneOf(yRatio), yRatio };
  }
}

/** Где голова модели (для взгляда и пузыря реплик). */
export function headPoint(): { x: number; y: number } {
  const m = scene?.model;
  if (!m) return { x: window.innerWidth / 2, y: window.innerHeight * 0.3 };
  const b = m.getBounds();
  return { x: b.x + b.width / 2, y: b.y + b.height * 0.16 };
}

/* ───────────────────────── инициализация ───────────────────────── */

export async function initAvatar(container: HTMLElement): Promise<void> {
  const app = new PIXI.Application({
    backgroundAlpha: 0,
    resizeTo: container,
    autoDensity: true,
    // DPR выше 1.5 почти не виден на прозрачном окне, а GPU ест квадратично
    resolution: Math.min(window.devicePixelRatio || 1, 1.5),
    antialias: true,
    powerPreference: "low-power",
  });
  app.ticker.maxFPS = 30;
  container.appendChild(app.view as HTMLCanvasElement);

  const placeholder = drawPlaceholder(app);
  scene = { app, model: null, placeholder, t: 0 };
  app.stage.addChild(placeholder);
  app.ticker.add((dt) => frame(dt));
  bindPointer(app.view as HTMLCanvasElement);
  bindVisibility();

  const fast = fastModelUrls();
  const loaded = await loadFirst(app, placeholder, fast.urls);
  if (loaded && fast.file) checkModelChanged(fast.file);
  if (!loaded) await loadFirst(app, placeholder, await modelUrls());
}

async function loadFirst(app: PIXI.Application, placeholder: PIXI.Container, urls: string[]): Promise<boolean> {
  if (!scene) return false;
  for (const url of urls) {
    try {
      const model = await Live2DModel.from(url, { autoInteract: false });
      model.anchor.set(0.5, 1);
      model.y = baseY();
      placeholder.destroy();
      scene.placeholder = null;
      scene.model = model;
      app.stage.addChild(model);
      layoutModel();
      (model.internalModel as any).on("beforeModelUpdate", applyFaceLayer);
      window.__AYA_DEBUG = { modelLoaded: true, url };
      window.dispatchEvent(new CustomEvent("aya-model-ready"));
      return true;
    } catch (err) {
      console.warn("Live2D model failed:", url, err);
      window.__AYA_DEBUG = { modelLoaded: false, url, error: String(err) };
    }
  }
  return false;
}

/* ───────────────────────── указатель: клик / drag / колесо ───────────────────────── */

const press = new PressGesture();
const rub = new RubDetector();
let lastTap = 0;

function bindPointer(canvas: HTMLCanvasElement) {
  canvas.style.touchAction = "none";
  canvas.addEventListener("pointerdown", (e) => {
    if (!e.isPrimary) return;
    const hit = modelHit(e.clientX, e.clientY);
    if (!hit) return;
    poke();
    if (e.button === 2) return; // контекстное меню — ниже
    if (e.button !== 0) return;
    e.preventDefault();
    press.down(e.clientX, e.clientY, performance.now());
  });
  canvas.addEventListener("pointermove", (e) => {
    lastCursorMove = performance.now();
    if (press.active) {
      if (press.move(e.clientX, e.clientY)) void startWindowDrag(); // кнопка ещё зажата — ОС потащит окно
      return;
    }
    const hit = modelHit(e.clientX, e.clientY);
    canvas.style.cursor = hit ? (hit.zone === "head" ? "grab" : "pointer") : "default";
    if (hit?.zone === "head" && reactionsOn && rub.feed(e.clientX, performance.now())) {
      emit("aya-rub", {});
    } else if (!hit) rub.reset();
  });
  const finish = (e: PointerEvent) => {
    const kind = press.up(performance.now());
    if (kind === "none") return;
    const hit = modelHit(e.clientX, e.clientY);
    if (!hit) return;
    const now = performance.now();
    if (now - lastTap < 320) {
      lastTap = 0;
      emit("aya-dblclick", {});
      return;
    }
    lastTap = now;
    const zone = hit.zone;
    // ждём, не будет ли второго клика: двойной открывает чат, одиночный — реакция
    window.setTimeout(() => {
      if (lastTap === now) emit("aya-tap", { zone, long: kind === "long", x: e.clientX, y: e.clientY });
    }, 330);
  };
  canvas.addEventListener("pointerup", finish);
  canvas.addEventListener("pointercancel", () => press.up(performance.now()));
  canvas.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (modelHit(e.clientX, e.clientY)) emit("aya-menu", { x: e.clientX, y: e.clientY });
  });
  canvas.addEventListener(
    "wheel",
    (e) => {
      if (!modelHit(e.clientX, e.clientY) || !e.ctrlKey) return;
      e.preventDefault();
      setAvatarScale(scale * (e.deltaY < 0 ? 1.05 : 0.95));
      emit("aya-scale", { scale });
    },
    { passive: false },
  );
}

function emit(name: string, detail: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

/** Любое взаимодействие будит Аю и сбрасывает таймер простоя. */
export function poke() {
  const now = performance.now();
  if (sleepy > 0.6 && now - lastInteraction > 60_000) emit("aya-wake", {});
  lastInteraction = now;
  nextIdleAct = Math.max(nextIdleAct, now + 15_000);
}

/* ───────────────────────── видимость и FPS ───────────────────────── */

let fpsWanted = 30;
let hidden = false;

function bindVisibility() {
  document.addEventListener("visibilitychange", () => setHidden(document.visibilityState === "hidden"));
}

/** Окно скрыто/свёрнуто — не рисуем вовсе (раньше рендер шёл и в трее). */
export function setHidden(h: boolean) {
  hidden = h;
  const s = scene;
  if (!s) return;
  if (h) s.app.ticker.stop();
  else {
    s.app.ticker.maxFPS = fpsWanted;
    s.app.ticker.start();
  }
}

export function setRenderFps(fps: number) {
  fpsWanted = fps;
  const s = scene;
  if (s && !hidden) s.app.ticker.maxFPS = fps;
}

/* ───────────────────────── кадр ───────────────────────── */

let lastFrame = performance.now();

function frame(dt: number) {
  const s = scene;
  if (!s) return;
  const now = performance.now();
  const ms = Math.min(100, now - lastFrame);
  lastFrame = now;
  s.t += dt * 0.03;
  if (s.placeholder) {
    s.placeholder.y = Math.sin(s.t) * 5;
    return;
  }
  const m = s.model as any;
  if (!m) return;
  // лёгкое «дыхание» всего тела, при ходьбе — шаги
  if (walking) walkT += ms;
  const step = walking ? Math.abs(Math.sin(walkT * 0.011)) * 7 : 0;
  m.y = baseY() + Math.sin(s.t) * 3 * (1 - sleepy * 0.5) - step;

  // настроение остывает до спокойного
  if (mood !== "calm" && now > moodUntil) setMoodInternal("calm");

  // простой без речи и касаний — 20 кадров/с вместо 30: дыхание медленное, GPU отдыхает
  const calmIdle = now - lastInteraction > 60_000 && mouth < 0.02 && !thinking && !listening;
  const fps = calmIdle ? Math.min(20, fpsWanted) : fpsWanted;
  if (s.app.ticker.maxFPS !== fps) s.app.ticker.maxFPS = fps;

  // взгляд: курсор, заданная точка или «оглядывание» в простое
  const idleFor = now - lastInteraction;
  if (gazeTarget && now < gazeUntil) m.focus(gazeTarget.x, gazeTarget.y);
  else if (thinking) focusNorm(m, -0.5, 0.55);

  // сонливость в простое
  const sleepTarget = idleLifeOn && !listening && !thinking && mouth < 0.05
    ? (idleFor > 600_000 ? 0.9 : idleFor > 240_000 ? 0.5 : 0)
    : 0;
  sleepy = approach(sleepy, sleepTarget, ms, sleepTarget > sleepy ? 4000 : 250);

  // жизнь в простое: редкие движения и оглядывания
  if (idleLifeOn && now > nextIdleAct && !thinking && !listening && mouth < 0.05) {
    nextIdleAct = now + 25_000 + Math.random() * 45_000;
    idleAct(m, idleFor);
  }

  // губы: настоящий уровень звука, иначе синусоида
  let target = 0;
  if (analyser && analyserBuf) {
    analyser.getByteTimeDomainData(analyserBuf as any);
    target = mouthLevel(analyserBuf);
  } else if (mouthFallback) {
    mouthT += ms * 0.012;
    target = 0.25 + Math.abs(Math.sin(mouthT)) * 0.55;
  }
  mouth = approach(mouth, target, ms, target > mouth ? 35 : 70);
  blush = approach(blush, blushTarget, ms, 400);
  if (blushTarget > 0 && now > blushUntil) blushTarget = 0;

  // сглаживаем надстройку лица к цели настроения
  const want: Record<string, number> = { ...(hasExpressions(m) ? {} : moodParams(mood)) };
  if (walking) {
    want.ParamBodyAngleZ = (want.ParamBodyAngleZ ?? 0) + Math.sin(walkT * 0.011) * 5;
    want.ParamAngleX = (want.ParamAngleX ?? 0) + walking * 12; // смотрит, куда идёт
    want.ParamBodyAngleX = (want.ParamBodyAngleX ?? 0) + walking * 4;
  }
  if (listening) {
    want.ParamAngleZ = (want.ParamAngleZ ?? 0) + 6;
    want.ParamEyeLOpen = (want.ParamEyeLOpen ?? 0) + 0.15;
    want.ParamEyeROpen = (want.ParamEyeROpen ?? 0) + 0.15;
  }
  const keys = new Set([...Object.keys(overlay), ...Object.keys(want)]);
  for (const k of keys) overlay[k] = approach(overlay[k] ?? 0, want[k] ?? 0, ms, 160);
}

function focusNorm(m: any, x: number, y: number) {
  // focus() ждёт мировые координаты: переводим из [-1,1] относительно головы
  const h = headPoint();
  m.focus(h.x + x * 400, h.y - y * 400);
}

function idleAct(m: any, idleFor: number) {
  const groups = Object.keys(m.internalModel?.motionManager?.definitions ?? {});
  const r = Math.random();
  if (r < 0.45) {
    const g = pickMotionGroup("idle", groups);
    if (g) m.motion(g, undefined, 1).catch(() => {});
  } else if (r < 0.75 || idleFor > 240_000) {
    // оглядывается по сторонам пару секунд
    const h = headPoint();
    const x = (Math.random() - 0.5) * 1.6;
    const y = (Math.random() - 0.3) * 0.8;
    lookAt(h.x + x * 400, h.y - y * 400, 2200);
  } else if (sleepy < 0.3) {
    const g = pickMotionGroup("flick", groups) ?? pickMotionGroup("tap", groups);
    if (g) m.motion(g, undefined, 1).catch(() => {});
  }
}

/** Надстройка параметров каждый кадр — после движений и физики, до отрисовки. */
function applyFaceLayer() {
  const m = scene?.model as any;
  const core = m?.internalModel?.coreModel;
  if (!core) return;
  for (const [id, v] of Object.entries(overlay)) {
    if (Math.abs(v) > 0.001) core.addParameterValueById?.(id, v);
  }
  if (sleepy > 0.02) {
    const k = 1 - sleepy * 0.85;
    for (const id of ["ParamEyeLOpen", "ParamEyeROpen"]) {
      const v = core.getParameterValueById?.(id);
      if (typeof v === "number") core.setParameterValueById(id, v * k);
    }
  }
  if (blush > 0.01) core.addParameterValueById?.("ParamCheek", blush);
  if (mouth > 0.01) {
    const cur = core.getParameterValueById?.("ParamMouthOpenY") ?? 0;
    core.setParameterValueById?.("ParamMouthOpenY", Math.max(cur, mouth));
  }
}

function hasExpressions(m: any): boolean {
  return !!m?.internalModel?.motionManager?.expressionManager?.definitions?.length;
}

/* ───────────────────────── публичное API ───────────────────────── */

export function setPanelOpen(open: boolean) {
  panelOpen = open;
  layoutModel();
}

let resizeTimer: ReturnType<typeof setTimeout> | null = null;
window.addEventListener("resize", () => {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(layoutModel, 60);
});

export function setAvatarScale(v: number) {
  scale = clampScale(v);
  layoutModel();
}

export function getAvatarScale(): number {
  return scale;
}

export function applyAvatarSettings(o: { scale?: number; reactions?: boolean; idleLife?: boolean }) {
  if (typeof o.scale === "number" && clampScale(o.scale) !== scale) setAvatarScale(o.scale);
  if (typeof o.reactions === "boolean") reactionsOn = o.reactions;
  if (typeof o.idleLife === "boolean") idleLifeOn = o.idleLife;
}

function setMoodInternal(next: Mood) {
  mood = next;
  const m = scene?.model as any;
  const defs = m?.internalModel?.motionManager?.expressionManager?.definitions ?? [];
  const names: string[] = defs.map((d: any) => d.Name ?? d.name).filter(Boolean);
  const ex = pickExpression(next, names);
  if (ex) m.expression(ex).catch(() => {});
}

/** Настроение реплики: выражение/мимика держится ~25 с и остывает. */
export function setMood(next: string, holdMs = 25_000) {
  const valid: Mood[] = ["calm", "joyful", "thoughtful", "sad", "surprise"];
  const mm = (valid.includes(next as Mood) ? next : "calm") as Mood;
  moodUntil = performance.now() + holdMs;
  setMoodInternal(mm);
}

/** Прогулка по экрану: шаги и взгляд по направлению движения. */
export function setWalking(dir: number) {
  walking = Math.sign(dir);
  if (walking) poke();
}

/** Над Аей держат файл: удивлённо смотрит на него. */
export function setDragHover(at: { x: number; y: number } | null) {
  if (!at) return;
  poke();
  if (mood !== "surprise") setMood("surprise", 3000);
  lookAt(at.x, at.y, 800);
}

/** Совместимость со старым кодом. */
export const setExpression = (m: string) => setMood(m);

/** Ждём ответ модели: задумчиво смотрит вверх. */
export function setThinking(on: boolean) {
  thinking = on;
  if (on) poke();
}

/** Слушает микрофон: чуть наклоняет голову. */
export function setListening(on: boolean) {
  listening = on;
  if (on) poke();
}

export function startTalking() {
  setThinking(true);
}

export function stopTalking() {
  setThinking(false);
}

/** Подключить анализатор звука озвучки — губы двигаются по реальной громкости. */
export function attachLipSync(node: AnalyserNode | null) {
  analyser = node;
  analyserBuf = node ? new Uint8Array(node.fftSize) : null;
}

export function startMouth() {
  if (!analyser) mouthFallback = true;
  poke();
}

export function stopMouth() {
  mouthFallback = false;
}

/** Смотреть в точку окна (CSS px) некоторое время. */
export function lookAt(x: number, y: number, ms = 1500) {
  const m = scene?.model;
  if (!m) return;
  gazeTarget = { x, y };
  gazeUntil = performance.now() + ms;
}

/** Курсор (CSS px окна; может быть за его пределами) — следим взглядом. */
export function followCursor(x: number, y: number, moved: boolean) {
  const m = scene?.model as any;
  if (!m) return;
  const now = performance.now();
  if (moved) {
    lastCursorMove = now;
    poke(); // пользователь за компьютером — не засыпаем
  }
  if (gazeTarget && now < gazeUntil) return;
  if (thinking) return;
  // давно не двигали мышь — взгляд мягко возвращается вперёд
  if (now - lastCursorMove > 8000) {
    const h = headPoint();
    m.focus(h.x, h.y + 40);
    return;
  }
  const h = headPoint();
  const g = gazeFromCursor(x, y, h.x, h.y);
  focusNorm(m, g.x, g.y);
}

let blushUntil = 0;

/** Реакция на касание: движение, выражение, румянец. */
export function playReaction(kind: "pat" | "poke" | "hello" | "wake", reactionMood?: string) {
  const m = scene?.model as any;
  poke();
  if (!m) return;
  const groups = Object.keys(m.internalModel?.motionManager?.definitions ?? {});
  if (kind === "pat") {
    blushTarget = 0.9;
    blushUntil = performance.now() + 4000;
    setMood(reactionMood || "joyful", 6000);
    const h = headPoint();
    lookAt(h.x, h.y + 260, 1800); // прикрывает глаза, смотрит вниз
  } else if (kind === "poke") {
    setMood(reactionMood || "surprise", 4000);
    const g = pickMotionGroup("tap", groups) ?? pickMotionGroup("flick", groups);
    if (g) m.motion(g, undefined, 3).catch(() => {});
  } else {
    setMood(reactionMood || "joyful", 5000);
    const g = pickMotionGroup("tap", groups) ?? pickMotionGroup("idle", groups);
    if (g) m.motion(g, undefined, 2).catch(() => {});
  }
}

declare global {
  interface Window {
    __AYA_DEBUG?: { modelLoaded: boolean; url: string; error?: string };
  }
}
