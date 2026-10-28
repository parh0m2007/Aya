/* Мастер первого запуска: чистая логика (шаги, пресеты моделей, тексты ошибок). */

export const STEPS = ["Привет", "Модель", "Голос", "Микрофон", "Кто она", "Готово"] as const;

export type LlmKind = "ollama_cloud" | "ollama_local" | "openai";

export interface LlmPreset {
  type: "ollama" | "openai_compatible";
  base_url: string;
  model: string;
  needsKey: boolean;
}

const PRESETS: Record<LlmKind, LlmPreset> = {
  ollama_cloud: { type: "ollama", base_url: "https://ollama.com", model: "gemma4:31b-cloud", needsKey: true },
  ollama_local: { type: "ollama", base_url: "http://127.0.0.1:11434", model: "", needsKey: false },
  openai: { type: "openai_compatible", base_url: "", model: "", needsKey: true },
};

export function llmPreset(kind: LlmKind): LlmPreset {
  return { ...PRESETS[kind] };
}

const ERRORS: Record<string, string> = {
  key: "Ключ не подошёл — проверь, что скопировал целиком",
  offline: "Нет интернета — проверь подключение",
  url: "По этому адресу никто не отвечает",
  model: "Такой модели нет — выбери из списка",
};

export function errorText(code: string): string {
  return ERRORS[code] ?? "Что-то пошло не так — попробуй ещё раз";
}

/** Список моделей для выбора: выбранная — всегда есть (сервис может звать её иначе). */
export function modelOptions(models: string[], selected: string): string[] {
  if (!selected || models.includes(selected)) return [...models];
  return [selected, ...models];
}

/** Пройден ли мастер. Ядро на первом запуске может просыпаться долго (антивирус
    проверяет свежие файлы) — спрашиваем, пока не ответит. null — так и не ответило. */
export async function setupDone(get: () => Promise<{ done: boolean }>, tries = 120, delayMs = 500): Promise<boolean | null> {
  for (let i = 0; i < tries; i++) {
    try {
      return (await get()).done;
    } catch {
      if (delayMs) await new Promise((ok) => setTimeout(ok, delayMs));
    }
  }
  return null;
}
