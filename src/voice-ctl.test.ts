import { describe, expect, it } from "vitest";

import { HoldGate, hotkeyAction } from "./voice-ctl";

describe("hotkeyAction — что делать с Ctrl+Space при смене режима", () => {
  it("включает регистрацию при входе в режим hotkey", () => {
    expect(hotkeyAction("hotkey", false)).toBe("register");
  });

  it("повторный вызов в режиме hotkey НЕ снимает регистрацию (иначе клавиша залипает)", () => {
    expect(hotkeyAction("hotkey", true)).toBe("none");
  });

  it("снимает регистрацию при уходе из режима hotkey", () => {
    expect(hotkeyAction("auto", true)).toBe("unregister");
    expect(hotkeyAction("button", true)).toBe("unregister");
  });

  it("ничего не делает, если хоткей и не был зарегистрирован", () => {
    expect(hotkeyAction("button", false)).toBe("none");
    expect(hotkeyAction("auto", false)).toBe("none");
  });
});

describe("HoldGate — гонка «нажал и быстро отпустил»", () => {
  it("токен актуален, пока кнопка удерживается", () => {
    const gate = new HoldGate();
    const token = gate.begin();
    expect(gate.isCurrent(token)).toBe(true);
  });

  it("отпускание до старта записи отменяет отложенный старт", () => {
    const gate = new HoldGate();
    const token = gate.begin();
    gate.end(); // Released пришёл, пока ещё открывался микрофон
    expect(gate.isCurrent(token)).toBe(false);
  });

  it("новое удержание не отменяется отпусканием предыдущего", () => {
    const gate = new HoldGate();
    const first = gate.begin();
    gate.end();
    const second = gate.begin();
    expect(gate.isCurrent(first)).toBe(false);
    expect(gate.isCurrent(second)).toBe(true);
  });

  it("повторный begin делает старый токен неактуальным", () => {
    const gate = new HoldGate();
    const first = gate.begin();
    const second = gate.begin();
    expect(gate.isCurrent(first)).toBe(false);
    expect(gate.isCurrent(second)).toBe(true);
  });
});
