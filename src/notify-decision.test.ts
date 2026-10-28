import { describe, expect, it } from "vitest";
import { shouldToast, toastBody } from "./notify-decision";

describe("shouldToast", () => {
  it("тост при скрытом окне", () => {
    expect(shouldToast(true, false)).toBe(true);
  });
  it("тост в тишине", () => {
    expect(shouldToast(false, true)).toBe(true);
  });
  it("без тоста, когда Ая видна и тишина выключена", () => {
    expect(shouldToast(false, false)).toBe(false);
  });
  it("тост при обоих условиях", () => {
    expect(shouldToast(true, true)).toBe(true);
  });
});

describe("toastBody", () => {
  it("схлопывает пробелы", () => {
    expect(toastBody("  привет \n мир ")).toBe("привет мир");
  });
  it("обрезает до 240 символов", () => {
    const s = toastBody("я".repeat(250));
    expect(s.length).toBe(240);
    expect(s.endsWith("…")).toBe(true);
  });
  it("короткий текст не трогает", () => {
    expect(toastBody("привет")).toBe("привет");
  });
});
