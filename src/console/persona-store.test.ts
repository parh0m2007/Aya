import { describe, expect, it } from "vitest";
import { addTag, applyPreset, summary, traitWord, type Profile } from "./persona-store";

const base: Profile = {
  name: "Ая",
  user_name: "",
  role: "companion",
  role_text: "",
  traits: { warmth: 80, humor: 60, directness: 40, curiosity: 65, energy: 55 },
  length: "short",
  address: "ты",
  emoji: false,
  tags: ["тихая"],
  interests: "",
  about: "",
  greeting: "",
  modules: { voice: true, initiative: true, memory: true, actions: false, avatar_reactions: true, idle_life: true },
  configured: false,
};

describe("конструктор персоны", () => {
  it("пресет меняет черты частично и не трогает имя", () => {
    const p = applyPreset(base, { id: "x", title: "", sub: "", patch: { role: "friend", traits: { humor: 90 }, modules: { actions: true } } });
    expect(p.name).toBe("Ая");
    expect(p.role).toBe("friend");
    expect(p.traits.humor).toBe(90);
    expect(p.traits.warmth).toBe(80);
    expect(p.modules.actions).toBe(true);
    expect(p.modules.voice).toBe(true);
    expect(base.traits.humor).toBe(60); // исходник не мутирует
  });
  it("теги без дублей и не больше 8", () => {
    expect(addTag(["а"], " А ")).toEqual(["а"]);
    expect(addTag([], "  ")).toEqual([]);
    const many = Array.from({ length: 8 }, (_, i) => `t${i}`);
    expect(addTag(many, "ещё")).toHaveLength(8);
  });
  it("подписи уровней и сводка", () => {
    // пять ступеней — как в промпте ядра (0–19, 20–39, 40–59, 60–79, 80–100)
    expect(traitWord(90, { lo: "a", hi: "b" })).toBe("очень b");
    expect(traitWord(70, { lo: "a", hi: "b" })).toBe("b");
    expect(traitWord(50, { lo: "a", hi: "b" })).toBe("в меру");
    expect(traitWord(30, { lo: "a", hi: "b" })).toBe("a");
    expect(traitWord(10, { lo: "a", hi: "b" })).toBe("очень a");
    expect(summary(base)).toContain("заботливая");
  });
});
