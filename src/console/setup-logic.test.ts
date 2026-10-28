import { describe, expect, it } from "vitest";
import { STEPS, errorText, llmPreset, modelOptions } from "./setup-logic";

describe("мастер: логика", () => {
  it("шаги по порядку", () => {
    expect(STEPS).toEqual(["Привет", "Модель", "Голос", "Микрофон", "Кто она", "Готово"]);
  });

  it("пресеты моделей", () => {
    expect(llmPreset("ollama_cloud")).toEqual({ type: "ollama", base_url: "https://ollama.com", model: "gemma4:31b-cloud", needsKey: true });
    expect(llmPreset("ollama_local")).toEqual({ type: "ollama", base_url: "http://127.0.0.1:11434", model: "", needsKey: false });
    expect(llmPreset("openai")).toEqual({ type: "openai_compatible", base_url: "", model: "", needsKey: true });
  });

  it("понятные ошибки", () => {
    expect(errorText("key")).toBe("Ключ не подошёл — проверь, что скопировал целиком");
    expect(errorText("offline")).toBe("Нет интернета — проверь подключение");
    expect(errorText("url")).toBe("По этому адресу никто не отвечает");
    expect(errorText("model")).toBe("Такой модели нет — выбери из списка");
    expect(errorText("???")).toBe("Что-то пошло не так — попробуй ещё раз");
  });

  it("выбранная модель есть в списке, даже если сервис назвал её иначе", () => {
    expect(modelOptions(["gemma4:31b", "qwen3"], "gemma4:31b-cloud")).toEqual(["gemma4:31b-cloud", "gemma4:31b", "qwen3"]);
    expect(modelOptions(["a", "b"], "b")).toEqual(["a", "b"]);
    expect(modelOptions([], "")).toEqual([]);
  });
});

describe("консоль ждёт ядро на первом запуске", () => {
  it("спрашивает /api/setup, пока ядро не проснётся", async () => {
    const { setupDone } = await import("./setup-logic");
    let n = 0;
    const get = async () => {
      n += 1;
      if (n < 3) throw new Error("ядро ещё стартует");
      return { done: false };
    };
    expect(await setupDone(get, 10, 0)).toBe(false);
    expect(n).toBe(3);
  });

  it("не дождались — null, а не «пройден»", async () => {
    const { setupDone } = await import("./setup-logic");
    expect(await setupDone(async () => { throw new Error("нет"); }, 3, 0)).toBeNull();
  });
});
