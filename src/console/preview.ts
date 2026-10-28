/* Live2D-превью в консоли: один PIXI-рендерер на всё окно.
 *
 * Раньше каждое открытие вкладки «Персонаж» создавало новый WebGL-контекст;
 * браузер держит их ограниченное число, и после нескольких переключений
 * превью переставало рисоваться. Теперь холст переносится между контейнерами,
 * а меняется только модель. */
import { CORE } from "./ui";

type Pixi = typeof import("pixi.js");

let PIXI: Pixi | null = null;
let app: import("pixi.js").Application | null = null;
let model: any = null;
let currentFile = "";
let loadSeq = 0;
let t = 0;

async function ensureApp(): Promise<import("pixi.js").Application> {
  if (app) return app;
  PIXI = await import("pixi.js");
  const { Live2DModel } = await import("pixi-live2d-display/cubism4");
  Live2DModel.registerTicker(PIXI.Ticker);
  app = new PIXI.Application({ backgroundAlpha: 0, width: 300, height: 400, autoDensity: true, antialias: true });
  app.ticker.maxFPS = 24;
  app.ticker.add((dt) => {
    if (!model || !app) return;
    t += dt * 0.03;
    model.y = app.screen.height + 5 + Math.sin(t) * 4;
  });
  return app;
}

function fit() {
  if (!app || !model) return;
  const s = Math.min((app.screen.height * 0.92) / (model.height / model.scale.y), (app.screen.width * 0.85) / (model.width / model.scale.x));
  model.scale.set(s);
  model.x = app.screen.width / 2;
  model.y = app.screen.height + 5;
}

/** Показать модель `file` (путь относительно /models) в контейнере. */
let observer: ResizeObserver | null = null;

/** Страница строится до вставки в документ — ждём, пока контейнер появится и получит размер. */
async function whenSized(container: HTMLElement, seq: number): Promise<boolean> {
  for (let i = 0; i < 120; i++) {
    if (seq !== loadSeq) return false;
    if (container.isConnected && container.clientWidth > 0) return true;
    await new Promise((r) => requestAnimationFrame(r));
  }
  return container.isConnected;
}

export async function showPreview(container: HTMLElement, file: string): Promise<void> {
  const seq = ++loadSeq;
  const a = await ensureApp();
  if (!(await whenSized(container, seq))) return;
  const view = a.view as HTMLCanvasElement;
  if (view.parentElement !== container) {
    container.textContent = "";
    container.appendChild(view);
  }
  const resize = () => {
    a.renderer.resize(Math.max(120, container.clientWidth), Math.max(160, container.clientHeight));
    fit();
  };
  observer?.disconnect();
  observer = new ResizeObserver(resize);
  observer.observe(container);
  resize();
  a.start();
  if (file === currentFile && model) {
    fit();
    return;
  }
  container.classList.add("loading");
  try {
    const { Live2DModel } = await import("pixi-live2d-display/cubism4");
    const next = await Live2DModel.from(`${CORE}/models/${file}`, { autoInteract: false });
    if (seq !== loadSeq) {
      next.destroy();
      return;
    }
    if (model) {
      a.stage.removeChild(model);
      model.destroy();
    }
    model = next;
    currentFile = file;
    model.anchor.set(0.5, 1);
    a.stage.addChild(model);
    fit();
  } catch {
    if (seq === loadSeq) container.insertAdjacentHTML("beforeend", '<div class="preview-err">модель не отрисовалась</div>');
  } finally {
    container.classList.remove("loading");
  }
}

/** Скрыли страницу с превью — не тратим GPU. */
export function pausePreview() {
  app?.stop();
}

/** Короткая анимация «привет» в превью. */
export function wavePreview() {
  if (!model) return;
  const groups = Object.keys(model.internalModel?.motionManager?.definitions ?? {});
  const g = groups.find((x) => /^tap/i.test(x)) ?? groups.find((x) => /flick/i.test(x));
  if (g) model.motion(g, undefined, 3).catch(() => {});
}
