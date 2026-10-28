import { CORE_URL } from "./persona";

export interface ModelConfig {
  live2d_model_file?: string;
}

const BUNDLED_HARU = "models/haru/Haru.model3.json";
const CACHE_KEY = "aya.modelFile";
/** Свой протокол приложения: модели прямо из ~/.aya/models, без ядра (см. models_proto.rs). */
export const DISK_MODELS = "http://ayamodels.localhost";

/** Безопасный путь модели: «папка/файл.model3.json», без «..» и дисков. */
export function safeModelFile(file: unknown): string | null {
  const f = typeof file === "string" ? file.trim() : "";
  if (!f || !/\.model3\.json$/i.test(f) || f.includes("\\") || f.includes(":")) return null;
  const parts = f.split("/");
  return parts.every((p) => p && p !== "." && p !== "..") ? f : null;
}

export function diskModelUrl(file: string, base = DISK_MODELS): string {
  return `${base}/${file.split("/").map(encodeURIComponent).join("/")}`;
}

/** Какую модель показывали в прошлый раз — чтобы на старте не ждать ядро. */
export function cachedModelFile(store: Pick<Storage, "getItem"> | null = safeStorage()): string | null {
  try {
    return safeModelFile(store?.getItem(CACHE_KEY));
  } catch {
    return null;
  }
}

export function rememberModelFile(file: string | null | undefined, store: Pick<Storage, "setItem" | "removeItem"> | null = safeStorage()) {
  try {
    const f = safeModelFile(file);
    if (f) store?.setItem(CACHE_KEY, f);
    else store?.removeItem(CACHE_KEY);
  } catch {
    /* нет хранилища — просто будем ждать ядро */
  }
}

function safeStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** В приложении (Tauri) — да; в обычном браузере (npm run dev) протокола нет. */
export function canReadDisk(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function modelUrlsFor(config: ModelConfig | null, coreUrl = CORE_URL): string[] {
  const coreHaru = `${coreUrl}/models/haru/Haru.model3.json`;
  const selected = config?.live2d_model_file;
  return selected
    ? [`${coreUrl}/models/${selected}`, coreHaru, BUNDLED_HARU]
    : [coreHaru, BUNDLED_HARU];
}

export async function loadModelConfig(
  fetchFn: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> = fetch,
  timeoutMs = 45_000, // ядро теперь стартует вместе с приложением — ждём дольше
  intervalMs = 250,
  coreUrl = CORE_URL,
): Promise<ModelConfig | null> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const response = await fetchFn(`${coreUrl}/api/config`);
      if (response.ok) return (await response.json()) as ModelConfig;
    } catch {
      /* ядро ещё запускается */
    }
    if (Date.now() >= deadline) return null;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
