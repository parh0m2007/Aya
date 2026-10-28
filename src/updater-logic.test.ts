import { describe, expect, it } from "vitest";
import { newer, updateLine } from "./updater-logic";

describe("обновления", () => {
  it("предлагает только более новую версию", () => {
    expect(updateLine("1.0.0", "1.1.0")).toBe("Доступна версия 1.1.0 — обновить?");
    expect(updateLine("1.0.0", "1.0.0")).toBeNull();
    expect(updateLine("1.2.0", "1.1.9")).toBeNull();
  });

  it("сравнивает по числам, а не по строке", () => {
    expect(newer("1.0.10", "1.0.9")).toBe(true);
    expect(updateLine("1.0.9", "1.0.10")).toBe("Доступна версия 1.0.10 — обновить?");
    expect(updateLine("1.0.0", "v1.0.1")).toBe("Доступна версия 1.0.1 — обновить?");
  });
});
