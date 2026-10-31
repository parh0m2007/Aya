import { describe, expect, it } from "vitest";
import { failText, runGuarded } from "./guard";

describe("runGuarded", () => {
  it("ядро не ответило — кнопка говорит об этом, а не молчит", async () => {
    const shown: string[] = [];
    const ok = await runGuarded(async () => {
      throw new TypeError("Failed to fetch");
    }, (m) => shown.push(m));
    expect(ok).toBe(false);
    expect(shown).toEqual(["ядро не отвечает"]);
  });
  it("успех — без сообщений", async () => {
    const shown: string[] = [];
    expect(await runGuarded(async () => {}, (m) => shown.push(m))).toBe(true);
    expect(shown).toEqual([]);
  });
});

describe("failText", () => {
  it("другая ошибка — с текстом", () => {
    expect(failText(new Error("500"))).toBe("не получилось: 500");
  });
});
