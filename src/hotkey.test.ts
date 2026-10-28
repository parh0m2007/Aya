import { describe, expect, it } from "vitest";
import { accelFromEvent, accelLabel, rebind } from "./hotkey";

type Mods = Partial<Record<"ctrlKey" | "altKey" | "shiftKey" | "metaKey", boolean>>;
const k = (key: string, code: string, m: Mods = {}) =>
  accelFromEvent({ key, code, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...m });

describe("accelFromEvent", () => {
  it("разбирает сочетания", () => {
    expect(k(" ", "Space", { ctrlKey: true })).toBe("Control+Space");
    expect(k(" ", "Space", { ctrlKey: true, shiftKey: true })).toBe("Control+Shift+Space");
    expect(k("F9", "F9")).toBe("F9");
    expect(k("a", "KeyA")).toBeNull();
    expect(k("Control", "ControlLeft", { ctrlKey: true })).toBeNull(); // только модификатор
    expect(k("ф", "KeyA", { altKey: true })).toBe("Alt+A"); // по физической клавише, не по раскладке
    expect(accelLabel("Control+Shift+Space")).toBe("Ctrl+Shift+Space");
  });
});

describe("rebind", () => {
  it("занятое сочетание не ломает старое", async () => {
    const reg = new Set(["Control+Space"]);
    const gs = {
      register: async (a: string) => {
        if (a === "Alt+Tab") throw new Error("busy");
        reg.add(a);
      },
      unregister: async (a: string) => void reg.delete(a),
    };
    expect(await rebind(gs, "Control+Space", "Alt+Tab", () => {})).toEqual({ ok: false, error: "busy" });
    expect([...reg]).toEqual(["Control+Space"]);
    expect((await rebind(gs, "Control+Space", "F9", () => {})).ok).toBe(true);
    expect([...reg]).toEqual(["F9"]);
  });
});
