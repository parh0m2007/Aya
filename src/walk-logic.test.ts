import { describe, expect, it } from "vitest";
import { pickWalkTarget } from "./walk-logic";

const work = { x: 0, y: 0, w: 1920, h: 1040 };
const self = { x: 1400, y: 260, w: 420, h: 780 };

describe("pickWalkTarget", () => {
  it("садится на верхний край окна, если над ним есть место", () => {
    const fg = { x: 300, y: 900, w: 1000, h: 140 };
    const t = pickWalkTarget({ work, self, fg, feet: 8, rnd: 0.5 });
    expect(t.kind).toBe("window");
    expect(t.y).toBe(900 - 780 + 8);
    expect(t.x + self.w / 2).toBeGreaterThan(fg.x);
    expect(t.x + self.w / 2).toBeLessThan(fg.x + fg.w);
  });
  it("высокое окно (нет места сверху) — идёт на пол", () => {
    const fg = { x: 300, y: 100, w: 1000, h: 800 };
    const t = pickWalkTarget({ work, self, fg, feet: 8, rnd: 0.2 });
    expect(t.kind).toBe("floor");
    expect(t.y).toBe(1040 - 780 + 8);
  });
  it("узкое окно игнорирует, пол всегда в пределах экрана и не на месте", () => {
    const t = pickWalkTarget({ work, self, fg: { x: 10, y: 900, w: 100, h: 50 }, feet: 8, rnd: 0.99 });
    expect(t.kind).toBe("floor");
    expect(t.x).toBeGreaterThanOrEqual(0);
    expect(t.x + self.w).toBeLessThanOrEqual(1920);
    expect(Math.abs(t.x - self.x)).toBeGreaterThanOrEqual(self.w);
  });
});
