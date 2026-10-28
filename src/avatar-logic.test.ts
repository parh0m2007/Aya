import { describe, expect, it } from "vitest";
import {
  PressGesture,
  RubDetector,
  approach,
  clampScale,
  gazeFromCursor,
  moodParams,
  mouthLevel,
  pickExpression,
  pickMotionGroup,
  zoneOf,
} from "./avatar-logic";

describe("PressGesture", () => {
  it("короткое нажатие без движения — tap", () => {
    const g = new PressGesture();
    g.down(10, 10, 0);
    expect(g.move(12, 11)).toBe(false);
    expect(g.up(120)).toBe("tap");
  });
  it("движение за порог — drag, а не клик", () => {
    const g = new PressGesture();
    g.down(10, 10, 0);
    expect(g.move(20, 10)).toBe(true);
    expect(g.move(40, 10)).toBe(false); // drag начинается один раз
    expect(g.up(100)).toBe("none");
  });
  it("долгое удержание — long", () => {
    const g = new PressGesture();
    g.down(0, 0, 0);
    expect(g.up(800)).toBe("long");
  });
});

describe("RubDetector", () => {
  it("туда-сюда над головой засчитывается", () => {
    const r = new RubDetector(4, 2000, 5);
    const xs = [0, 20, 0, 20, 0, 20];
    const hits = xs.map((x, i) => r.feed(x, i * 100));
    expect(hits.includes(true)).toBe(true);
  });
  it("медленное движение в одну сторону — нет", () => {
    const r = new RubDetector();
    expect([0, 10, 20, 30, 40, 50].some((x, i) => r.feed(x, i * 100))).toBe(false);
  });
});

describe("зоны и выбор анимаций", () => {
  it("зона по hit area и по высоте", () => {
    expect(zoneOf(0.9, ["Head"])).toBe("head");
    expect(zoneOf(0.1, [])).toBe("head");
    expect(zoneOf(0.6, [])).toBe("body");
  });
  it("выражение по имени или раскладке Haru", () => {
    expect(pickExpression("joyful", ["Normal", "Smile", "Sad"])).toBe("Smile");
    expect(pickExpression("sad", ["Normal", "Smile", "Sad"])).toBe("Sad");
    expect(pickExpression("joyful", ["F01", "F02", "F03"])).toBe("F01");
    expect(pickExpression("calm", ["F01", "F02"])).toBe("F02");
    expect(pickExpression("joyful", [])).toBeNull();
  });
  it("группа движений у разных моделей", () => {
    expect(pickMotionGroup("tap", ["Idle", "TapBody"])).toBe("TapBody");
    expect(pickMotionGroup("tap", ["Idle", "Tap", "Tap@Body"])).toBe("Tap@Body");
    expect(pickMotionGroup("flick", ["Idle", "FlickLeft"])).toBe("FlickLeft");
    expect(pickMotionGroup("tap", ["Idle"])).toBeNull();
  });
  it("параметры лица под настроение", () => {
    expect(moodParams("joyful").ParamMouthForm).toBeGreaterThan(0);
    expect(moodParams("calm")).toEqual({});
  });
});

describe("числа", () => {
  it("сглаживание стремится к цели", () => {
    let v = 0;
    for (let i = 0; i < 20; i++) v = approach(v, 1, 33, 100);
    expect(v).toBeGreaterThan(0.95);
  });
  it("взгляд ограничен [-1,1]", () => {
    expect(gazeFromCursor(5000, -5000, 0, 0)).toEqual({ x: 1, y: 1 });
    const g = gazeFromCursor(0, 0, 0, 0);
    expect(Math.abs(g.x) + Math.abs(g.y)).toBe(0);
  });
  it("громкость губ: тишина 0, сигнал > 0", () => {
    expect(mouthLevel(new Uint8Array(64).fill(128))).toBe(0);
    const loud = new Uint8Array(64).map((_, i) => (i % 2 ? 200 : 56));
    expect(mouthLevel(loud)).toBeGreaterThan(0.5);
  });
  it("масштаб в пределах", () => {
    expect(clampScale(9)).toBe(1.6);
    expect(clampScale(0.1)).toBe(0.5);
    expect(clampScale(NaN)).toBe(1);
  });
});
