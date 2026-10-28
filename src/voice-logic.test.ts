import { describe, expect, it } from "vitest";
import { Endpointer, matchWake, speechLevel, wakeNames } from "./voice-logic";

function run(ep: Endpointer, frames: [number, number][]) {
  const events: string[] = [];
  for (const [level, ms] of frames) {
    for (let t = 0; t < ms; t += 30) {
      const e = ep.feed(level, 30);
      if (e) events.push(e);
    }
  }
  return events;
}

describe("Endpointer", () => {
  it("речь после тишины: start, затем end через ~0.65 с тишины", () => {
    const ep = new Endpointer();
    expect(run(ep, [[0.01, 1000], [0.2, 900], [0.01, 600]])).toEqual(["start"]);
    expect(run(ep, [[0.01, 90]])).toEqual(["end"]);
  });
  it("короткий щелчок — не фраза", () => {
    const ep = new Endpointer();
    expect(run(ep, [[0.01, 600], [0.3, 60], [0.01, 2000]])).toEqual([]);
  });
  it("паузы между словами не рвут фразу", () => {
    const ep = new Endpointer();
    const ev = run(ep, [[0.01, 600], [0.2, 400], [0.01, 300], [0.2, 400], [0.01, 300], [0.2, 300]]);
    expect(ev).toEqual(["start"]);
  });
  it("в шумной комнате порог поднимается", () => {
    const ep = new Endpointer();
    run(ep, [[0.06, 5000]]); // ровный шум 0.06 — не речь
    expect(ep.threshold()).toBeGreaterThan(0.1);
    expect(ep.speaking).toBe(false);
  });
  it("begin(): ждём только конец", () => {
    const ep = new Endpointer();
    ep.begin();
    expect(run(ep, [[0.2, 500], [0.01, 700]])).toEqual(["end"]);
  });
});

describe("speechLevel", () => {
  it("берёт только полосу голоса", () => {
    const bytes = new Uint8Array(256);
    bytes[1] = 255; // гул 94 Гц
    expect(speechLevel(bytes, 48000, 512)).toBe(0);
    bytes.fill(200, 4, 30);
    expect(speechLevel(bytes, 48000, 512)).toBeGreaterThan(0.5);
  });
});

describe("matchWake", () => {
  const names = wakeNames("Ая", "Аюшка");
  it("имя в начале", () => {
    expect(matchWake("Ая, открой калькулятор.", names)).toEqual({ matched: true, rest: "открой калькулятор." });
    expect(matchWake("Эй, Ая! Как дела?", names)).toEqual({ matched: true, rest: "Как дела?" });
    expect(matchWake("Айя, привет", names).matched).toBe(true);
  });
  it("имя в конце", () => {
    expect(matchWake("Который час, Ая?", names)).toEqual({ matched: true, rest: "Который час" });
  });
  it("одно имя — пустой остаток", () => {
    expect(matchWake("Ая.", names)).toEqual({ matched: true, rest: "" });
  });
  it("«а я» и чужая речь не срабатывают", () => {
    expect(matchWake("А я вчера ходил в кино", names).matched).toBe(false);
    expect(matchWake("Включи музыку", names).matched).toBe(false);
    expect(matchWake("", names).matched).toBe(false);
  });
  it("своё имя персоны", () => {
    expect(matchWake("Мику, поставь таймер", wakeNames("Мику", "")).rest).toBe("поставь таймер");
  });
});

describe("dropWhenHidden", () => {
  it("скрытая Ая слушает клавишу, но не ловит фоновую речь", async () => {
    const { dropWhenHidden } = await import("./voice-logic");
    expect(dropWhenHidden("hold", true)).toBe(false);
    expect(dropWhenHidden("auto", true)).toBe(true);
    expect(dropWhenHidden("followup", true)).toBe(true);
    expect(dropWhenHidden("barge", true)).toBe(true);
    expect(dropWhenHidden("auto", false)).toBe(false);
  });
});
