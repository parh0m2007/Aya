import { describe, expect, it } from "vitest";
import { clampFollowupSec, FollowUpWindow, shouldArmFollowUp, shouldArmWakeWindow, type ArmDecision } from "./followup";

describe("clampFollowupSec", () => {
  it("зажимает диапазон 2–30", () => {
    expect(clampFollowupSec(6)).toBe(6);
    expect(clampFollowupSec(1)).toBe(2);
    expect(clampFollowupSec(99)).toBe(30);
    expect(clampFollowupSec("12")).toBe(12);
    expect(clampFollowupSec("x")).toBe(6);
    expect(clampFollowupSec(Number.NaN)).toBe(6);
    expect(clampFollowupSec(6.6)).toBe(7);
  });
});

describe("FollowUpWindow", () => {
  it("взводится, считает остаток и истекает", () => {
    const w = new FollowUpWindow();
    w.arm(6, 1000);
    expect(w.active(1000)).toBe(true);
    expect(w.remaining(1000)).toBe(6);
    expect(w.active(6999)).toBe(true);
    expect(w.remaining(6999)).toBe(1);
    expect(w.active(7000)).toBe(false);
    expect(w.remaining(7000)).toBe(0);
    expect(w.expire(7000)).toBe(true);
    expect(w.active(7000)).toBe(false);
  });

  it("cancel и повторное взведение", () => {
    const w = new FollowUpWindow();
    w.arm(6, 1000);
    w.cancel();
    expect(w.active(1000)).toBe(false);
    w.arm(30, 2000);
    expect(w.remaining(2000)).toBe(30);
  });
});

const base: ArmDecision = {
  enabled: true,
  voiceTurn: true,
  mode: "button",
  panelOpen: true,
  hidden: false,
  busy: false,
  pending: false,
  reply: "ответ",
  spoke: true,
  aborted: false,
};

describe("shouldArmFollowUp", () => {
  it("все условия выполнены", () => {
    expect(shouldArmFollowUp(base)).toBe(true);
  });
  it("не взводится после текста, в auto, без панели, скрыто", () => {
    expect(shouldArmFollowUp({ ...base, voiceTurn: false })).toBe(false);
    expect(shouldArmFollowUp({ ...base, mode: "auto" })).toBe(false);
    expect(shouldArmFollowUp({ ...base, panelOpen: false })).toBe(false);
    expect(shouldArmFollowUp({ ...base, hidden: true })).toBe(false);
  });
  it("не взводится при busy/pending/пустом ответе/без озвучки/abort", () => {
    expect(shouldArmFollowUp({ ...base, busy: true })).toBe(false);
    expect(shouldArmFollowUp({ ...base, pending: true })).toBe(false);
    expect(shouldArmFollowUp({ ...base, reply: "   " })).toBe(false);
    expect(shouldArmFollowUp({ ...base, spoke: false })).toBe(false);
    expect(shouldArmFollowUp({ ...base, aborted: true })).toBe(false);
  });
  it("не взводится, если функция выключена", () => {
    expect(shouldArmFollowUp({ ...base, enabled: false })).toBe(false);
  });
});

describe("shouldArmWakeWindow", () => {
  it("«свободные руки» выключены — после ответа без имени не слушаем", () => {
    expect(shouldArmWakeWindow({ wakeOn: true, followupOn: false, mode: "button" })).toBe(false);
  });
  it("«свободные руки» и отклик на имя включены — 7 секунд можно без имени", () => {
    expect(shouldArmWakeWindow({ wakeOn: true, followupOn: true, mode: "hotkey" })).toBe(true);
  });
  it("без отклика на имя — нет окна", () => {
    expect(shouldArmWakeWindow({ wakeOn: false, followupOn: true, mode: "button" })).toBe(false);
  });
  it("режим «по голосу» и так слушает всё", () => {
    expect(shouldArmWakeWindow({ wakeOn: true, followupOn: true, mode: "auto" })).toBe(false);
  });
});
