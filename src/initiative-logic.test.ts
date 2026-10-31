import { describe, expect, it } from "vitest";
import { initiativeDelivery, initiativeQuery, isRequested } from "./initiative-logic";

describe("isRequested", () => {
  it("напоминание и отчёт суб-агента человек просил сам", () => {
    expect(isRequested("reminder:выпить воды")).toBe(true);
    expect(isRequested("report:итог")).toBe(true);
  });
  it("утренний привет и письма Telegram — своя инициатива Аи", () => {
    expect(isRequested("morning")).toBe(false);
    expect(isRequested("tg:")).toBe(false);
  });
});

describe("initiativeQuery", () => {
  it("инициатива выключена — спрашиваем только напоминания", () => {
    expect(initiativeQuery(false)).toBe("?requested_only=1");
  });
  it("инициатива включена — всё", () => {
    expect(initiativeQuery(true)).toBe("");
  });
});

describe("initiativeDelivery", () => {
  it("напоминание при выключенной инициативе — говорим", () => {
    expect(initiativeDelivery("reminder:позвонить", false, false)).toBe("full");
  });
  it("напоминание, пока человек говорил, не теряется — тихо в чат", () => {
    expect(initiativeDelivery("reminder:позвонить", true, true)).toBe("quiet");
  });
  it("своя инициатива, пока человек говорил, — выбрасываем", () => {
    expect(initiativeDelivery("morning", true, true)).toBe("drop");
  });
  it("своя инициатива при выключенной настройке — выбрасываем", () => {
    expect(initiativeDelivery("idle", false, false)).toBe("drop");
  });
  it("своя инициатива в обычной ситуации — говорим", () => {
    expect(initiativeDelivery("idle", true, false)).toBe("full");
  });
});
